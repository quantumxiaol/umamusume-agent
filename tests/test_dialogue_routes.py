import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

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


class _FakeDelta:
    def __init__(self, content: str):
        self.content = content


class _FakeStreamChoice:
    def __init__(self, content: str):
        self.delta = _FakeDelta(content)


class _FakeStreamChunk:
    def __init__(self, content: str):
        self.choices = [_FakeStreamChoice(content)]


class _FakeStream:
    def __init__(self, content: str):
        self._chunks = iter([_FakeStreamChunk(content)])

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeCompletions:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return _FakeStream(self.content)
        return _FakeResponse(self.content)


class _FakeLlmClient:
    def __init__(self, content: str):
        self.completions = _FakeCompletions(content)
        self.chat = type("_Chat", (), {"completions": self.completions})()


class _FakeCharacter:
    name_zh = "测试角色"
    name_en = "test_character"
    name_jp = "テスト"

    @staticmethod
    def get_system_prompt():
        return "你是测试角色。"

    @staticmethod
    def get_voice_config():
        return {
            "no_voice": False,
            "ref_audio_path": "/tmp/reference.wav",
        }


class _FakeSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.user_uuid = "00000000-0000-4000-8000-000000000001"
        self.character = _FakeCharacter()
        self.created_at = datetime.now()
        self.last_active_at = self.created_at
        self.history = []
        self.message_count = 0
        self.voice_index = 0
        self.output_dir = Path("/tmp/dialogue-route-test")
        self.history_file = self.output_dir / "dialogue.jsonl"
        self.added_messages = []

    def touch(self):
        self.last_active_at = datetime.now()

    def mark_closed(self, _reason: str):
        return None

    def get_messages(self, text_only: bool = False):
        context = ds.legacy_context_builder.build(
            character=self.character,
            history=self.history,
            text_only=text_only,
        )
        return list(context.messages)

    def add_message(self, role: str, content: str, **metadata):
        self.added_messages.append((role, content, metadata))
        if role == "assistant":
            record = {
                "role": role,
                "content": content,
                **metadata,
            }
            self.history.append(ds._to_compact_context_message(record))
        else:
            self.history.append({"role": role, "content": content})
        self.message_count += 1
        self.touch()


class DialogueRouteCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._original_llm_client = ds.llm_client
        self._original_api_access_key = ds.API_ACCESS_KEY
        self._original_rate_limit_enabled = ds.API_RATE_LIMIT_ENABLED
        self._original_enable_tts = ds.ENABLE_TTS
        self._original_json_enabled = ds.config.LLM_JSON_ENABLED
        self._original_json_output_mode = ds.config.LLM_JSON_OUTPUT_MODE
        self._original_sessions = dict(ds.sessions)

        ds.API_ACCESS_KEY = ""
        ds.API_RATE_LIMIT_ENABLED = False
        ds.ENABLE_TTS = False
        ds.config.LLM_JSON_ENABLED = True
        ds.config.LLM_JSON_OUTPUT_MODE = "auto"
        ds.sessions.clear()
        ds._rate_limit_buckets.clear()

        self.transport = httpx.ASGITransport(app=ds.app)
        self.client = httpx.AsyncClient(
            transport=self.transport,
            base_url="http://testserver",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        ds.llm_client = self._original_llm_client
        ds.API_ACCESS_KEY = self._original_api_access_key
        ds.API_RATE_LIMIT_ENABLED = self._original_rate_limit_enabled
        ds.ENABLE_TTS = self._original_enable_tts
        ds.config.LLM_JSON_ENABLED = self._original_json_enabled
        ds.config.LLM_JSON_OUTPUT_MODE = self._original_json_output_mode
        ds.sessions.clear()
        ds.sessions.update(self._original_sessions)
        ds._rate_limit_buckets.clear()
        ds._sync_character_runtime_client()

    def _prepare_session(self, session_id: str = "route-test") -> _FakeSession:
        session = _FakeSession(session_id)
        ds.sessions[session_id] = session
        ds.llm_client = _FakeLlmClient(
            '{"action":"测试角色轻轻点头。","dialogue":"收到。"}'
        )
        return session

    async def test_legacy_chat_payload_and_response_remain_compatible(self):
        session = self._prepare_session()

        response = await self.client.post(
            "/chat",
            json={"session_id": session.session_id, "message": "你好"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["action"], "测试角色轻轻点头。")
        self.assertEqual(payload["dialogue"], "收到。")
        self.assertEqual(
            payload["message"],
            {
                "schema_version": 2,
                "role": "assistant",
                "content": "收到。",
                "action": "测试角色轻轻点头。",
                "dialogue": "收到。",
                "source_format": "json_v2",
            },
        )
        self.assertEqual(
            [item[0] for item in session.added_messages],
            ["user", "assistant"],
        )

    async def test_json_stream_event_order_remains_compatible(self):
        session = self._prepare_session("stream-test")

        response = await self.client.post(
            "/chat_stream",
            json={"session_id": session.session_id, "message": "你好"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.text
        structured_index = body.index("event: structured_reply")
        done_index = body.index("event: done")
        self.assertLess(structured_index, done_index)
        self.assertIn('"dialogue": "收到。"', body)
        self.assertEqual(
            [item[0] for item in session.added_messages],
            ["user", "assistant"],
        )

    async def test_legacy_stream_keeps_token_and_done_protocol(self):
        session = self._prepare_session("legacy-stream-test")
        ds.config.LLM_JSON_OUTPUT_MODE = "disabled"
        ds.llm_client = _FakeLlmClient(
            "动作：测试角色挥了挥手。\n对白：晚上好。"
        )

        response = await self.client.post(
            "/chat_stream",
            json={"session_id": session.session_id, "message": "晚上好"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.text
        self.assertIn("data: 动作：测试角色挥了挥手。", body)
        self.assertIn("event: done", body)
        self.assertNotIn("event: structured_reply", body)
        self.assertEqual(
            [item[0] for item in session.added_messages],
            ["user", "assistant"],
        )

    async def test_chat_tts_receives_dialogue_without_action(self):
        session = self._prepare_session("tts-test")
        ds.ENABLE_TTS = True
        voice_plan = {
            "audio_path": "/tmp/reply.wav",
            "audio_url": "/audio?path=/tmp/reply.wav",
            "output_name": "reply.wav",
        }
        generate_voice = AsyncMock(
            return_value={"audio_url": voice_plan["audio_url"]}
        )

        with (
            patch.object(ds, "_reserve_voice_output", return_value=voice_plan),
            patch.object(ds, "_generate_voice_for_reply", generate_voice),
        ):
            response = await self.client.post(
                "/chat",
                json={
                    "session_id": session.session_id,
                    "message": "你好",
                    "generate_voice": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("voice", response.json())
        self.assertEqual(generate_voice.await_count, 1)
        self.assertEqual(generate_voice.await_args.args[1], "收到。")


if __name__ == "__main__":
    unittest.main()
