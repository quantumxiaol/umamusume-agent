"""Offline end-to-end contracts for memory budgets, persistence and recovery."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import httpx

from tests.server_support import test_settings
from tests.test_llm_usage import _response
from umamusume_agent.character.model import CharacterConfig, VoiceConfig
from umamusume_agent.dialogue.compaction import split_source
from umamusume_agent.dialogue.compaction_runtime import CompactionError, SUMMARY_INSTRUCTION
from umamusume_agent.dialogue.history import collect_history_messages, normalize_import_messages
from umamusume_agent.dialogue.memory import checkpoint_matches, source_digest
from umamusume_agent.dialogue.protocol import to_compact_context_message
from umamusume_agent.dialogue.token_budget import estimate_tokens, message_bytes
from umamusume_agent.llm_usage import DeepSeekUsageTracker
from umamusume_agent.server.app import create_app
from umamusume_agent.server.schemas import HistoryImportMessage
from umamusume_agent.server.services import build_services

USER = '00000000-0000-4000-8000-000000000001'
OTHER = '00000000-0000-4000-8000-000000000002'


class Stream:
    def __init__(self, text, reason):
        self.parts = iter([NS(choices=[NS(delta=NS(content=text), finish_reason=reason)])])
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.parts)
        except StopIteration:
            raise StopAsyncIteration

    async def close(self):
        self.closed = True


class Llm:
    def __init__(self):
        self.calls, self.streams, self.options = [], [], []
        self.outputs = []
        self.chat = NS(completions=self)

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self

    async def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        if kwargs.get('stream'):
            text, reason = self.outputs.pop(0) if self.outputs else ('约定：明天一起训练。称呼：训练员。', 'stop')
            stream = Stream(text, reason)
            self.streams.append(stream)
            return stream
        return NS(choices=[NS(message=NS(content='{ "action": "无", "dialogue": "我记得约定。" }'), finish_reason='stop')])


class CompactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.character = CharacterConfig(id='test', name_zh='测试角色', name_en='Test', name_jp='テスト',
                                         system_prompt='你是测试角色。', voice_config=VoiceConfig(no_voice=True))
        self.settings = test_settings(self.root,
            DIALOGUE_COMPACTION_ENABLED=True, DIALOGUE_CONTEXT_MAX_TOKENS=50000,
            DIALOGUE_CONTEXT_RESERVE_TOKENS=5000, DIALOGUE_COMPACTION_TRIGGER_TOKENS=8000,
            DIALOGUE_COMPACTION_TARGET_TOKENS=5000, DIALOGUE_COMPACTION_MEMORY_TOKENS=2000,
            DIALOGUE_COMPACTION_KEEP_TURNS=3, DIALOGUE_COMPACTION_RECENT_TOKENS=1500,
            DIALOGUE_COMPACTION_CHUNK_TOKENS=4500, DIALOGUE_COMPACTION_MAX_TOKENS=4096,
            DIALOGUE_COMPACTION_MAX_DYNAMIC_TOKENS=8192,
        )
        self.llm = Llm()
        self.services = self.make_services()
        self.session = self.services.session_store.create(self.character, user_uuid=USER)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(services=self.services)), base_url='http://test')
        self.addAsyncCleanup(self.client.aclose)

    def make_services(self):
        return build_services(settings=self.settings, llm_client=self.llm, tts_client=object(),
            character_manager=NS(load_character=AsyncMock(return_value=self.character), list_characters=lambda: [],
                                 character_exists=lambda _: False))

    def fill(self, count=30, start=0):
        for index in range(start, start + count):
            self.session.add_message('user', f'第{index}轮：' + '训练约定与经历。' * 20)
            self.session.add_message('assistant', f'回复{index}：明天见。', action='点头', dialogue=f'回复{index}：明天见。')

    async def compact(self):
        await self.services.compactor.prepare(self.session, [{'role': 'user', 'content': '继续'}])

    def records(self):
        return collect_history_messages(self.services.session_store.history_dir, USER,
            character_name=None, character_manager=self.services.character_manager)

    async def test_compaction_preserves_archive_recent_turns_and_append_only_prefix(self):
        self.fill()
        before = copy.deepcopy(self.session.history)
        await self.compact()
        checkpoint = self.session.checkpoint
        self.assertIsNotNone(checkpoint)
        self.assertEqual(self.session.history, before)
        self.assertEqual(checkpoint.covered_messages, len(before) - 6)
        messages = self.session.get_messages()
        self.assertLess(estimate_tokens(messages), self.settings.DIALOGUE_COMPACTION_TARGET_TOKENS)
        self.assertIn('conversation_memory', messages[1]['content'])
        self.assertEqual(len(messages), 8)
        calls = len(self.llm.calls)
        self.session.add_message('user', '新的一天')
        self.session.add_message('assistant', '继续训练', dialogue='继续训练')
        await self.compact()
        self.assertEqual(self.session.checkpoint, checkpoint)
        self.assertEqual(len(self.llm.calls), calls)
        self.assertEqual(self.session.get_messages()[:len(messages)], messages)

    async def test_second_compaction_includes_previous_memory_and_new_source(self):
        self.fill()
        await self.compact()
        old = self.session.checkpoint
        self.llm.calls.clear()
        self.fill(start=30)
        await self.compact()
        self.assertEqual(self.session.checkpoint.revision, 2)
        self.assertGreater(self.session.checkpoint.covered_messages, old.covered_messages)
        sources = '\n'.join(call['messages'][-1]['content'] for call in self.llm.calls)
        self.assertIn('已有历史记忆', sources)
        self.assertIn(old.summary, sources)
        self.assertNotIn('第0轮：', sources)
        self.assertEqual(len(self.session.history), 120)

    async def test_trigger_position_and_each_summary_survive_restart_and_browser_recovery(self):
        self.fill()
        with patch.object(self.services.compactor.runtime, 'summarize', AsyncMock(return_value='第一版独有内容')):
            await self.compact()
        first = self.session.checkpoint
        self.assertEqual(first.trigger_message_count, 60)
        self.assertEqual(first.covered_messages, 54)
        self.fill(start=30)
        with patch.object(self.services.compactor.runtime, 'summarize', AsyncMock(return_value='第二版独有内容')):
            await self.compact()
        second = self.session.checkpoint
        self.assertEqual(second.trigger_message_count, 120)
        self.assertEqual(len(self.session.checkpoints), 2)
        model_view = json.dumps(self.session.get_messages(), ensure_ascii=False)
        self.assertNotIn('第一版独有内容', model_view)  # Audit snapshots aren't extra model input.
        restored = self.make_services().session_store.create(self.character, user_uuid=USER)
        self.assertEqual(restored.checkpoints, [first, second])
        records = self.records()
        self.settings.DIALOGUE_HISTORY_DIRECTORY = str(self.root / 'fresh-audit')
        fresh_services = self.make_services()
        fresh = fresh_services.session_store.create(self.character, user_uuid=USER)
        normalized = normalize_import_messages([HistoryImportMessage(**item) for item in records])
        fresh.import_messages(normalized, checkpoint=second, checkpoints=[first, second])
        again = fresh_services.session_store.create(self.character, user_uuid=USER)
        self.assertEqual(again.checkpoints, [first, second])
        self.assertEqual(again.checkpoint, second)
        self.assertEqual(again.get_messages(), self.session.get_messages())

    async def test_old_long_history_is_untouched_until_next_send_then_marker_uses_old_end(self):
        self.fill()
        records = self.records()
        original = copy.deepcopy(self.session.history)
        imported = await self.client.post('/history/import', json={
            'session_id': self.session.session_id, 'messages': records,
        })
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.json()['context_checkpoints'], [])
        loaded = await self.client.post('/load_character', json={'character_name': '测试角色', 'user_uuid': USER})
        self.assertEqual(loaded.status_code, 200)
        session_id = loaded.json()['session_id']
        state = self.services.session_store.get(session_id)
        self.assertEqual(state.history, original)
        await self.client.get(f'/session/{session_id}/context', params={'user_uuid': USER})
        await self.client.get('/history', params={'user_uuid': USER, 'character_name': 'Test', 'limit': 0})
        self.assertEqual(self.llm.calls, [])  # Loading, importing and inspecting are read-only for the model.
        response = await self.client.post('/chat', json={'session_id': session_id, 'message': '下一句'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['context_checkpoint']['trigger_message_count'], len(original))
        self.assertEqual(state.history[:len(original)], original)
        self.assertEqual(len(state.history), len(original) + 2)

    async def test_legacy_checkpoint_without_trigger_position_remains_usable(self):
        self.fill()
        await self.compact()
        old_payload = self.session.checkpoint.model_dump(mode='json')
        old_payload.pop('trigger_message_count')
        response = await self.client.post('/history/import', json={
            'session_id': self.session.session_id, 'messages': self.records(), 'context_checkpoint': old_payload,
        })
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['context_checkpoint']['trigger_message_count'])
        self.assertEqual(len(response.json()['context_checkpoints']), 1)
        before = len(self.llm.calls)
        await self.client.post('/chat', json={'session_id': self.session.session_id, 'message': '继续'})
        self.assertEqual(len(self.llm.calls), before + 1)  # One RP call, no repeated compaction.

    async def test_import_prunes_invalid_audit_snapshots_and_clear_removes_all_markers(self):
        self.fill()
        await self.compact()
        first = self.session.checkpoint
        self.fill(start=30)
        await self.compact()
        second = self.session.checkpoint
        records = self.records()
        records[70]['content'] = '改写第一版之后、第二版之内的一条原文'
        response = await self.client.post('/history/import', json={
            'session_id': self.session.session_id, 'messages': records,
            'context_checkpoint': second.model_dump(mode='json'),
            'context_checkpoints': [item.model_dump(mode='json') for item in [first, second]],
        })
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['context_checkpoint'])
        self.assertEqual([item['checkpoint_id'] for item in response.json()['context_checkpoints']], [first.checkpoint_id])
        restored = self.make_services().session_store.create(self.character, user_uuid=USER)
        self.assertEqual(restored.checkpoints, [first])
        self.assertIsNone(restored.checkpoint)  # A historical snapshot is never activated implicitly.
        await self.client.delete('/history', params={'user_uuid': USER, 'character_name': 'Test'})
        self.assertEqual(self.session.checkpoints, [])

    async def test_jsonl_restore_and_browser_import_keep_checkpoint_and_raw_reply(self):
        self.fill()
        response = await self.client.post('/chat', json={'session_id': self.session.session_id, 'message': '记得吗？'})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn('context_checkpoint', payload)
        self.assertTrue(payload['message']['model_content'].startswith('{ '))
        before = self.session.get_messages()
        restored = self.make_services().session_store.create(self.character, user_uuid=USER)
        self.assertEqual(restored.checkpoint, self.session.checkpoint)
        self.assertEqual(restored.get_messages(), before)
        records = self.records()
        # Simulate HF storage being absent: use a fresh service/root and POST the
        # browser's full archive plus checkpoint to its new session.
        self.settings.DIALOGUE_HISTORY_DIRECTORY = str(self.root / 'fresh')
        fresh_services = self.make_services()
        fresh = fresh_services.session_store.create(self.character, user_uuid=USER)
        normalized = normalize_import_messages([HistoryImportMessage(**item) for item in records])
        fresh.import_messages(normalized, checkpoint=self.session.checkpoint)
        self.assertEqual(fresh.get_messages(), before)
        again = fresh_services.session_store.create(self.character, user_uuid=USER)
        self.assertEqual(again.get_messages(), before)

    async def test_source_character_user_and_prompt_mismatch_discard_checkpoint(self):
        self.fill()
        await self.compact()
        cp = self.session.checkpoint
        for update in [dict(user_uuid=OTHER), dict(character_id='other'), dict(prompt_digest='0' * 64), dict(source_digest='0' * 64)]:
            self.assertFalse(checkpoint_matches(self.session, cp.model_copy(update=update)))
        records = self.records()
        normalized = normalize_import_messages([HistoryImportMessage(**item) for item in records])
        normalized[0]['content'] = '已编辑的旧历史'
        self.session.import_messages(normalized, checkpoint=cp)
        self.assertIsNone(self.session.checkpoint)
        restored = self.make_services().session_store.create(self.character, user_uuid=USER)
        self.assertIsNone(restored.checkpoint)

    async def test_regeneration_preserves_memory_only_when_covered_prefix_unchanged(self):
        self.fill()
        await self.compact()
        cp = self.session.checkpoint
        records = self.records()
        normalized = normalize_import_messages([HistoryImportMessage(**item) for item in records])
        self.session.import_messages(normalized[:-2], source='regenerate')
        self.assertEqual(self.session.checkpoint, cp)
        restored = self.make_services().session_store.create(self.character, user_uuid=USER)
        self.assertEqual(len(restored.history), len(normalized) - 2)  # no duplicate imports
        self.assertEqual(restored.checkpoint, cp)
        self.session.import_messages(normalized[:2])
        self.assertIsNone(self.session.checkpoint)

    async def test_sse_progress_before_reply_and_scoped_checkpoint(self):
        self.fill()
        response = await self.client.post('/chat_stream', json={'session_id': self.session.session_id, 'message': '继续'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('"phase": "compacting"', response.text)
        self.assertIn('"phase": "compacted"', response.text)
        self.assertLess(response.text.index('event: context_status'), response.text.index('event: structured_reply'))
        self.assertIn('event: done', response.text)
        url = f'/session/{self.session.session_id}/context'
        self.assertEqual((await self.client.get(url, params={'user_uuid': OTHER})).status_code, 404)
        result = await self.client.get(url, params={'user_uuid': USER})
        self.assertEqual(result.json()['context_checkpoint']['revision'], 1)

    async def test_length_retries_original_request_with_independent_budget(self):
        self.llm.outputs = [('partial', 'length'), ('完整记忆', 'stop')]
        messages = [{'role': 'system', 'content': SUMMARY_INSTRUCTION}, {'role': 'user', 'content': '旧记录'}]
        text = await self.services.compactor.runtime.summarize(messages=messages, target_tokens=1000, session_id=self.session.session_id)
        self.assertEqual(text, '完整记忆')
        self.assertEqual([call['max_tokens'] for call in self.llm.calls], [4096, 8192])
        self.assertEqual(self.llm.calls[0]['messages'], self.llm.calls[1]['messages'])
        self.assertNotIn('response_format', self.llm.calls[0])
        self.assertEqual(self.llm.options[0]['max_retries'], 0)
        self.assertTrue(all(stream.closed for stream in self.llm.streams))

    async def test_partial_empty_oversized_and_persist_failures_never_install(self):
        self.fill()
        before = copy.deepcopy(self.session.history)
        for outputs in [[('', 'stop')], [('partial', 'length')] * 2, [('太长' * 5000, 'stop')] * 5]:
            self.llm.outputs = outputs
            with self.assertRaises(CompactionError):
                await self.compact()
            self.assertIsNone(self.session.checkpoint)
            self.assertEqual(self.session.history, before)
        self.llm.outputs = []
        with patch.object(self.session, '_append_history_event', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                await self.compact()
        self.assertIsNone(self.session.checkpoint)

    async def test_oversized_pending_input_rejected_before_history_append(self):
        self.fill()
        before = copy.deepcopy(self.session.history)
        response = await self.client.post('/chat', json={'session_id': self.session.session_id, 'message': '超大输入' * 20000})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.session.history, before)
        self.assertEqual(self.llm.calls, [])

    async def test_long_recent_turns_reduce_retained_turns_without_splitting_them(self):
        self.fill()
        self.session.add_message('user', '巨大的上一轮' * 2000)
        self.session.add_message('assistant', '收到', dialogue='收到')
        await self.compact()
        self.assertEqual(self.session.checkpoint.covered_messages, len(self.session.history))
        self.assertEqual(len(self.session.get_messages()), 2)

    async def test_clear_removes_memory_and_does_not_resurrect_on_restart(self):
        self.fill()
        await self.compact()
        response = await self.client.delete('/history', params={'user_uuid': USER, 'character_name': 'Test'})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.session.checkpoint)
        restored = self.make_services().session_store.create(self.character, user_uuid=USER)
        self.assertIsNone(restored.checkpoint)
        self.assertEqual(restored.history, [])

    async def test_cancellation_and_same_session_lock(self):
        self.fill()
        entered = asyncio.Event()
        async def blocked(**_kwargs):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(self.services.compactor.runtime, 'summarize', side_effect=blocked):
            task = asyncio.create_task(self.client.post('/chat', json={'session_id': self.session.session_id, 'message': '继续'}))
            await entered.wait()
            self.assertTrue(self.session.lock.locked())
            importer = asyncio.create_task(self.client.post('/history/import', json={'session_id': self.session.session_id, 'messages': []}))
            await asyncio.sleep(0)
            self.assertFalse(importer.done())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual((await importer).status_code, 200)
            self.assertIsNone(self.session.checkpoint)
            self.assertEqual(self.session.history, [])

    async def test_failed_fsync_rolls_back_only_the_checkpoint_append(self):
        self.fill()
        original = self.session.history_file.read_bytes()
        with patch('umamusume_agent.dialogue.session.os.fsync', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                await self.compact()
        self.assertIsNone(self.session.checkpoint)
        self.assertEqual(self.session.history_file.read_bytes(), original)

    async def test_timeout_closes_stream_without_installing_memory(self):
        self.fill()
        closed = asyncio.Event()
        class HangingStream:
            def __aiter__(self):
                return self
            async def __anext__(self):
                await asyncio.Event().wait()
            async def close(self):
                closed.set()
        self.settings.DIALOGUE_COMPACTION_TIMEOUT_SECONDS = 0.01
        with patch.object(self.llm, 'create', AsyncMock(return_value=HangingStream())):
            with self.assertRaises(CompactionError):
                await self.compact()
        self.assertTrue(closed.is_set())
        self.assertIsNone(self.session.checkpoint)

    async def test_default_scale_600k_source_is_summarized_in_multiple_calls(self):
        self.settings.DIALOGUE_CONTEXT_MAX_TOKENS = 1000000
        self.settings.DIALOGUE_CONTEXT_RESERVE_TOKENS = 65536
        self.settings.DIALOGUE_COMPACTION_TRIGGER_TOKENS = 600000
        self.settings.DIALOGUE_COMPACTION_TARGET_TOKENS = 200000
        self.settings.DIALOGUE_COMPACTION_MEMORY_TOKENS = 100000
        self.settings.DIALOGUE_COMPACTION_KEEP_TURNS = 8
        self.settings.DIALOGUE_COMPACTION_RECENT_TOKENS = 40000
        self.settings.DIALOGUE_COMPACTION_CHUNK_TOKENS = 100000
        self.settings.DIALOGUE_COMPACTION_MAX_TOKENS = 32768
        self.settings.DIALOGUE_COMPACTION_MAX_DYNAMIC_TOKENS = 65536
        for _ in range(50):
            self.session.add_message('user', '记' * 8000)
            self.session.add_message('assistant', '收到', dialogue='收到')
        await self.compact()
        self.assertIsNotNone(self.session.checkpoint)
        self.assertGreaterEqual(len(self.llm.calls), 5)
        self.assertLessEqual(len(self.llm.calls), 8)
        self.assertEqual(len(self.session.history), 100)
        self.assertLess(estimate_tokens(self.session.get_messages()), 200000)

    async def test_disabled_and_short_history_do_not_request_summaries(self):
        self.fill(count=1)
        await self.compact()
        self.assertEqual(self.llm.calls, [])
        self.fill()
        self.settings.DIALOGUE_COMPACTION_ENABLED = False
        await self.compact()
        self.assertEqual(self.llm.calls, [])

    async def test_compaction_failure_does_not_submit_tts_or_generate_a_reply(self):
        self.fill()
        self.settings.ENABLE_TTS = True
        self.llm.outputs = [('', 'stop')]
        with patch('umamusume_agent.server.dialogue_routes.submit_single_voice', AsyncMock()) as tts:
            response = await self.client.post('/chat_stream', json={
                'session_id': self.session.session_id, 'message': '继续', 'generate_voice': True,
            })
        self.assertIn('event: error', response.text)
        self.assertNotIn('event: structured_reply', response.text)
        self.assertNotIn('event: done', response.text)
        tts.assert_not_awaited()
        self.assertTrue(all(call.get('stream') for call in self.llm.calls))
        self.assertEqual(len(self.session.history), 60)

    async def test_usage_groups_summary_and_reply_but_calibration_uses_reply_only(self):
        self.fill()
        tracker = DeepSeekUsageTracker(base_url='https://api.deepseek.com')
        self.services.usage_tracker = tracker
        self.services.character_runtime.usage_tracker = tracker
        # Rebuild only the route closures after replacing the per-app tracker.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(services=self.services)), base_url='http://test') as client:
            original = self.llm.create
            async def create(**kwargs):
                result = await original(**kwargs)
                usage = _response(request_id=str(len(self.llm.calls)), prompt=20000 if kwargs.get('stream') else 1234,
                                  cached=1000, completion=100, reasoning=20)
                if kwargs.get('stream'):
                    chunks = list(result.parts)
                    chunks[-1].usage = usage.usage
                    chunks[-1].id = usage.id
                    result.parts = iter(chunks)
                else:
                    result.usage = usage.usage
                    result.id = usage.id
                return result
            with patch.object(self.llm, 'create', side_effect=create):
                response = await client.post('/chat_stream', json={'session_id': self.session.session_id, 'message': '继续'})
        self.assertIn('event: structured_reply', response.text)
        stats = tracker.snapshot(user_uuid=USER)
        self.assertEqual(len(stats['recent_operations']), 1)
        self.assertEqual(stats['instance']['request_count'], len(self.llm.calls))
        self.assertEqual(stats['instance']['prompt_tokens'], (len(self.llm.calls) - 1) * 20000 + 1234)
        expected = min(1.5, max(0.1, 1234 / message_bytes(self.llm.calls[-1]['messages']) * 1.1))
        self.assertAlmostEqual(self.session.token_ratio, expected)
        self.assertEqual(tracker.snapshot(user_uuid=OTHER)['instance']['request_count'], 0)

    def test_digest_is_semantic_and_chunk_split_preserves_unicode(self):
        record = {'role': 'assistant', 'action': '点头', 'dialogue': '明天见'}
        a = to_compact_context_message(record)
        b = to_compact_context_message({**record, 'model_content': '{ "dialogue": "明天见", "action": "点头" }'})
        self.assertEqual(source_digest([a]), source_digest([b]))
        content = '你好🙂' * 3000
        chunks = split_source([{'role': 'user', 'content': content}], max_tokens=1000, ratio=0.5)
        self.assertEqual(''.join(item['content'] for chunk in chunks for item in chunk), content)
        self.assertTrue(all(estimate_tokens(chunk) <= 1000 for chunk in chunks))
