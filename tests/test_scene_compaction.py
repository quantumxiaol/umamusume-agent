"""Offline scene compaction, persistence, browser recovery and turn contracts."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from tests.server_support import test_settings
from tests import test_director_service as fixtures
from umamusume_agent.dialogue.compaction_runtime import CompactionError
from umamusume_agent.dialogue.compaction_runtime import CompactionRuntime
from umamusume_agent.dialogue.runtime import CharacterRuntime
from umamusume_agent.dialogue.models import DialogueInputEvent
from umamusume_agent.director.context import CharacterSceneContextBuilder, DirectorContextBuilder
from umamusume_agent.director.memory import source_digest, threads
from umamusume_agent.director.models import SceneRecoverySnapshot
from umamusume_agent.director.models import DirectorPlan, DirectorSpeakerPlan, SceneStatePatch
from umamusume_agent.director.service import DirectorService
from umamusume_agent.server.director_routes import create_director_router
from umamusume_agent.stage.context import StageDirectorContextBuilder
from tests.test_dialogue_compaction import Llm


class SceneCompactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = test_settings(self.root,
            DIRECTOR_COMPACTION_ENABLED=False, DIRECTOR_CONTEXT_MAX_TOKENS=100000,
            DIRECTOR_CONTEXT_RESERVE_TOKENS=5000, LLM_JSON_MAX_DYNAMIC_TOKENS=2048,
            DIRECTOR_COMPACTION_TRIGGER_TOKENS=12000, DIRECTOR_COMPACTION_TARGET_TOKENS=7000,
            DIRECTOR_COMPACTION_MEMORY_TOKENS=1000, DIRECTOR_COMPACTION_KEEP_TURNS=2,
            DIRECTOR_COMPACTION_RECENT_TOKENS=4000, DIRECTOR_COMPACTION_CHUNK_TOKENS=6000,
            DIRECTOR_COMPACTION_MAX_TOKENS=1024, DIRECTOR_COMPACTION_MAX_DYNAMIC_TOKENS=2048,
            DIRECTOR_COMPACTION_MAX_CHUNKS=16, DIRECTOR_ROLE_REINJECTION_INTERVAL_REPLIES=3,
        )
        self.service = self.make_service(self.root / 'original')
        self.session = await self.service.create_session(
            user_uuid='00000000-0000-4000-8000-000000000001',
            template_id='test_scene', character_names=['角色A', '角色B'],
        )
        self.summarize = AsyncMock(return_value='已确认：角色A和角色B约好明天与训练员再到跑道训练；两人听到了公开约定。')
        self.service.compactor.runtime.summarize = self.summarize
        await self.fill()
        self.settings.DIRECTOR_COMPACTION_ENABLED = True
        self.pending = [DialogueInputEvent(content='继续明天的约定。')]

    def make_service(self, directory, *, stage=False):
        builder = StageDirectorContextBuilder if stage else DirectorContextBuilder
        return DirectorService(
            character_manager=fixtures._CharacterManager(), character_runtime=fixtures._FakeCharacterRuntime(),
            director_runtime=fixtures._FakeDirectorRuntime(), template_repository=fixtures._TemplateRepository(),
            director_context_builder=builder(settings=self.settings, max_speakers=2),
            character_context_builder=CharacterSceneContextBuilder(settings=self.settings),
            history_dir=directory, max_participants=3,
        )

    async def fill(self, count=12):
        enabled = self.settings.DIRECTOR_COMPACTION_ENABLED
        self.settings.DIRECTOR_COMPACTION_ENABLED = False
        try:
            for index in range(count):
                await self.service.execute_turn(self.session, [DialogueInputEvent(
                    content=f'第{self.session.turn_index + 1}轮公开事件：' + '训练场的约定与经历。' * 60,
                )])
        finally:
            self.settings.DIRECTOR_COMPACTION_ENABLED = enabled

    async def compact(self):
        async with self.session.lock:
            await self.service.compactor.prepare(self.session, self.pending)
        self.assertIsNotNone(self.session.checkpoint)

    async def test_shared_summary_preserves_archive_recent_turns_and_stable_prefixes(self):
        before = copy.deepcopy(self.session.timeline.events)
        old_systems = {name: thread.messages[0] for name, thread in threads(self.session).items()}
        await self.compact()
        cp = self.session.checkpoint
        self.assertEqual(self.session.timeline.events, before)
        self.assertEqual(cp.trigger_event_id, self.session.timeline.public_events()[-1].event_id)
        self.assertEqual(cp.trigger_turn_index, 12)
        public = self.session.timeline.public_events()
        self.assertEqual(public[cp.covered_events].turn_index, 11)
        for name, thread in threads(self.session).items():
            self.assertEqual(thread.messages[0], old_systems[name])
            self.assertIn(cp.summary, thread.messages[1]['content'])
            self.assertNotIn('第1轮公开事件', json.dumps(thread.messages, ensure_ascii=False))
            self.assertIn('第11轮公开事件', thread.messages[2]['content'])
        sources = '\n'.join(call.kwargs['messages'][-1]['content'] for call in self.summarize.call_args_list)
        self.assertNotIn('先回应训练员', sources)  # Hidden intent is not a public fact.
        self.assertNotIn('完整角色提示词', sources)
        calls = self.summarize.await_count
        prefixes = {name: thread.snapshot() for name, thread in threads(self.session).items()}
        await self.service.execute_turn(self.session, self.pending)
        self.assertEqual(self.summarize.await_count, calls)
        for name, thread in threads(self.session).items():
            self.assertEqual(thread.messages[:len(prefixes[name])], prefixes[name])

    async def test_server_restart_rebuilds_exact_post_compaction_threads(self):
        await self.compact()
        await self.service.execute_turn(self.session, self.pending)
        before = self.session.history_file.read_bytes()
        restored = await self.service.restore_session(user_uuid=self.session.user_uuid, session_id=self.session.session_id)
        self.assertEqual(restored.checkpoint, self.session.checkpoint)
        self.assertEqual(restored.timeline.events, self.session.timeline.events)
        self.assertEqual(restored.timeline.state, self.session.timeline.state)
        for name, thread in threads(restored).items():
            self.assertEqual(thread.messages, threads(self.session)[name].messages)
            self.assertEqual(thread.reply_count, threads(self.session)[name].reply_count)
        self.assertEqual(self.session.history_file.read_bytes(), before)

    async def test_browser_memory_and_markers_survive_hf_loss_and_another_restart(self):
        await self.compact()
        await self.service.execute_turn(self.session, self.pending)
        fresh = self.make_service(self.root / 'fresh')
        snapshot = SceneRecoverySnapshot.model_validate(self.session.public_snapshot())
        recovered = await fresh.recover_browser_snapshot(user_uuid=self.session.user_uuid, snapshot=snapshot)
        self.assertEqual(recovered.checkpoint, self.session.checkpoint)
        self.assertEqual(recovered.checkpoints, self.session.checkpoints)
        self.assertEqual(recovered.timeline.state, self.session.timeline.state)
        self.assertEqual([e.event_id for e in recovered.timeline.public_events()], [e.event_id for e in self.session.timeline.public_events()])
        for thread in threads(recovered).values():
            self.assertIn(self.session.checkpoint.summary, thread.messages[1]['content'])
            self.assertNotIn('第1轮公开事件', json.dumps(thread.messages, ensure_ascii=False))
        again = await fresh.restore_session(user_uuid=recovered.user_uuid, session_id=recovered.session_id)
        self.assertEqual(again.checkpoint, recovered.checkpoint)
        for name, thread in threads(again).items():
            self.assertEqual(thread.messages, threads(recovered)[name].messages)
        self.assertEqual(fresh.character_runtime.contexts, [])

    async def test_second_compaction_uses_prior_memory_not_original_covered_source(self):
        await self.compact()
        first = self.session.checkpoint
        self.summarize.reset_mock()
        await self.fill()
        await self.compact()
        self.assertEqual(self.session.checkpoint.revision, 2)
        self.assertGreater(self.session.checkpoint.covered_events, first.covered_events)
        self.assertEqual(len(self.session.checkpoints), 2)
        sources = '\n'.join(call.kwargs['messages'][-1]['content'] for call in self.summarize.call_args_list)
        self.assertIn('已有共享记忆', sources)
        self.assertIn(first.summary, sources)
        self.assertNotIn('第1轮公开事件', sources)
        fresh = self.make_service(self.root / 'fresh')
        recovered = await fresh.recover_browser_snapshot(
            user_uuid=self.session.user_uuid, snapshot=SceneRecoverySnapshot.model_validate(self.session.public_snapshot()),
        )
        self.assertEqual(recovered.checkpoints, self.session.checkpoints)

    async def test_invalid_memory_does_not_destroy_old_browser_archive(self):
        await self.compact()
        original = self.session.public_snapshot()
        cases = []
        for field, value in [('source_digest', '0' * 64), ('prompt_digest', '0' * 64), ('user_uuid', 'another-browser')]:
            snapshot = copy.deepcopy(original)
            snapshot['context_checkpoint'][field] = value
            snapshot['context_checkpoints'] = []
            cases.append(snapshot)
        snapshot = copy.deepcopy(original)
        snapshot['events'][1]['content'] = '修改已被覆盖的原文'
        cases.append(snapshot)
        snapshot = copy.deepcopy(original)
        # A valid audit copy must not override a damaged active checkpoint.
        snapshot['context_checkpoint']['source_digest'] = '0' * 64
        cases.append(snapshot)
        snapshot = copy.deepcopy(original)
        cp = snapshot['context_checkpoint']
        cp['covered_events'] -= 1
        cp['covered_event_id'] = snapshot['events'][cp['covered_events'] - 1]['event_id']
        cp['source_digest'] = source_digest(self.session.timeline.public_events()[:cp['covered_events']])
        cases.append(snapshot)  # Correct digest, but cuts through a turn.
        for index, payload in enumerate(cases):
            with self.subTest(index=index):
                fresh = self.make_service(self.root / f'invalid-{index}')
                recovered = await fresh.recover_browser_snapshot(user_uuid=self.session.user_uuid, snapshot=SceneRecoverySnapshot.model_validate(payload))
                self.assertIsNone(recovered.checkpoint)
                self.assertEqual(len(recovered.timeline.public_events()), len(payload['events']))

    async def test_failed_second_compaction_preserves_previous_memory_and_threads(self):
        await self.compact()
        await self.fill()
        checkpoint = self.session.checkpoint.model_copy(deep=True)
        before = self.session.history_file.read_bytes()
        messages = {name: thread.snapshot() for name, thread in threads(self.session).items()}
        for output in (CompactionError('模型未完成'), '超长摘要' * 3000):
            self.summarize.side_effect = output if isinstance(output, Exception) else None
            self.summarize.return_value = output
            with self.assertRaises(CompactionError):
                await self.service.execute_turn(self.session, self.pending)
            self.assertEqual(self.session.checkpoint, checkpoint)
            self.assertEqual(self.session.history_file.read_bytes(), before)
            self.assertEqual({name: thread.snapshot() for name, thread in threads(self.session).items()}, messages)

    async def test_current_state_remains_authoritative_and_disabled_compaction_can_restore_memory(self):
        self.service.director_runtime.generate_plan = AsyncMock(return_value=DirectorPlan(
            scene_patch=SceneStatePatch(location='河边', time='夜晚'),
            speakers=[DirectorSpeakerPlan(actor_id='uma_a', target_actor_ids=['player'], intent='回应')],
        ))
        await self.fill(count=1)
        await self.compact()
        for thread in threads(self.session).values():
            state = json.loads(thread.messages[2]['content'])['current_scene_state']
            self.assertEqual((state['location'], state['time']), ('河边', '夜晚'))
        self.settings.DIRECTOR_COMPACTION_ENABLED = False
        restored = await self.service.restore_session(user_uuid=self.session.user_uuid, session_id=self.session.session_id)
        self.assertEqual(restored.checkpoint, self.session.checkpoint)
        self.assertEqual(restored.timeline.state, self.session.timeline.state)
        for name, thread in threads(restored).items():
            self.assertEqual(thread.messages, threads(self.session)[name].messages)

    async def test_failure_and_oversized_input_leave_history_and_turn_untouched(self):
        original = self.session.history_file.read_bytes()
        turns = self.session.turn_index
        for error in [CompactionError('empty model output'), TimeoutError('timeout'), OSError('disk full')]:
            self.summarize.side_effect = error
            with self.subTest(error=error), self.assertRaises(type(error)):
                await self.service.execute_turn(self.session, self.pending)
            self.assertEqual(self.session.history_file.read_bytes(), original)
            self.assertIsNone(self.session.checkpoint)
            self.assertEqual(self.session.turn_index, turns)
        self.summarize.side_effect = None
        self.summarize.reset_mock()
        with self.assertRaises(CompactionError):
            await self.service.execute_turn(self.session, [DialogueInputEvent(content='超大输入' * 50000)])
        self.summarize.assert_not_awaited()
        self.assertEqual(self.session.history_file.read_bytes(), original)
        with patch.object(self.session.history, 'append', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                await self.service.execute_turn(self.session, self.pending)
        self.assertIsNone(self.session.checkpoint)
        self.assertEqual(self.session.turn_index, turns)

    async def test_cancellation_unlocks_session_without_partial_install(self):
        entered = asyncio.Event()
        async def blocked(**_):
            entered.set()
            await asyncio.Event().wait()
        self.summarize.side_effect = blocked
        before = self.session.history_file.read_bytes()
        task = asyncio.create_task(self.service.execute_turn(self.session, self.pending))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.session.lock.locked())
        self.assertEqual(self.session.history_file.read_bytes(), before)
        self.assertIsNone(self.session.checkpoint)

    async def test_regeneration_after_checkpoint_only_commit_keeps_summary_valid(self):
        await self.compact()
        cp = self.session.checkpoint
        original = self.session.timeline.public_events()[-1]
        replacement = await self.service.regenerate_reply(self.session, event_id=original.event_id)
        self.assertEqual(replacement.revision, original.revision + 1)
        self.assertEqual(self.session.checkpoint, cp)
        self.assertNotIn(original.dialogue, json.dumps(self.service.character_runtime.contexts[-1].messages, ensure_ascii=False))
        self.assertEqual(cp.source_digest, source_digest(self.session.timeline.public_events()[:cp.covered_events]))
        for thread in threads(self.session).values():
            self.assertNotIn(original.dialogue, json.dumps(thread.messages, ensure_ascii=False))
            self.assertIn(replacement.dialogue, json.dumps(thread.messages, ensure_ascii=False))
        restored = await self.service.restore_session(user_uuid=self.session.user_uuid, session_id=self.session.session_id)
        self.assertEqual(restored.checkpoint, cp)
        for name, thread in threads(restored).items():
            self.assertEqual(thread.messages, threads(self.session)[name].messages)

    async def test_private_events_are_not_leaked_into_shared_memory(self):
        self.session.timeline.events[1].visible_to = ['uma_a']
        with self.assertRaises(CompactionError):
            await self.service.execute_turn(self.session, self.pending)
        self.summarize.assert_not_awaited()

    async def test_stream_progress_precedes_input_and_nonstream_includes_memory(self):
        app = FastAPI()
        app.include_router(create_director_router(service=self.service, sessions={self.session.session_id: self.session}, session_ttl_seconds=0))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            payload = {'session_id': self.session.session_id, 'user_uuid': self.session.user_uuid, 'events': [{'content': '继续'}]}
            response = await client.post('/director/turn_stream', json=payload)
            self.assertEqual(response.status_code, 200)
            self.assertLess(response.text.index('event: context_status'), response.text.index('event: scene_event'))
            self.assertIn('"phase": "compacted"', response.text)
            self.assertIn('event: done', response.text)
            response = await client.post('/director/turn', json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['context_checkpoint']['checkpoint_id'], self.session.checkpoint.checkpoint_id)

    async def test_old_browser_snapshot_waits_until_send_before_compacting(self):
        fresh = self.make_service(self.root / 'old-browser')
        snapshot = self.session.public_snapshot()
        snapshot.pop('context_checkpoint')
        snapshot.pop('context_checkpoints')
        recovered = await fresh.recover_browser_snapshot(
            user_uuid=self.session.user_uuid, snapshot=SceneRecoverySnapshot.model_validate(snapshot),
        )
        fresh.compactor.runtime.summarize = self.summarize
        self.summarize.assert_not_awaited()
        self.assertIsNone(recovered.checkpoint)
        await fresh.execute_turn(recovered, self.pending)
        self.assertIsNotNone(recovered.checkpoint)
        self.assertEqual(recovered.checkpoint.trigger_event_count, len(snapshot['events']))

    async def test_silent_actor_unread_history_also_triggers_shared_compaction(self):
        self.service.director_runtime.generate_plan = AsyncMock(return_value=DirectorPlan(speakers=[
            DirectorSpeakerPlan(actor_id='uma_a', target_actor_ids=['player'], intent='回应训练员'),
        ]))
        self.session = await self.service.create_session(
            user_uuid=self.session.user_uuid, template_id='test_scene', character_names=['角色A', '角色B'],
        )
        await self.fill()
        silent = self.session.actor_threads['uma_b']
        self.assertEqual(silent.last_seen_sequence, 0)
        self.assertEqual(len(silent.messages), 1)
        # Even if the other threads are short, unread public events must count.
        for thread in (self.session.director_thread, self.session.actor_threads['uma_a']):
            thread.messages = thread.messages[:1]
            thread.last_seen_sequence = self.session.timeline.latest_sequence
        await self.compact()
        self.assertIn(self.session.checkpoint.summary, self.session.actor_threads['uma_b'].messages[1]['content'])

    async def test_stage_multi_and_single_character_paths_use_shared_memory(self):
        for count in (1, 2):
            with self.subTest(count=count):
                self.service = self.make_service(self.root / f'stage-{count}', stage=True)
                self.session = await self.service.create_session(
                    user_uuid='00000000-0000-4000-8000-000000000001', template_id='test_scene',
                    character_names=['角色A', '角色B'][:count],
                )
                self.service.compactor.runtime.summarize = self.summarize
                if count == 1:
                    self.service.director_runtime.generate_plan = AsyncMock(return_value=DirectorPlan(speakers=[
                        DirectorSpeakerPlan(actor_id='uma_a', target_actor_ids=['player'], intent='回应训练员'),
                    ]))
                await self.fill()
                stage = {'anchors': [{'id': 'bench'}], 'actors': [{'id': 'uma_a'}, {'id': 'uma_b'}]}
                operation = self.service.execute_single_character_stage_turn if count == 1 else self.service.execute_stage_turn
                await operation(self.session, self.pending, stage_context=stage, allowed_stage_actor_ids=set(self.session.character_actor_ids))
                self.assertIsNotNone(self.session.checkpoint)
                packet = json.loads(self.session.director_thread.messages[-2]['content'])
                self.assertEqual(packet['live_stage'], stage)
                if count == 1:
                    self.assertIn('角色本轮已经完成回复', packet['instruction'])

    async def test_concurrent_turns_serialize_and_do_not_repeat_compaction(self):
        await asyncio.gather(
            self.service.execute_turn(self.session, self.pending),
            self.service.execute_turn(self.session, self.pending),
        )
        self.assertEqual(self.session.turn_index, 14)
        self.assertEqual(self.session.checkpoint.revision, 1)
        self.assertEqual(len(self.session.checkpoints), 1)

    async def test_scene_summary_uses_independent_budget_and_retries_original_on_length(self):
        llm = Llm()
        llm.outputs = [('truncated', 'length'), ('完整公开记忆', 'stop')]
        runtime = CharacterRuntime(llm_client=llm, settings=self.settings)
        summarizer = CompactionRuntime(runtime=runtime, settings=self.settings, config_prefix='DIRECTOR', purpose='scene_compaction')
        messages = [{'role': 'user', 'content': '已有公开事件'}]
        with self.assertLogs('umamusume_agent.llm_diagnostics', level='INFO') as logs:
            summary = await summarizer.summarize(messages=messages, target_tokens=500, session_id=self.session.session_id)
        self.assertEqual(summary, '完整公开记忆')
        self.assertEqual([call['max_tokens'] for call in llm.calls], [1024, 2048])
        self.assertEqual(llm.calls[0]['messages'], llm.calls[1]['messages'])
        self.assertNotIn('response_format', llm.calls[0])
        self.assertIn('scene_compaction', '\n'.join(logs.output))
        llm.outputs = [('', 'stop')]
        with self.assertRaises(CompactionError):
            await summarizer.summarize(messages=messages, target_tokens=500, session_id=self.session.session_id)


if __name__ == '__main__':
    unittest.main()
