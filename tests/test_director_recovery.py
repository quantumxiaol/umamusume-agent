"""Recovery is usable independently of online runtimes and rejects unsafe snapshots."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from umamusume_agent.dialogue.models import DialogueInputEvent
from umamusume_agent.director.history import InvalidSceneHistory
from umamusume_agent.director.models import SceneRecoverySnapshot
from umamusume_agent.director.recovery import SceneRecovery
from tests import test_director_service as fixtures


class SceneRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.service = fixtures.DirectorServiceTests._service(self.root / "original")
        self.session = await self.service.create_session(
            user_uuid="00000000-0000-4000-8000-000000000001",
            template_id="test_scene", character_names=["角色A", "角色B"],
        )
        await self.service.execute_turn(self.session, [DialogueInputEvent(content="你好")])

    def recovery(self, directory):
        return SceneRecovery(
            character_manager=self.service.character_manager,
            director_context_builder=self.service.director_context_builder,
            character_context_builder=self.service.character_context_builder,
            history_dir=directory, max_participants=3,
            narrator=self.service._narrator_actor(),
        )

    async def test_server_replay_preserves_prompt_threads_without_llm_or_history_writes(self):
        before = self.session.history_file.read_bytes()
        recovery = self.recovery(self.service.history_dir)
        with patch.object(self.service.character_runtime, "generate_reply", AsyncMock()) as character_call, \
             patch.object(self.service.director_runtime, "generate_plan", AsyncMock()) as director_call:
            restored = await recovery.restore_session(
                user_uuid=self.session.user_uuid, session_id=self.session.session_id,
            )
        character_call.assert_not_awaited()
        director_call.assert_not_awaited()
        self.assertEqual(self.session.history_file.read_bytes(), before)
        self.assertEqual(restored.director_thread.messages, self.session.director_thread.messages)
        self.assertEqual(restored.timeline.events, self.session.timeline.events)
        for actor_id, thread in restored.actor_threads.items():
            self.assertEqual(thread.messages, self.session.actor_threads[actor_id].messages)

    async def test_browser_validation_precedes_any_recovery_history_write(self):
        recovery = self.recovery(self.root / "recovered")
        original = self.session.public_snapshot()
        invalid_snapshots = []
        snapshot = copy.deepcopy(original)
        snapshot["user_uuid"] = "another-browser"
        invalid_snapshots.append(snapshot)
        snapshot = copy.deepcopy(original)
        snapshot["scene_state"]["location"] = "forged location"
        invalid_snapshots.append(snapshot)
        for field, value in [("hidden", True), ("model_content", "internal prompt"),
                             ("event_type", "director_plan")]:
            snapshot = copy.deepcopy(original)
            snapshot["events"][0][field] = value
            invalid_snapshots.append(snapshot)
        snapshot = copy.deepcopy(original)
        snapshot["events"][1]["sequence"] = snapshot["events"][0]["sequence"]
        invalid_snapshots.append(snapshot)
        snapshot = copy.deepcopy(original)
        snapshot['context_checkpoints'] = [{'summary': 'x'}] * 101
        invalid_snapshots.append(snapshot)
        snapshot = copy.deepcopy(original)
        snapshot['context_checkpoint'] = {'summary': 'x' * 2_000_001}
        invalid_snapshots.append(snapshot)
        for snapshot in invalid_snapshots:
            with self.subTest(snapshot=snapshot), self.assertRaises(InvalidSceneHistory):
                await recovery.recover_browser_snapshot(
                    user_uuid=self.session.user_uuid,
                    snapshot=SceneRecoverySnapshot.model_validate(snapshot),
                )
            self.assertEqual(list((self.root / "recovered").rglob("*.jsonl")), [])
        restored = await recovery.recover_browser_snapshot(
            user_uuid=self.session.user_uuid,
            snapshot=SceneRecoverySnapshot.model_validate(original),
        )
        self.assertEqual(restored.turn_index, self.session.turn_index)
        self.assertEqual(restored.timeline.state, self.session.timeline.state)
        self.assertEqual(
            [event.event_id for event in restored.timeline.public_events()],
            [event.event_id for event in self.session.timeline.public_events()],
        )
