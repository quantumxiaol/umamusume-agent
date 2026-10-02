"""Bound request bodies before JSON parsing or session mutation (also chunked)."""
from starlette.responses import JSONResponse

from ..input_limits import MAX_HISTORY_BYTES, MAX_REQUEST_BYTES


class RequestBodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            return await self.app(scope, receive, send)
        path = scope.get('path', '').rstrip('/')
        limit = MAX_HISTORY_BYTES if path in {'/history/import', '/director/sessions/recover'} else MAX_REQUEST_BYTES

        async def reject():
            response = JSONResponse(status_code=413, content={
                'detail': f"请求内容超过 {limit // (1024 * 1024)} MiB 上限；未处理本次请求，请减小内容或拆分历史文件。",
            })
            await response(scope, receive, send)

        for key, value in scope.get('headers', []):
            if key.lower() == b'content-length':
                try:
                    length = int(value)
                    if length < 0:
                        raise ValueError('negative length')
                except ValueError:
                    response = JSONResponse(status_code=400, content={'detail': 'Content-Length 无效。'})
                    return await response(scope, receive, send)
                if length > limit:
                    return await reject()
        # Do not trust Content-Length: clients may omit/underreport it. Buffer a
        # bounded body before invoking the app so an over-limit stream has zero
        # application side effects. No background drain of an unlimited upload.
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            if len(body) + len(chunk) > limit:
                return await reject()
            body.extend(chunk)
            if not message.get('more_body', False):
                break
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                payload = bytes(body)
                body.clear()
                return {'type': 'http.request', 'body': payload, 'more_body': False}
            return await receive()

        await self.app(scope, replay, send)
