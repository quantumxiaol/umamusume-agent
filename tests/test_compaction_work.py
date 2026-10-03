"""Offline budget, cancellation, privacy and paid-work reuse contracts."""
import asyncio
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from umamusume_agent.dialogue.compaction_runtime import CompactionError
from umamusume_agent.dialogue.compaction_work import CompactionWork, SummaryDraft
from umamusume_agent.dialogue.token_budget import estimate_tokens


class CompactionWorkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.session = SimpleNamespace(user_uuid='user-a', session_id='scene-a', history_file=Path(self.temp.name) / 'history.jsonl')
        self.runtime = SimpleNamespace(purpose='scene_compaction',
            settings=SimpleNamespace(ROLEPLAY_LLM_MODEL_NAME='test', ROLEPLAY_LLM_MODEL_BASE_URL='https://test'),
            setting=lambda key: {'MAX_TOKENS': 32768, 'MAX_DYNAMIC_TOKENS': 65536}[key],
            summarize=AsyncMock(return_value='甲答应明天陪乙训练。'))
        self.options = dict(runtime=self.runtime, session=self.session, ratio=0.5, budget=1000,
            capacity=1000000, reserve=65536, scene=True, prompt_key='role-card-v1',
            chunks=[[{'role': 'user', 'content': '第一段'}], [{'role': 'user', 'content': '第二段'}]],
            prefix=[{'role': 'system', 'content': '整理记忆'}])
        self.progress = AsyncMock()

    def work(self, **overrides):
        return CompactionWork(**{**self.options, **overrides})

    async def test_oversized_piece_shrinks_and_preserves_continuity_within_total_budget(self):
        self.runtime.summarize.side_effect = ['原始长摘要。' * 1000, '甲答应明天陪乙训练。', '乙答应准时到场。']
        work = self.work()
        with self.assertLogs('umamusume_agent.dialogue.compaction_work', level='INFO') as logs:
            summary = await work.run(self.progress)
        self.assertLessEqual(estimate_tokens([{'content': summary}], .5), 1000)
        self.assertIn('甲答应', summary)
        self.assertIn('乙答应', summary)
        self.assertEqual(self.runtime.summarize.await_count, 3)
        self.assertIn('原始长摘要', self.runtime.summarize.call_args_list[1].kwargs['messages'][-1]['content'])
        self.assertIn('甲答应', self.runtime.summarize.call_args_list[2].kwargs['messages'][1]['content'])
        self.assertNotIn('原始长摘要', '\n'.join(logs.output))
        self.assertTrue(any(call.args[0] == 'compacting' and call.kwargs.get('stage') == 'shrinking' for call in self.progress.call_args_list))

    async def test_failure_reuses_completed_pieces_from_disk_not_just_same_object(self):
        self.runtime.summarize.side_effect = ['第一段已完成', CompactionError('timeout')]
        with self.assertRaises(CompactionError):
            await self.work().run(self.progress)
        self.runtime.summarize.reset_mock(side_effect=True)
        summary = await self.work().run(self.progress)
        self.assertEqual(self.runtime.summarize.await_count, 1)
        self.assertIn('第一段已完成', summary)
        self.assertTrue(any(call.kwargs.get('reused') for call in self.progress.call_args_list))

    async def test_cancellation_does_not_discard_previous_complete_piece(self):
        entered = asyncio.Event()
        async def generate(**kwargs):
            if '第一段' in kwargs['messages'][-1]['content']:
                return '第一段完成'
            entered.set()
            await asyncio.Event().wait()
        self.runtime.summarize.side_effect = generate
        task = asyncio.create_task(self.work().run(self.progress))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.runtime.summarize.reset_mock(side_effect=True)
        await self.work().run(self.progress)
        self.assertEqual(self.runtime.summarize.await_count, 1)

    async def test_bounded_shrink_failure_retries_only_last_shrink(self):
        self.runtime.summarize.return_value = '仍然过长的摘要' * 1000
        with self.assertRaises(CompactionError):
            await self.work().run(self.progress)
        self.assertEqual(self.runtime.summarize.await_count, 3)
        self.runtime.summarize.reset_mock()
        self.runtime.summarize.return_value = '已精简的记忆'
        await self.work().run(self.progress)
        self.assertEqual(self.runtime.summarize.await_count, 2)  # Last shrink + untouched second piece.
        self.assertIn('<summary>', self.runtime.summarize.call_args_list[0].kwargs['messages'][-1]['content'])

    async def test_source_prompt_owner_and_budget_changes_do_not_reuse_stale_work(self):
        for overrides in [
            {'chunks': [[{'role': 'user', 'content': '修改过的原文'}]]},
            {'prefix': [{'role': 'system', 'content': '新版摘要要求'}]},
            {'prompt_key': 'role-card-v2'}, {'budget': 1200}, {'ratio': 0.6},
            {'session': SimpleNamespace(user_uuid='user-b', session_id='scene-a', history_file=self.session.history_file)},
        ]:
            await self.work().run(self.progress)
            self.runtime.summarize.reset_mock()
            await self.work(**overrides).run(self.progress)
            self.assertGreater(self.runtime.summarize.await_count, 0)

    async def test_draft_write_failure_stops_before_next_paid_call(self):
        with patch('umamusume_agent.dialogue.compaction_work.os.replace', side_effect=OSError('unavailable')):
            with self.assertRaisesRegex(CompactionError, '草稿保存失败'):
                await self.work().run(self.progress)
        self.assertEqual(self.runtime.summarize.await_count, 1)
        self.assertEqual(list(self.session.history_file.parent.glob('*.tmp')), [])
        self.assertFalse(self.session.history_file.exists())

    async def test_context_guard_prevents_oversized_request(self):
        with self.assertRaisesRegex(CompactionError, '容量'):
            await self.work(capacity=100000).run(self.progress)
        self.runtime.summarize.assert_not_awaited()

    async def test_draft_never_changes_history_and_can_be_cleared_after_commit(self):
        work = self.work()
        await work.run(self.progress)
        self.assertFalse(self.session.history_file.exists())
        data = json.loads(work.draft.path.read_text())
        self.assertEqual(len(data['entries']), 2)
        self.assertFalse('checkpoint' in data)
        work.draft.clear()
        self.assertFalse(work.draft.path.exists())

    def test_bad_sidecar_is_ignored(self):
        path = self.session.history_file.with_suffix('.compaction.json')
        path.write_text('[]')
        self.assertEqual(SummaryDraft(self.session.history_file, {}).entries, {})


if __name__ == '__main__':
    unittest.main()
