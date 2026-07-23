import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi import FastAPI

from umamusume_agent.director.context import (
    CharacterSceneContextBuilder,
    DirectorContextBuilder,
)
from umamusume_agent.director.service import DirectorService
from umamusume_agent.server.director_routes import create_director_router
from tests.test_director_service import (
    _CharacterManager,
    _FakeCharacterRuntime,
    _FakeDirectorRuntime,
    _Settings,
    _TemplateRepository,
)


class DirectorRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.sessions = {}
        self.character_runtime = _FakeCharacterRuntime()
        self.director_runtime = _FakeDirectorRuntime()
        service = DirectorService(
            character_manager=_CharacterManager(),
            character_runtime=self.character_runtime,
            director_runtime=self.director_runtime,
            template_repository=_TemplateRepository(),
            director_context_builder=DirectorContextBuilder(
                settings=_Settings,
                max_speakers=2,
            ),
            character_context_builder=CharacterSceneContextBuilder(
                settings=_Settings,
            ),
            history_dir=Path(self.temp_dir.name),
            max_participants=3,
        )
        app = FastAPI()
        app.include_router(
            create_director_router(
                service=service,
                sessions=self.sessions,
                session_ttl_seconds=3600,
            )
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp_dir.cleanup()

    async def _create_session(self):
        response = await self.client.post(
            "/director/sessions",
            json={
                "template_id": "test_scene",
                "character_names": ["角色A", "角色B"],
                "user_uuid": "00000000-0000-4000-8000-000000000001",
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    async def test_create_and_execute_director_turn(self):
        created = await self._create_session()
        self.assertEqual(created["template"]["template_id"], "test_scene")
        self.assertEqual(len(created["participants"]), 3)

        response = await self.client.post(
            "/director/turn",
            json={
                "session_id": created["session_id"],
                "events": [
                    {
                        "content": "你们今天训练得怎么样？",
                        "speaker": {
                            "actor_id": "player",
                            "actor_type": "trainer",
                            "display_name": "训练员"
                        },
                        "event_type": "dialogue"
                    }
                ]
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["turn_index"], 1)
        self.assertEqual(
            [event["event_type"] for event in payload["events"]],
            ["dialogue", "character_reply", "character_reply"],
        )
        self.assertEqual(len(self.director_runtime.calls), 1)
        self.assertEqual(len(self.character_runtime.contexts), 2)

    async def test_create_custom_scene_with_multiple_characters(self):
        response = await self.client.post(
            "/director/sessions",
            json={
                "custom_scene": {
                    "name": "河边散步",
                    "initial_state": {
                        "location": "河边",
                        "time": "黄昏",
                        "weather": "微风",
                    },
                    "opening_narration": "夕阳落在河面上。",
                },
                "story_outline": "聊到下一场比赛，但不强制进度。",
                "character_names": ["角色A", "角色B"],
                "user_uuid": "00000000-0000-4000-8000-000000000001",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["scene_state"]["location"], "河边")
        self.assertEqual(payload["story_outline"], "聊到下一场比赛，但不强制进度。")
        self.assertEqual(len(payload["participants"]), 3)
        self.assertTrue(payload["template"]["template_id"].startswith("custom_"))

    async def test_stream_emits_scene_events_then_done(self):
        created = await self._create_session()
        response = await self.client.post(
            "/director/turn_stream",
            json={
                "session_id": created["session_id"],
                "events": [
                    {
                        "content": "夜幕降临。",
                        "speaker": {
                            "actor_id": "narrator",
                            "actor_type": "narrator",
                            "display_name": "环境"
                        },
                        "event_type": "scene_event"
                    }
                ]
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: scene_event", response.text)
        self.assertEqual(response.text.count("event: character_reply"), 2)
        self.assertLess(
            response.text.index("event: character_reply"),
            response.text.index("event: done"),
        )

    async def test_history_list_resume_after_memory_loss_and_delete(self):
        created = await self._create_session()
        turn_response = await self.client.post(
            "/director/turn",
            json={
                "session_id": created["session_id"],
                "events": [{"content": "训练场的灯亮了。", "event_type": "scene_event"}],
            },
        )
        self.assertEqual(turn_response.status_code, 200)

        history_response = await self.client.get(
            "/director/history",
            params={"user_uuid": created["user_uuid"]},
        )
        self.assertEqual(history_response.status_code, 200)
        self.assertEqual(len(history_response.json()["scenes"]), 1)
        self.assertEqual(
            history_response.json()["scenes"][0]["session_id"],
            created["session_id"],
        )

        self.sessions.clear()
        resume_response = await self.client.post(
            f"/director/history/{created['session_id']}/resume",
            json={"user_uuid": created["user_uuid"]},
        )
        self.assertEqual(resume_response.status_code, 200)
        self.assertEqual(resume_response.json()["turn_index"], 1)
        self.assertIn(created["session_id"], self.sessions)

        wrong_user_delete = await self.client.delete(
            f"/director/history/{created['session_id']}",
            params={"user_uuid": "00000000-0000-4000-8000-000000000099"},
        )
        self.assertEqual(wrong_user_delete.status_code, 404)
        self.assertIn(created["session_id"], self.sessions)

        delete_response = await self.client.delete(
            f"/director/history/{created['session_id']}",
            params={"user_uuid": created["user_uuid"]},
        )
        self.assertEqual(delete_response.status_code, 200)
        self.assertNotIn(created["session_id"], self.sessions)
        history_response = await self.client.get(
            "/director/history",
            params={"user_uuid": created["user_uuid"]},
        )
        self.assertEqual(history_response.json()["scenes"], [])


if __name__ == "__main__":
    unittest.main()
