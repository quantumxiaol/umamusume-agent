"""In-memory, browser-scoped DeepSeek usage accounting.

The DeepSeek balance endpoint only exposes the account's remaining balance.
Per-request token usage is already returned by Chat Completions, so keeping a
bounded local ledger is both more accurate and safer for a shared frontend.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from time import monotonic
from typing import Any, Iterator
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def is_deepseek_base_url(base_url: str) -> bool:
    """Return whether a configured OpenAI-compatible URL is DeepSeek-owned."""

    raw = str(base_url or "").strip()
    if not raw:
        return False
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    return hostname == "deepseek.com" or hostname.endswith(".deepseek.com")


def _read(source: Any, key: str) -> Any:
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


def _token_count(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class UsageScope:
    user_uuid: str
    feature: str
    operation_id: str
    started_at: datetime


@dataclass(frozen=True)
class UsageEvent:
    created_at: datetime
    operation_started_at: datetime
    user_uuid: str
    feature: str
    operation_id: str
    request_id: str
    model: str
    finish_reason: str
    prompt_tokens: int
    cached_input_tokens: int
    uncached_input_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    total_tokens: int
    latency_ms: int


class DeepSeekUsageTracker:
    """Keep a bounded ledger for the lifetime of one backend process."""

    def __init__(
        self,
        *,
        base_url: str,
        max_events: int = 10_000,
        recent_operations: int = 6,
        display_timezone: str = "Asia/Shanghai",
    ) -> None:
        self.enabled = is_deepseek_base_url(base_url)
        self.provider = "deepseek" if self.enabled else ""
        self.started_at = datetime.now(timezone.utc)
        self.max_events = max(1, int(max_events))
        self.recent_operations = max(1, int(recent_operations))
        try:
            self.display_timezone = ZoneInfo(display_timezone)
            self.timezone_name = display_timezone
        except ZoneInfoNotFoundError:
            self.display_timezone = timezone.utc
            self.timezone_name = "UTC"
        self._events: deque[UsageEvent] = deque(maxlen=self.max_events)
        self._dropped_events = 0
        self._lock = Lock()
        self._scope: ContextVar[UsageScope | None] = ContextVar(
            f"deepseek_usage_scope_{id(self)}",
            default=None,
        )

    @contextmanager
    def operation(self, *, user_uuid: str, feature: str) -> Iterator[str | None]:
        """Attribute every model call in the block to one browser operation."""

        normalized_user = str(user_uuid or "").strip()
        if not self.enabled or not normalized_user:
            yield None
            return

        scope = UsageScope(
            user_uuid=normalized_user,
            feature=str(feature or "unknown").strip() or "unknown",
            operation_id=uuid4().hex,
            started_at=datetime.now(timezone.utc),
        )
        token = self._scope.set(scope)
        try:
            yield scope.operation_id
        finally:
            self._scope.reset(token)

    @staticmethod
    def started_timer() -> float:
        return monotonic()

    @staticmethod
    def elapsed_ms(started: float) -> int:
        return max(0, round((monotonic() - started) * 1000))

    def record_response(
        self,
        response: Any,
        *,
        finish_reason: str,
        latency_ms: int | None = None,
    ) -> None:
        """Record usage from one completed DeepSeek response if scoped."""

        if not self.enabled:
            return
        scope = self._scope.get()
        usage = getattr(response, "usage", None)
        if scope is None or usage is None:
            return

        prompt_tokens = _token_count(_read(usage, "prompt_tokens"))
        completion_tokens = _token_count(_read(usage, "completion_tokens"))
        if prompt_tokens is None or completion_tokens is None:
            return

        prompt_details = (
            _read(usage, "prompt_tokens_details")
            or _read(usage, "input_tokens_details")
        )
        cached_tokens = _token_count(_read(usage, "prompt_cache_hit_tokens"))
        if cached_tokens is None and prompt_details is not None:
            cached_tokens = _token_count(_read(prompt_details, "cached_tokens"))
        cached_tokens = min(prompt_tokens, cached_tokens or 0)

        uncached_tokens = _token_count(
            _read(usage, "prompt_cache_miss_tokens")
        )
        if uncached_tokens is None:
            uncached_tokens = max(0, prompt_tokens - cached_tokens)

        completion_details = _read(usage, "completion_tokens_details")
        reasoning_tokens = _token_count(
            _read(completion_details, "reasoning_tokens")
            if completion_details is not None
            else None
        ) or 0
        total_tokens = _token_count(_read(usage, "total_tokens"))
        if total_tokens is None:
            total_tokens = prompt_tokens + completion_tokens

        event = UsageEvent(
            created_at=datetime.now(timezone.utc),
            operation_started_at=scope.started_at,
            user_uuid=scope.user_uuid,
            feature=scope.feature,
            operation_id=scope.operation_id,
            request_id=str(getattr(response, "id", None) or "unknown"),
            model=str(getattr(response, "model", None) or "unknown"),
            finish_reason=str(finish_reason or "unknown"),
            prompt_tokens=prompt_tokens,
            cached_input_tokens=cached_tokens,
            uncached_input_tokens=uncached_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=min(completion_tokens, reasoning_tokens),
            total_tokens=total_tokens,
            latency_ms=max(0, int(latency_ms or 0)),
        )
        with self._lock:
            if len(self._events) == self.max_events:
                self._dropped_events += 1
            self._events.append(event)

    @staticmethod
    def _aggregate(events: list[UsageEvent]) -> dict[str, Any]:
        prompt_tokens = sum(item.prompt_tokens for item in events)
        cached_tokens = sum(item.cached_input_tokens for item in events)
        completion_tokens = sum(item.completion_tokens for item in events)
        latency_ms = sum(item.latency_ms for item in events)
        return {
            "request_count": len(events),
            "prompt_tokens": prompt_tokens,
            "cached_input_tokens": cached_tokens,
            "uncached_input_tokens": sum(
                item.uncached_input_tokens for item in events
            ),
            "completion_tokens": completion_tokens,
            "reasoning_tokens": sum(item.reasoning_tokens for item in events),
            "total_tokens": sum(item.total_tokens for item in events),
            "length_response_count": sum(
                item.finish_reason == "length" for item in events
            ),
            "latency_ms": latency_ms,
            "average_latency_ms": round(latency_ms / len(events)) if events else 0,
            "cache_hit_rate": (
                round(cached_tokens / prompt_tokens, 4)
                if prompt_tokens
                else 0.0
            ),
        }

    def snapshot(self, *, user_uuid: str) -> dict[str, Any]:
        """Return only usage attributed to the requesting browser UUID."""

        generated_at = datetime.now(timezone.utc)
        normalized_user = str(user_uuid or "").strip()
        if not self.enabled:
            return {
                "enabled": False,
                "provider": "",
                "scope": "current_backend_instance",
            }

        with self._lock:
            events = [
                item for item in self._events
                if item.user_uuid == normalized_user
            ]
            dropped_events = self._dropped_events

        local_today = generated_at.astimezone(self.display_timezone).date()
        today_events = [
            item for item in events
            if item.created_at.astimezone(self.display_timezone).date()
            == local_today
        ]

        grouped: OrderedDict[str, list[UsageEvent]] = OrderedDict()
        for event in events:
            grouped.setdefault(event.operation_id, []).append(event)
        recent_groups = list(grouped.values())[-self.recent_operations :]
        recent = []
        for group in reversed(recent_groups):
            first = group[0]
            last = group[-1]
            recent.append(
                {
                    "operation_id": first.operation_id,
                    "feature": first.feature,
                    "model": last.model,
                    "started_at": first.operation_started_at.isoformat(),
                    "completed_at": last.created_at.isoformat(),
                    **self._aggregate(group),
                }
            )

        started_local_date = self.started_at.astimezone(
            self.display_timezone
        ).date()
        return {
            "enabled": True,
            "provider": self.provider,
            "scope": "current_backend_instance",
            "timezone": self.timezone_name,
            "instance_started_at": self.started_at.isoformat(),
            "generated_at": generated_at.isoformat(),
            "today": {
                "date": local_today.isoformat(),
                "partial": started_local_date == local_today or dropped_events > 0,
                **self._aggregate(today_events),
            },
            "instance": self._aggregate(events),
            "recent_operations": recent,
        }
