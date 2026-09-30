"""Application-local API authentication and rate limiting."""
import asyncio
from collections import defaultdict, deque
from time import monotonic
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


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
                retry_after = max(1, int(API_RATE_LIMIT_WINDOW_SECONDS - (now - bucket[0])))
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

    @app.middleware("http")
    async def protect_api(request: Request, call_next):
        if _requires_api_key(request):
            provided_key = request.headers.get("x-api-key", "").strip()
            if provided_key != API_ACCESS_KEY:
                return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key."})

        rate_limit_response = await _check_rate_limit(request)
        if rate_limit_response is not None:
            return rate_limit_response

        return await call_next(request)
