"""Application wiring contracts: no live LLM, MCP or user history required."""
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError

from umamusume_agent.character.model import CharacterConfig, VoiceConfig
from umamusume_agent.server.app import create_app
from umamusume_agent.server.services import build_services
from umamusume_agent.tts import MCPToolError
from tests.server_support import test_settings
from tests.test_dialogue_routes import _FakeLlmClient, _FakeStreamChunk
from tests.test_llm_usage import _response


USER_A = "00000000-0000-4000-8000-000000000001"
USER_B = "00000000-0000-4000-8000-000000000002"


class ServerApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.character = CharacterConfig(
            id="uma_test", name_zh="测试角色", name_en="Test Character", name_jp="テスト",
            system_prompt="你是测试角色。", voice_config=VoiceConfig(no_voice=True),
        )

    def make_app(self, name="app", **overrides):
        settings = test_settings(self.root / name, **overrides)
        manager = SimpleNamespace(
            load_character=AsyncMock(return_value=self.character),
            list_characters=lambda: [],
            character_exists=lambda _name: False,
        )
        services = build_services(
            settings=settings, character_manager=manager, tts_client=object(),
            llm_client=_FakeLlmClient('{"action":"无","dialogue":"收到。"}'),
        )
        app = create_app(services=services)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver",
        )
        self.addAsyncCleanup(client.aclose)
        return app, services, client

    async def load_session(self, client, user_uuid=USER_A):
        response = await client.post("/load_character", json={
            "character_name": "测试角色", "user_uuid": user_uuid,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_apps_own_sessions_runtime_caches_and_shared_in_app_runtime(self):
        first, a, client = self.make_app("first")
        second, b, _ = self.make_app("second")
        self.assertIs(first.state.services, a)
        self.assertIs(second.state.services, b)
        self.assertIs(a.character_runtime, a.dialogue_service.runtime)
        self.assertIs(a.character_runtime, a.director_service.character_runtime)
        self.assertIs(a.character_runtime, a.stage_director_service.character_runtime)
        self.assertIsNot(a.director_service, a.stage_director_service)
        a.character_runtime.response_format_unsupported.add(("provider", "model"))
        self.assertEqual(b.character_runtime.response_format_unsupported, set())
        a.director_sessions["sentinel"] = object()
        self.assertEqual(a.stage_sessions, {})
        self.assertEqual(b.director_sessions, {})
        loaded = await self.load_session(client)
        self.assertIn(loaded["session_id"], a.session_store.sessions)
        self.assertEqual(b.session_store.sessions, {})

    async def test_auth_exempt_audio_and_cors_preflight_are_unchanged(self):
        _, services, client = self.make_app(API_ACCESS_KEY="secret")
        self.assertEqual((await client.get("/")).status_code, 200)
        denied = await client.get("/capabilities")
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(denied.json(), {"detail": "Invalid or missing API key."})
        allowed = await client.get("/capabilities", headers={"X-API-Key": "secret"})
        self.assertEqual(allowed.status_code, 200)
        preflight = await client.options("/chat", headers={
            "Origin": "https://username.github.io", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-api-key",
        })
        self.assertEqual(preflight.status_code, 200)
        self.assertIn("access-control-allow-origin", preflight.headers)
        self.assertEqual((await client.get("/audio", params={"path": ""})).status_code, 400)
        with patch.object(services.voice_service, "resolve_job_audio", AsyncMock(side_effect=MCPToolError("gone"))):
            response = await client.get("/tts/jobs/missing/audio", params={"user_uuid": USER_A})
        self.assertEqual(response.status_code, 404)
        self.assertEqual((await client.get("/tts/jobs/missing", params={"user_uuid": USER_A})).status_code, 401)

    async def test_rate_limit_buckets_are_per_app_and_chat_group_is_preserved(self):
        options = dict(API_RATE_LIMIT_ENABLED=True, API_RATE_LIMIT_MAX_REQUESTS=1,
                       API_CHAT_RATE_LIMIT_MAX_REQUESTS=1)
        _, _, first = self.make_app("first", **options)
        _, _, second = self.make_app("second", **options)
        self.assertEqual((await first.get("/capabilities")).status_code, 200)
        limited = await first.get("/characters")
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)
        self.assertEqual((await second.get("/capabilities")).status_code, 200)
        missing = await first.post("/chat", json={"session_id": "missing", "message": "hi"})
        self.assertEqual(missing.status_code, 404)
        self.assertEqual((await first.post("/director/turn", json={})).status_code, 429)
        self.assertEqual((await first.get("/")).status_code, 200)

    async def test_history_import_restore_clear_and_browser_isolation(self):
        _, services, client = self.make_app()
        first = await self.load_session(client)
        second = await self.load_session(client, USER_B)
        imported = await client.post("/history/import", json={
            "session_id": first["session_id"], "messages": [
                {"role": "user", "content": "晚上好"},
                {"role": "assistant", "action": "她微笑。", "dialogue": "你好。"},
            ],
        })
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual(imported.json()["imported_messages"], 2)
        history = await client.get("/history", params={"user_uuid": USER_A, "limit": 0})
        self.assertEqual(history.json()["total_messages"], 2)
        self.assertEqual(history.json()["messages"][1]["action"], "她微笑。")
        other = await client.get("/history", params={"user_uuid": USER_B, "limit": 0})
        self.assertEqual(other.json()["total_messages"], 0)
        self.assertEqual(len((await client.get("/sessions")).json()), 2)
        self.assertEqual((await client.delete(f"/session/{first['session_id']}")).status_code, 200)
        restored = await self.load_session(client)
        self.assertEqual(restored["restored_history_messages"], 2)
        restored_session = services.session_store.sessions[restored["session_id"]]
        self.assertIn("她微笑。", restored_session.get_messages()[-1]["content"])
        cleared = await client.delete("/history", params={
            "user_uuid": USER_A, "character_name": self.character.name_en,
        })
        self.assertEqual(cleared.status_code, 200, cleared.text)
        self.assertEqual(cleared.json()["cleared_active_sessions"], 1)
        self.assertEqual(restored_session.history, [])
        self.assertIn(second["session_id"], services.session_store.sessions)

    async def test_history_validation_and_empty_replace(self):
        _, services, client = self.make_app()
        loaded = await self.load_session(client)
        for messages, replace in [([], False), ([{"role": "system", "content": "bad"}], True)]:
            response = await client.post("/history/import", json={
                "session_id": loaded["session_id"], "messages": messages, "replace_current": replace,
            })
            self.assertEqual(response.status_code, 400)
        session = services.session_store.sessions[loaded["session_id"]]
        session.add_message("user", "old")
        response = await client.post("/history/import", json={
            "session_id": loaded["session_id"], "messages": [], "replace_current": True,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.history, [])
        self.assertEqual((await client.get("/history", params={"user_uuid": "bad"})).status_code, 400)
        self.assertEqual((await client.get("/history", params={"user_uuid": USER_A, "limit": -1})).status_code, 400)

    async def test_expiry_and_shutdown_lifecycle(self):
        app, services, client = self.make_app(DIALOGUE_SESSION_TTL_SECONDS=1)
        async with app.router.lifespan_context(app):
            task = app.state.session_cleanup_task
            self.assertIsInstance(task, asyncio.Task)
            await asyncio.sleep(0)
            first = await self.load_session(client)
            session = services.session_store.sessions[first["session_id"]]
            session.last_active_at = datetime.now() - timedelta(seconds=2)
            response = await client.post("/chat", json={"session_id": session.session_id, "message": "hi"})
            self.assertEqual(response.status_code, 404)
            self.assertNotIn(session.session_id, services.session_store.sessions)
            second = await self.load_session(client)
            closed_session = services.session_store.sessions[second["session_id"]]
        self.assertTrue(task.cancelled())
        records = [json.loads(line) for line in closed_session.history_file.read_text().splitlines()]
        self.assertEqual(records[-1]["reason"], "server_shutdown")

    async def test_disabled_ttl_still_closes_sessions_at_shutdown(self):
        app, services, client = self.make_app(DIALOGUE_SESSION_TTL_SECONDS=0)
        async with app.router.lifespan_context(app):
            self.assertIsNone(app.state.session_cleanup_task)
            loaded = await self.load_session(client)
            session = services.session_store.sessions[loaded["session_id"]]
            with patch.object(session, "mark_closed") as closed:
                # Shutdown below calls the real method, checked via JSONL.
                self.assertEqual(services.session_store.cleanup_expired(), 0)
                closed.assert_not_called()
        records = [json.loads(line) for line in session.history_file.read_text().splitlines()]
        self.assertEqual(records[-1]["reason"], "server_shutdown")

    async def test_usage_endpoint_only_returns_requested_browser(self):
        _, services, client = self.make_app(ROLEPLAY_LLM_MODEL_BASE_URL="https://api.deepseek.com")
        with services.usage_tracker.operation(user_uuid=USER_A, feature="dialogue_turn"):
            services.usage_tracker.record_response(
                _response(request_id="one", prompt=100, cached=64, completion=20, reasoning=4),
                finish_reason="stop",
            )
        a = await client.get("/usage/recent", params={"user_uuid": USER_A})
        b = await client.get("/usage/recent", params={"user_uuid": USER_B})
        self.assertEqual(a.json()["instance"]["request_count"], 1)
        self.assertEqual(b.json()["instance"]["request_count"], 0)
        self.assertEqual((await client.get("/usage/recent", params={"user_uuid": "bad"})).status_code, 400)

    async def test_legacy_stream_usage_stays_in_browser_operation(self):
        _, services, client = self.make_app(
            ROLEPLAY_LLM_MODEL_BASE_URL="https://api.deepseek.com",
            LLM_JSON_OUTPUT_MODE="disabled",
        )
        loaded = await self.load_session(client)

        async def chunks():
            yield _FakeStreamChunk("动作：她挥手。\n对白：你好。")
            yield _response(request_id="legacy", prompt=100, cached=64, completion=20, reasoning=4)

        completions = services.character_runtime.llm_client.chat.completions
        with patch.object(completions, "create", AsyncMock(return_value=chunks())) as create:
            response = await client.post("/chat_stream", json={
                "session_id": loaded["session_id"], "message": "hi",
            })
        self.assertIn("event: done", response.text)
        self.assertEqual(create.await_args.kwargs["stream_options"], {"include_usage": True})
        usage = services.usage_tracker.snapshot(user_uuid=USER_A)
        self.assertEqual(usage["instance"]["cached_input_tokens"], 64)
        self.assertEqual(usage["recent_operations"][0]["feature"], "dialogue_turn")

    async def test_tts_job_routes_and_private_audio_headers(self):
        _, services, client = self.make_app()
        # NamedTemporaryFile provides an isolated fixture, never user audio.
        with tempfile.NamedTemporaryFile(dir=services.voice_service.outputs_dir) as audio:
            audio_path = Path(audio.name)
            with patch.object(services.voice_service, "resolve_job_audio", AsyncMock(return_value=audio_path)) as resolve:
                response = await client.get("/tts/jobs/job/audio", params={"user_uuid": USER_A})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "private, no-store")
            self.assertEqual(response.headers["pragma"], "no-cache")
            resolve.assert_awaited_once_with(job_id="job", user_uuid=USER_A)
            for method in (client.get, client.head):
                response = await method("/audio", params={"path": str(audio_path)})
                self.assertEqual(response.status_code, 200)
            forbidden = await client.get("/audio", params={"path": __file__})
            self.assertEqual(forbidden.status_code, 403)
        for method, target in [(client.get, "get_job"), (client.delete, "cancel_job")]:
            with patch.object(services.voice_service, target, AsyncMock(return_value={"state": "ready"})) as call:
                response = await method("/tts/jobs/job", params={"user_uuid": USER_A})
                self.assertEqual(response.json(), {"state": "ready"})
                call.assert_awaited_once_with(job_id="job", user_uuid=USER_A)
                invalid = await method("/tts/jobs/job", params={"user_uuid": "bad"})
                self.assertEqual(invalid.status_code, 400)
            with patch.object(services.voice_service, target, AsyncMock(side_effect=MCPToolError("gone"))):
                response = await method("/tts/jobs/job", params={"user_uuid": USER_A})
                self.assertEqual(response.status_code, 404)

    async def test_upstream_errors_keep_http_and_sse_error_protocols(self):
        _, services, client = self.make_app()
        loaded = await self.load_session(client)
        request = httpx.Request("POST", "https://llm.example.test")
        errors = [
            (APITimeoutError(request=request), 504, "上游模型服务超时"),
            (APIConnectionError(request=request), 502, "无法连接"),
            (APIStatusError("failed", response=httpx.Response(429, request=request, json={"error": {"message": "busy"}}), body={}), 429, "busy"),
            (ValueError("bad response"), 502, "bad response"),
        ]
        for error, status, detail in errors:
            with self.subTest(status=status), patch.object(services.dialogue_service, "execute_turn", AsyncMock(side_effect=error)):
                response = await client.post("/chat", json={"session_id": loaded["session_id"], "message": "hi"})
                self.assertEqual(response.status_code, status)
                self.assertIn(detail, response.json()["detail"])
                response = await client.post("/chat_stream", json={"session_id": loaded["session_id"], "message": "hi"})
                self.assertEqual(response.status_code, 200)
                self.assertIn("event: error", response.text)
                self.assertIn(detail, response.text)
