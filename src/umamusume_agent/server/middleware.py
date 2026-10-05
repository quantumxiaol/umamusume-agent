"""Application-local API authentication and rate limiting."""
import asyncio
import hashlib
import logging
from collections import defaultdict, deque
from math import ceil
from time import monotonic
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..llm_diagnostics import llm_request_scope

logger = logging.getLogger(__name__)


def _get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def install_api_protection(app: FastAPI, *, settings) -> None:
    """Each app owns its rate-limit buckets and lock (no module globals)."""
    API_ACCESS_KEY = (settings.API_ACCESS_KEY or "").strip()
    API_RATE_LIMIT_ENABLED = settings.API_RATE_LIMIT_ENABLED
    API_RATE_LIMIT_WINDOW_SECONDS = max(1, settings.API_RATE_LIMIT_WINDOW_SECONDS)
    API_RATE_LIMIT_MAX_REQUESTS = max(1, settings.API_RATE_LIMIT_MAX_REQUESTS)
    API_CHAT_RATE_LIMIT_MAX_REQUESTS = max(1, settings.API_CHAT_RATE_LIMIT_MAX_REQUESTS)
    API_AUTH_EXEMPT_PATHS = {"/", "/audio"}
    _CHAT_ENDPOINTS = {
        "/chat",
        "/chat_stream",
        "/director/turn",
        "/director/turn_stream",
        "/stage/turn",
    }
    _rate_limit_buckets: dict[str, deque[float]] = defaultdict(deque)
    _rate_limit_lock = asyncio.Lock()

    def _requires_api_key(request: Request) -> bool:
        if request.method == "OPTIONS":
            return False
        if not API_ACCESS_KEY:
            return False
        path = request.url.path
        if path in API_AUTH_EXEMPT_PATHS:
            return False
        if path.startswith("/tts/jobs/") and path.endswith("/audio"):
            return False
        return True

    async def _check_rate_limit(request: Request) -> Optional[JSONResponse]:
        if not API_RATE_LIMIT_ENABLED:
            return None

        path = request.url.path
        if request.method == "OPTIONS":
            return None
        if (
            path in API_AUTH_EXEMPT_PATHS
            or (
                path.startswith("/tts/jobs/")
                and path.endswith("/audio")
            )
        ):
            return None

        client_ip = _get_client_ip(request)
        is_chat = path in _CHAT_ENDPOINTS
        bucket_key = f"{client_ip}:{'chat' if is_chat else 'default'}"
        max_requests = API_CHAT_RATE_LIMIT_MAX_REQUESTS if is_chat else API_RATE_LIMIT_MAX_REQUESTS
        now = monotonic()

        async with _rate_limit_lock:
            bucket = _rate_limit_buckets[bucket_key]
            while bucket and (now - bucket[0]) >= API_RATE_LIMIT_WINDOW_SECONDS:
                bucket.popleft()

            if len(bucket) >= max_requests:
                retry_after = max(1, ceil(API_RATE_LIMIT_WINDOW_SECONDS - (now - bucket[0])))
                logger.warning(
                    "API rate limited path=%s bucket=%s client_key=%s ip_source=%s limit=%s window_seconds=%s retry_after=%s",
                    path, 'chat' if is_chat else 'default',
                    hashlib.sha256(client_ip.encode()).hexdigest()[:12],
                    'x-forwarded-for' if request.headers.get('x-forwarded-for') else 'peer',
                    max_requests, API_RATE_LIMIT_WINDOW_SECONDS, retry_after,
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": f"Rate limit exceeded for {path}. Please retry later.",
                        "retry_after": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )

            bucket.append(now)

        return None

    class APIProtectionMiddleware:
        """Pure ASGI: a disconnected upload may finish without a response.

        Do not buffer streams or turn cancellation into an HTTP success/error.
        The receive wrapper observes disconnects without consuming extra events.
        """

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope['type'] != 'http':
                return await self.app(scope, receive, send)
            request = Request(scope)
            if _requires_api_key(request):
                provided_key = request.headers.get("x-api-key", "").strip()
                if provided_key != API_ACCESS_KEY:
                    response = JSONResponse(status_code=401, content={"detail": "Invalid or missing API key."})
                    return await response(scope, receive, send)
            limited = await _check_rate_limit(request)
            if limited is not None:
                return await limited(scope, receive, send)

            request_id = uuid4().hex
            started = monotonic()
            disconnected = completed = response_started = cancelled = False

            async def observe_receive():
                nonlocal disconnected
                message = await receive()
                if message['type'] == 'http.disconnect':
                    disconnected = True
                return message

            async def observe_send(message):
                nonlocal response_started, completed
                await send(message)
                if message['type'] == 'http.response.start':
                    response_started = True
                elif message['type'] == 'http.response.body' and not message.get('more_body', False):
                    completed = True

            try:
                with llm_request_scope(http_request_id=request_id):
                    await self.app(scope, observe_receive, observe_send)
            except asyncio.CancelledError:
                cancelled = True
                raise
            finally:
                if not completed and (disconnected or cancelled):
                    logger.info(
                        "HTTP request cancelled request_id=%s method=%s path=%s reason=%s response_started=%s elapsed_ms=%s",
                        request_id, scope['method'], scope['path'],
                        'client_disconnect' if disconnected else 'task_cancelled', response_started,
                        round((monotonic() - started) * 1000),
                    )

    app.add_middleware(APIProtectionMiddleware)
