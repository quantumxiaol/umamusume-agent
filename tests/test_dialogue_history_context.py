"""Old single-chat archives remain readable; only their model view becomes JSON."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx

from tests.server_support import test_settings
from umamusume_agent.character.model import CharacterConfig, VoiceConfig
from umamusume_agent.dialogue.history import parse_history_file
from umamusume_agent.server.app import create_app
from umamusume_agent.server.services import build_services


class HistoryContextCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = test_settings(
            Path(self.temp.name), DIALOGUE_COMPACTION_ENABLED=True,
            DIALOGUE_COMPACTION_TRIGGER_TOKENS=750000,
            DIALOGUE_HIDDEN_FORMAT_REINJECTION_ENABLED=True,
            DIALOGUE_HIDDEN_FORMAT_REINJECTION_INTERVAL_MESSAGES=2,
        )
        self.character = CharacterConfig(
            id='test', name_zh='测试角色', name_en='Test', name_jp='テスト',
            system_prompt='你是测试角色。', voice_config=VoiceConfig(no_voice=True),
        )
        self.completion = AsyncMock(return_value=NS(choices=[NS(
            message=NS(content='{"action":"点头。","dialogue":"记得约定。"}'), finish_reason='stop',
        )]))
        self.services = build_services(
            settings=self.settings, llm_client=NS(chat=NS(completions=NS(create=self.completion))),
            tts_client=object(), character_manager=NS(
                load_character=AsyncMock(return_value=self.character), list_characters=lambda: [],
                character_exists=lambda _: False,
            ),
        )
        self.user_uuid = '00000000-0000-4000-8000-000000000001'
        self.session = self.services.session_store.create(self.character, user_uuid=self.user_uuid)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(services=self.services)), base_url='http://test',
        )
        self.addAsyncCleanup(self.client.aclose)
        self.raw_json = '{\n "dialogue": "明天见。", "action": "挥手。"\n}'
        self.records = [
            {'role': 'user', 'content': '你好。'},
            # v2 browser history predating model_content.
            {'role': 'assistant', 'content': '请多指教。', 'action': '点头。',
             'dialogue': '请多指教。', 'schema_version': 2, 'source_format': 'json_v2'},
            {'role': 'user', 'content': '递给她毛巾。', 'event_type': 'action',
             'actor': {'actor_id': 'player', 'actor_type': 'trainer', 'display_name': '训练员'},
             'target_actor_ids': ['test'], 'event_schema_version': 1},
            # Legacy file exports with only role/content.
            {'role': 'assistant', 'content': '动作：接过毛巾。\n对白：谢谢。'},
            {'role': 'user', 'content': '记住约定。'},
            {'role': 'assistant', 'content': '记住了。'},
            {'role': 'user', 'content': '明天见。'},
            {'role': 'assistant', 'content': '明天见。', 'action': '挥手。',
             'dialogue': '明天见。', 'model_content': self.raw_json, 'source_format': 'json_v2'},
        ]
        self.expected_replies = [
            {'action': '点头。', 'dialogue': '请多指教。'},
            {'action': '接过毛巾。', 'dialogue': '谢谢。'},
            {'action': '无', 'dialogue': '记住了。'},
            {'action': '挥手。', 'dialogue': '明天见。'},
        ]

    async def import_history(self, records, source='browser_cache'):
        response = await self.client.post('/history/import', json={
            'session_id': self.session.session_id, 'messages': records,
            'replace_current': True, 'source': source,
        })
        self.assertEqual(response.status_code, 200, response.text)

    def assert_json_request(self):
        messages = self.completion.call_args.kwargs['messages']
        replies = [message['content'] for message in messages if message['role'] == 'assistant']
        self.assertEqual([json.loads(content) for content in replies], self.expected_replies)
        self.assertEqual(replies[-1], self.raw_json)
        self.assertIn('【训练员动作】递给她毛巾。', messages[3]['content'])
        self.assertIn('JSON 格式提醒', messages[3]['content'])
        self.assertEqual(sum(message['role'] == 'system' for message in messages), 1)

    async def test_import_restore_and_continue_keep_all_old_formats(self):
        for route in ('/chat', '/chat_stream'):
            with self.subTest(route=route):
                self.completion.reset_mock()
                await self.import_history(self.records)
                old_history = copy.deepcopy(self.session.history)
                archive_bytes = self.session.history_file.read_bytes()
                # A new backend session loads the same JSONL without rewriting it.
                restored = self.services.session_store.create(self.character, user_uuid=self.user_uuid)
                self.assertEqual(restored.history, old_history)
                self.assertEqual(self.session.history_file.read_bytes(), archive_bytes)
                self.completion.assert_not_awaited()
                response = await self.client.post(route, json={
                    'session_id': restored.session_id, 'message': '还记得吗？',
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(self.completion.await_count, 1)
                self.assert_json_request()
                self.assertEqual(restored.history[:len(old_history)], old_history)
                self.assertEqual(self.session.history_file.read_bytes(), archive_bytes)
                archived, _ = parse_history_file(self.session.history_file)
                replies = [record for record in archived if record['role'] == 'assistant']
                self.assertEqual(
                    [{'action': r['action'], 'dialogue': r['dialogue']} for r in replies],
                    self.expected_replies,
                )
                self.assertFalse(replies[0].get('model_content'))  # No invented raw output in storage.
                self.assertEqual(replies[-1]['model_content'], self.raw_json)
                if route == '/chat_stream':
                    self.assertIn('event: structured_reply', response.text)
                    self.assertIn('event: done', response.text)

    async def test_regenerate_reimports_old_prefix_without_failed_reply(self):
        failed_round = [
            {'role': 'user', 'content': '还记得吗？'},
            {'role': 'assistant', 'action': '无', 'dialogue': '抱歉，刚才有点没听清，可以再说一次吗？',
             'source_format': 'parse_error'},
        ]
        await self.import_history([*self.records, *failed_round])
        # This is the existing browser's regenerate-last-user workflow.
        await self.import_history(self.records, source='regenerate_last_user')
        response = await self.client.post('/chat_stream', json={
            'session_id': self.session.session_id, 'message': failed_round[0]['content'],
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.completion.await_count, 1)
        self.assert_json_request()
        self.assertNotIn('没听清', json.dumps(self.completion.call_args.kwargs['messages'], ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
