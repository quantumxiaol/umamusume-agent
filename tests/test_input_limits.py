"""Input/upload limits reject before mutation and keep old long histories usable."""
import unittest
from unittest.mock import AsyncMock, patch

from umamusume_agent import input_limits as limits
from umamusume_agent.server.body_limit import RequestBodyLimitMiddleware
from umamusume_agent.server.schemas import HistoryImportRequest
from tests import test_server_app as fixtures


class InputLimitApiTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ServerApplicationTests.asyncSetUp
    make_app = fixtures.ServerApplicationTests.make_app
    load_session = fixtures.ServerApplicationTests.load_session

    async def test_single_input_boundary_unicode_and_batch_rejected_without_mutation(self):
        _, services, client = self.make_app()
        loaded = await self.load_session(client)
        session = services.session_store.get(loaded['session_id'])
        response = await client.post('/chat', json={'session_id': session.session_id, 'message': '😀' * 10000})
        self.assertEqual(response.status_code, 200, response.text)
        before = session.history_file.read_bytes()
        with patch.object(services.character_runtime, 'generate_reply', AsyncMock()) as model:
            for path in ['/chat', '/chat_stream']:
                for message, events in [('中' * 10001, []), ('b' * 5001, [{'content': 'a' * 5000}]),
                                        ('x', [{'content': 'x'}] * 20)]:
                    response = await client.post(path, json={
                        'session_id': session.session_id, 'message': message, 'context_events': events,
                    })
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertNotIn('input', response.json()['detail'][0])
            model.assert_not_awaited()
        self.assertEqual(session.history_file.read_bytes(), before)

    async def test_director_and_stage_turns_validate_before_session_lookup(self):
        _, _, client = self.make_app()
        for path in ['/director/turn', '/director/turn_stream', '/stage/turn']:
            payload = {'session_id': 'missing', 'user_uuid': fixtures.USER_A,
                       'events': [{'content': 'a' * 5000}, {'content': 'b' * 5000}]}
            if path.startswith('/stage'):
                payload.update(actor_bindings=[], live_stage={
                    'schema_version': 'live_stage.v1', 'stage_id': 'test', 'scene_id': 'test', 'actors': [], 'anchors': [],
                })
            response = await client.post(path, json=payload)
            self.assertEqual(response.status_code, 404, response.text)  # Fits, session simply absent.
            payload['events'][-1]['content'] += 'x'
            response = await client.post(path, json=payload)
            self.assertEqual(response.status_code, 422, response.text)

    async def test_history_has_separate_limits_and_rejection_keeps_disk_memory_unchanged(self):
        _, services, client = self.make_app()
        loaded = await self.load_session(client)
        session = services.session_store.get(loaded['session_id'])
        payload = {'session_id': session.session_id, 'messages': [{'role': 'user', 'content': '旧' * 10001}]}
        self.assertEqual((await client.post('/history/import', json=payload)).status_code, 200)
        self.assertEqual(len(session.history[0]['content']), 10001)
        before = session.history_file.read_bytes()
        rejected = [
            [{'role': 'user', 'content': 'x' * (limits.MAX_HISTORY_FIELD_CHARS + 1)}],
            [{'role': 'user', 'content': 'x'}] * (limits.MAX_HISTORY_MESSAGES + 1),
        ]
        for messages in rejected:
            response = await client.post('/history/import', json={**payload, 'messages': messages})
            self.assertEqual(response.status_code, 422)
            self.assertLess(len(response.content), 1000)  # Never echo the archive in errors.
            self.assertEqual(session.history_file.read_bytes(), before)
        with patch.object(limits, 'MAX_HISTORY_TEXT_CHARS', 100):
            response = await client.post('/history/import', json={**payload, 'messages': [{'role': 'user', 'content': 'x'}], 'replace_current': False})
            self.assertEqual(response.status_code, 413)  # Existing + append exceeds budget.
            self.assertEqual(session.history_file.read_bytes(), before)

    async def test_body_size_limits_have_cors_and_reject_even_unknown_fields(self):
        _, services, client = self.make_app()
        loaded = await self.load_session(client)
        session = services.session_store.get(loaded['session_id'])
        before = session.history_file.read_bytes()
        with patch('umamusume_agent.server.body_limit.MAX_REQUEST_BYTES', 100):
            response = await client.post('/chat', json={'session_id': session.session_id, 'message': 'hi', 'unused': 'x' * 101},
                                         headers={'Origin': 'https://username.github.io'})
            self.assertEqual(response.status_code, 413)
            self.assertIn('access-control-allow-origin', response.headers)
        with patch('umamusume_agent.server.body_limit.MAX_HISTORY_BYTES', 100):
            for path in ['/history/import', '/director/sessions/recover']:
                response = await client.post(path, content=b'x' * 101)
                self.assertEqual(response.status_code, 413)
        self.assertEqual(session.history_file.read_bytes(), before)

    def test_history_summary_counts_and_aliases_are_bounded(self):
        limits.validate_history_size([{'modelContent': '旧' * 10001}])
        with self.assertRaises(ValueError):
            limits.validate_history_size([{'modelContent': 'x' * 200001}])
        with self.assertRaises(ValueError):
            limits.validate_history_size([], checkpoints=[{'summary': 'x'}] * 101)
        with patch.object(limits, 'MAX_HISTORY_TEXT_CHARS', 10):
            with self.assertRaises(ValueError):
                limits.validate_history_size([{'content': 'abc'}], checkpoint={'summary': 'x' * 8})
        # Import of a legacy message exactly at the historical field cap remains valid.
        HistoryImportRequest(session_id='s', messages=[{'role': 'user', 'content': 'x' * 200000}])


class BodyLimitStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_missing_or_false_content_length_never_reaches_application(self):
        for headers in ([], [(b'content-length', b'1')]):
            app = AsyncMock()
            receive = AsyncMock(side_effect=[
                {'type': 'http.request', 'body': b'a' * 60, 'more_body': True},
                {'type': 'http.request', 'body': b'b' * 41, 'more_body': False},
            ])
            send = AsyncMock()
            with patch('umamusume_agent.server.body_limit.MAX_REQUEST_BYTES', 100):
                await RequestBodyLimitMiddleware(app)({'type': 'http', 'method': 'POST', 'path': '/chat', 'headers': headers}, receive, send)
            app.assert_not_awaited()
            self.assertEqual(send.call_args_list[0].args[0]['status'], 413)

    async def test_exact_byte_limit_replays_body_once_and_preserves_disconnect(self):
        receive = AsyncMock(side_effect=[
            {'type': 'http.request', 'body': b'a' * 60, 'more_body': True},
            {'type': 'http.request', 'body': b'b' * 40, 'more_body': False},
            {'type': 'http.disconnect'},
        ])
        async def app(_scope, read, _send):
            self.assertEqual((await read())['body'], b'a' * 60 + b'b' * 40)
            self.assertEqual((await read())['type'], 'http.disconnect')
        with patch('umamusume_agent.server.body_limit.MAX_REQUEST_BYTES', 100):
            await RequestBodyLimitMiddleware(app)({'type': 'http', 'method': 'POST', 'path': '/chat', 'headers': []}, receive, AsyncMock())
