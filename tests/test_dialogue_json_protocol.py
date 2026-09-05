import asyncio
import unittest

import httpx
from openai import APIStatusError

from umamusume_agent.server import dialogue_server as ds


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str, finish_reason: str = "stop"):
        self.message = _FakeMessage(content)
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, content: str, finish_reason: str = "stop"):
        self.choices = [_FakeChoice(content, finish_reason)]


class _FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _FakeLlmClient:
    def __init__(self, completions: _FakeCompletions):
        self.chat = type("_Chat", (), {"completions": completions})()


def _api_status_error(status_code: int, payload: dict) -> APIStatusError:
    request = httpx.Request("POST", "https://llm.example.test/v1/chat/completions")
    response = httpx.Response(status_code, json=payload, request=request)
    return APIStatusError("upstream error", response=response, body=payload)


class DialogueJsonProtocolTests(unittest.TestCase):
    def setUp(self):
        self._original_llm_client = ds.llm_client
        self._config_names = [
            "LLM_JSON_ENABLED",
            "LLM_JSON_OUTPUT_MODE",
            "LLM_JSON_RETRY_WITHOUT_RESPONSE_FORMAT_ON_ERROR",
            "LLM_JSON_MAX_RETRIES",
            "LLM_JSON_REGENERATE_ON_PARSE_FAILURE",
            "LLM_JSON_MAX_REGENERATE_ATTEMPTS",
            "LLM_JSON_MAX_TOKENS",
            "LLM_JSON_LENGTH_RETRY_ATTEMPTS",
            "LLM_JSON_MAX_DYNAMIC_TOKENS",
            "ROLEPLAY_LLM_MODEL_BASE_URL",
            "ROLEPLAY_LLM_MODEL_NAME",
        ]
        self._original_config = {name: getattr(ds.config, name) for name in self._config_names}
        ds._response_format_unsupported.clear()

    def tearDown(self):
        ds.llm_client = self._original_llm_client
        for name, value in self._original_config.items():
            setattr(ds.config, name, value)
        ds._response_format_unsupported.clear()

    def _configure_json_auto(self):
        ds.config.LLM_JSON_ENABLED = True
        ds.config.LLM_JSON_OUTPUT_MODE = "auto"
        ds.config.LLM_JSON_RETRY_WITHOUT_RESPONSE_FORMAT_ON_ERROR = True
        ds.config.LLM_JSON_MAX_RETRIES = 1
        ds.config.LLM_JSON_REGENERATE_ON_PARSE_FAILURE = True
        ds.config.LLM_JSON_MAX_REGENERATE_ATTEMPTS = 1
        ds.config.LLM_JSON_MAX_TOKENS = 64
        ds.config.LLM_JSON_LENGTH_RETRY_ATTEMPTS = 2
        ds.config.LLM_JSON_MAX_DYNAMIC_TOKENS = 512
        ds.config.ROLEPLAY_LLM_MODEL_BASE_URL = "https://llm.example.test/v1"
        ds.config.ROLEPLAY_LLM_MODEL_NAME = "test-model"

    def test_parse_structured_reply_accepts_code_fence(self):
        reply = ds._parse_structured_reply(
            '```json\n{"action":"光钻轻轻点头。","dialogue":"训练员，我们开始吧。"}\n```'
        )

        self.assertEqual(reply.action, "光钻轻轻点头。")
        self.assertEqual(reply.dialogue, "训练员，我们开始吧。")
        self.assertEqual(reply.source_format, "json_v2")
        self.assertEqual(reply.model_content, "")

    def test_raw_json_is_preserved_only_when_semantics_are_unchanged(self):
        raw = '{\n  "dialogue": "你好。", "action": "无"\n}'
        reply = ds._parse_structured_reply(raw)
        self.assertEqual(reply.model_content, raw)
        self.assertNotIn("model_content", reply.model_dump())
        amended = ds._parse_structured_reply('{"action":null,"dialogue":"你好。"}')
        self.assertEqual(amended.model_content, "")
        context = ds._to_compact_context_message({
            "role": "assistant", "action": "无", "dialogue": "已修改。",
            "model_content": raw,
        })
        self.assertIn("已修改。", context["content"])
        self.assertNotIn("你好。", context["content"])

    def test_normalize_import_messages_accepts_v2_and_legacy(self):
        messages = ds._normalize_import_messages(
            [
                ds.HistoryImportMessage(role="user", content="今天训练什么？"),
                ds.HistoryImportMessage(
                    role="assistant",
                    content="",
                    action="光钻整理计划表。",
                    dialogue="今天从耐力训练开始吧。",
                    source_format="json_v2",
                ),
                ds.HistoryImportMessage(
                    role="assistant",
                    content="动作：光钻微笑。\n对白：我们慢慢来。",
                ),
            ]
        )

        self.assertEqual(
            messages[0],
            {
                "role": "user",
                "content": "今天训练什么？",
                "timestamp": None,
                "schema_version": None,
            },
        )
        self.assertEqual(messages[1]["content"], "今天从耐力训练开始吧。")
        self.assertEqual(messages[1]["action"], "光钻整理计划表。")
        self.assertEqual(messages[1]["source_format"], "json_v2")
        self.assertEqual(messages[2]["content"], "我们慢慢来。")
        self.assertEqual(messages[2]["action"], "光钻微笑。")
        self.assertEqual(
            ds._to_compact_context_message(messages[1]),
            {
                "role": "assistant",
                "content": "角色动作：光钻整理计划表。\n角色对白：今天从耐力训练开始吧。",
            },
        )

    def test_response_format_auto_fallback_marks_provider(self):
        self._configure_json_auto()

        error = _api_status_error(
            400,
            {"error": {"message": "unknown parameter response_format json_object not supported"}},
        )
        completions = _FakeCompletions([error, _FakeResponse('{"action":"无","dialogue":"收到。"}')])
        ds.llm_client = _FakeLlmClient(completions)

        text = asyncio.run(
            ds._create_json_completion(
                [{"role": "user", "content": "hi"}],
                temperature=0.1,
                max_tokens=64,
            )
        )

        self.assertEqual(text, '{"action":"无","dialogue":"收到。"}')
        self.assertIn("response_format", completions.calls[0])
        self.assertNotIn("response_format", completions.calls[1])
        self.assertIn(("https://llm.example.test/v1", "test-model"), ds._response_format_unsupported)

    def test_response_format_auto_does_not_swallow_unrelated_400(self):
        self._configure_json_auto()

        error = _api_status_error(400, {"error": {"message": "invalid api key"}})
        completions = _FakeCompletions([error])
        ds.llm_client = _FakeLlmClient(completions)

        with self.assertRaises(APIStatusError):
            asyncio.run(
                ds._create_json_completion(
                    [{"role": "user", "content": "hi"}],
                    temperature=0.1,
                    max_tokens=64,
                )
            )

        self.assertEqual(len(completions.calls), 1)
        self.assertIn("response_format", completions.calls[0])

    def test_empty_reply_retries_identical_messages_and_parameters(self):
        self._configure_json_auto()
        messages = [{"role": "system", "content": "JSON"}, {"role": "user", "content": "你好"}]
        completions = _FakeCompletions([
            _FakeResponse(" \n"),
            _FakeResponse('{"action":"无","dialogue":"你好。"}'),
        ])
        ds.llm_client = _FakeLlmClient(completions)
        with self.assertLogs("umamusume_agent.llm_diagnostics", level="INFO") as logs:
            reply = asyncio.run(ds._complete_structured_reply(messages))
        self.assertEqual(reply.dialogue, "你好。")
        self.assertEqual(completions.calls[0], completions.calls[1])
        self.assertEqual(completions.calls[1]["messages"], messages)
        self.assertIn('"retry_reason":"empty_output"', "\n".join(logs.output))
        self.assertIn('"content_whitespace_only":true', "\n".join(logs.output))

    def test_repeated_empty_outputs_use_existing_bounded_retry_budget(self):
        self._configure_json_auto()
        messages = [{"role": "user", "content": "你好"}]
        completions = _FakeCompletions([_FakeResponse("") for _ in range(3)])
        ds.llm_client = _FakeLlmClient(completions)
        reply = asyncio.run(ds._complete_structured_reply(messages))
        self.assertEqual(reply.source_format, "parse_error")
        self.assertEqual(len(completions.calls), 3)
        self.assertTrue(all(call == completions.calls[0] for call in completions.calls))

    def test_length_retry_discards_partial_output_and_doubles_budget(self):
        self._configure_json_auto()
        original_messages = [{"role": "user", "content": "hi"}]
        completions = _FakeCompletions(
            [
                _FakeResponse('{"action":"半截', finish_reason="length"),
                _FakeResponse(
                    '{"action":"无","dialogue":"收到。"}',
                    finish_reason="stop",
                ),
            ]
        )
        ds.llm_client = _FakeLlmClient(completions)

        text = asyncio.run(
            ds._create_json_completion(
                original_messages,
                temperature=0.1,
                max_tokens=64,
            )
        )

        self.assertEqual(text, '{"action":"无","dialogue":"收到。"}')
        self.assertEqual(
            [call["max_tokens"] for call in completions.calls],
            [64, 128],
        )
        self.assertEqual(
            [call["messages"] for call in completions.calls],
            [original_messages, original_messages],
        )
        self.assertNotIn("半截", str(completions.calls[1]["messages"]))

    def test_exhausted_length_retry_never_enters_json_repair(self):
        self._configure_json_auto()
        ds.config.LLM_JSON_LENGTH_RETRY_ATTEMPTS = 1
        ds.config.LLM_JSON_MAX_DYNAMIC_TOKENS = 128
        original_messages = [
            {"role": "system", "content": "只输出 JSON"},
            {"role": "user", "content": "请回应"},
        ]
        completions = _FakeCompletions(
            [
                _FakeResponse('{"action":"第一次半截', finish_reason="length"),
                _FakeResponse('{"action":"第二次半截', finish_reason="length"),
            ]
        )
        ds.llm_client = _FakeLlmClient(completions)

        reply = asyncio.run(ds._complete_structured_reply(original_messages))

        self.assertEqual(reply.source_format, "parse_error")
        self.assertEqual(len(completions.calls), 2)
        self.assertEqual(
            [call["max_tokens"] for call in completions.calls],
            [64, 128],
        )
        self.assertEqual(
            [call["messages"] for call in completions.calls],
            [original_messages, original_messages],
        )
        self.assertNotIn("修复", str(completions.calls))

    def test_complete_structured_reply_regenerates_before_safe_fallback(self):
        self._configure_json_auto()
        completions = _FakeCompletions(
            [
                _FakeResponse("not json"),
                _FakeResponse('{"action":"无"}'),
                _FakeResponse('{"action":"米浴抬起头。","dialogue":"训练员，我听见了。"}'),
            ]
        )
        ds.llm_client = _FakeLlmClient(completions)

        reply = asyncio.run(
            ds._complete_structured_reply(
                [
                    {"role": "system", "content": "只输出 JSON"},
                    {"role": "user", "content": "怎么回事"},
                ]
            )
        )

        self.assertEqual(reply.action, "米浴抬起头。")
        self.assertEqual(reply.dialogue, "训练员，我听见了。")
        self.assertEqual(reply.source_format, "json_v2_regenerated")
        self.assertEqual(len(completions.calls), 3)
        self.assertIn("response_format", completions.calls[0])
        self.assertNotIn("response_format", completions.calls[1])
        self.assertNotIn("response_format", completions.calls[2])
        self.assertEqual(
            completions.calls[1]["messages"][-2],
            {"role": "assistant", "content": "not json"},
        )

    def test_safe_fallback_is_character_neutral(self):
        self._configure_json_auto()
        completions = _FakeCompletions(
            [
                _FakeResponse("not json"),
                _FakeResponse('{"action":"无"}'),
                _FakeResponse('{"dialogue":""}'),
            ]
        )
        ds.llm_client = _FakeLlmClient(completions)

        reply = asyncio.run(
            ds._complete_structured_reply(
                [
                    {
                        "role": "system",
                        "content": "你是米浴，只输出 JSON",
                    },
                    {"role": "user", "content": "请回应"},
                ]
            )
        )

        self.assertEqual(reply.source_format, "parse_error")
        self.assertEqual(
            reply.dialogue,
            "抱歉，刚才有点没听清，可以再说一次吗？",
        )
        self.assertNotIn("光钻", reply.dialogue)
        self.assertNotIn("米浴", reply.dialogue)


if __name__ == "__main__":
    unittest.main()
