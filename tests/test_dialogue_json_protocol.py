import asyncio
import unittest

import httpx
from openai import APIStatusError

from umamusume_agent.server import dialogue_server as ds


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


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
        ds.config.ROLEPLAY_LLM_MODEL_BASE_URL = "https://llm.example.test/v1"
        ds.config.ROLEPLAY_LLM_MODEL_NAME = "test-model"

    def test_parse_structured_reply_accepts_code_fence(self):
        reply = ds._parse_structured_reply(
            '```json\n{"action":"光钻轻轻点头。","dialogue":"训练员，我们开始吧。"}\n```'
        )

        self.assertEqual(reply.action, "光钻轻轻点头。")
        self.assertEqual(reply.dialogue, "训练员，我们开始吧。")
        self.assertEqual(reply.source_format, "json_v2")

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


if __name__ == "__main__":
    unittest.main()
