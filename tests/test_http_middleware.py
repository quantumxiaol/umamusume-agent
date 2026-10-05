"""Offline regressions for HF disconnects and cross-origin error responses."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from tests import test_server_app as fixtures


def http_scope(path, method='POST'):
    return {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.3'},
            'http_version': '1.1', 'method': method, 'scheme': 'http',
            'path': path, 'raw_path': path.encode(), 'query_string': b'', 'root_path': '',
            'headers': [(b'origin', b'https://username.github.io'), (b'content-type', b'application/json')],
            'client': ('127.0.0.1', 1234), 'server': ('testserver', 80)}


class HttpMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ServerApplicationTests.asyncSetUp
    make_app = fixtures.ServerApplicationTests.make_app
    load_session = fixtures.ServerApplicationTests.load_session

    def assert_cors(self, response):
        self.assertEqual(response.headers.get('access-control-allow-origin'), '*')
        self.assertIn('Retry-After', response.headers.get('access-control-expose-headers', ''))

    async def test_cross_origin_auth_rate_limit_validation_and_size_errors(self):
        _, _, auth = self.make_app('auth', API_ACCESS_KEY='secret')
        origin = {'Origin': 'https://username.github.io'}
        response = await auth.get('/capabilities', headers=origin)
        self.assertEqual(response.status_code, 401)
        self.assert_cors(response)
        _, _, client = self.make_app('limits', API_RATE_LIMIT_ENABLED=True, API_RATE_LIMIT_MAX_REQUESTS=1)
        with patch('umamusume_agent.server.middleware.monotonic', return_value=100) as clock:
            self.assert_cors(await client.get('/capabilities', headers=origin))
            clock.return_value = 100.1
            response = await client.get('/characters', headers=origin)
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers['retry-after'], '60')
            self.assertEqual(response.json()['retry_after'], 60)
            self.assert_cors(response)
            clock.return_value = 160.1
            self.assertEqual((await client.get('/characters', headers=origin)).status_code, 200)
        _, _, client = self.make_app('body')
        response = await client.post('/chat_stream', json={}, headers=origin)
        self.assertEqual(response.status_code, 422)
        self.assert_cors(response)
        with patch('umamusume_agent.server.body_limit.MAX_REQUEST_BYTES', 4):
            response = await client.post('/chat_stream', content=b'12345', headers=origin)
        self.assertEqual(response.status_code, 413)
        self.assert_cors(response)

    async def test_unexpected_errors_still_raise_and_500_has_cors(self):
        app, _, client = self.make_app()
        @app.get('/crash-test')
        async def crash():
            raise RuntimeError('real application failure')
        with self.assertRaisesRegex(RuntimeError, 'real application failure'):
            await client.get('/crash-test')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                     base_url='http://testserver') as browser:
            response = await browser.get('/crash-test', headers={'Origin': 'https://username.github.io'})
        self.assertEqual(response.status_code, 500)
        self.assert_cors(response)

    async def test_disconnected_partial_upload_returns_without_fake_response_or_mutation(self):
        app, services, _ = self.make_app()
        receive = AsyncMock(side_effect=[
            {'type': 'http.request', 'body': b'{"message":"private', 'more_body': True},
            {'type': 'http.disconnect'},
        ])
        send = AsyncMock()
        with self.assertLogs('umamusume_agent.server.middleware', level='INFO') as logs:
            await app(http_scope('/chat_stream'), receive, send)
        send.assert_not_awaited()
        self.assertEqual(services.session_store.sessions, {})
        self.assertIn('reason=client_disconnect', '\n'.join(logs.output))
        self.assertNotIn('private', '\n'.join(logs.output))

    async def test_stream_disconnect_cancels_model_and_logs_matching_call_id(self):
        app, services, client = self.make_app()
        loaded = await self.load_session(client)
        session = services.session_store.get(loaded['session_id'])
        started, model_cancelled = asyncio.Event(), asyncio.Event()

        async def pending_model(**_kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                model_cancelled.set()
                raise

        body = json.dumps({'session_id': session.session_id, 'message': 'private dialogue'}).encode()
        delivered = False
        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            await started.wait()
            return {'type': 'http.disconnect'}

        send = AsyncMock()
        with patch.object(services.character_runtime.llm_client.chat.completions, 'create', pending_model):
            with self.assertLogs('umamusume_agent', level='INFO') as logs:
                await asyncio.wait_for(app(http_scope('/chat_stream'), receive, send), timeout=2)
        self.assertTrue(model_cancelled.is_set())
        self.assertFalse(session.lock.locked())
        messages = [record.getMessage() for record in logs.records]
        begin = json.loads(next(message.split('LLM request ', 1)[1] for message in messages if message.startswith('LLM request {')))
        end = json.loads(next(message.split('LLM request cancelled ', 1)[1] for message in messages if message.startswith('LLM request cancelled ')))
        self.assertEqual(begin['call_id'], end['call_id'])
        self.assertEqual(begin['http_request_id'], end['http_request_id'])
        self.assertEqual(end['provider_usage'], 'unknown')
        self.assertIn('Dialogue stream cancelled', '\n'.join(messages))
        self.assertIn('reason=client_disconnect', '\n'.join(messages))
        self.assertNotIn('private dialogue', '\n'.join(messages))
        self.assertFalse(any(b'event: error' in call.args[0].get('body', b'') for call in send.call_args_list))

    async def test_task_cancellation_propagates_and_is_not_reported_as_client_disconnect(self):
        app, _, _ = self.make_app()
        started = asyncio.Event()
        async def receive():
            started.set()
            await asyncio.Event().wait()
        with self.assertLogs('umamusume_agent.server.middleware', level='INFO') as logs:
            task = asyncio.create_task(app(http_scope('/history/import'), receive, AsyncMock()))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIn('reason=task_cancelled', '\n'.join(logs.output))
