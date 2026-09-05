"""Request diagnostics without storing prompt or reply text in logs."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator
from uuid import uuid4


logger = logging.getLogger(__name__)
_request_scope: ContextVar[dict[str, Any]] = ContextVar("llm_request_scope", default={})


@contextmanager
def llm_request_scope(**metadata: Any) -> Iterator[None]:
    token = _request_scope.set({**_request_scope.get(), **metadata})
    try:
        yield
    finally:
        _request_scope.reset(token)


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _RequestFingerprint:
    call_id: str
    messages: tuple[tuple[str, int], ...]
    parameters: str


class LLMRequestDiagnostics:
    """Compare application messages against an in-process request baseline.

    Fingerprints are never persisted or restored. A new process or an evicted
    thread has no baseline, so comparisons are unknown rather than failures.
    These fingerprints are not provider token/cache keys.
    """

    def __init__(self, *, enabled: bool = True, max_threads: int = 128):
        self.enabled = enabled
        self.max_threads = max(1, max_threads)
        self._previous: OrderedDict[tuple, _RequestFingerprint] = OrderedDict()

    def start(
        self,
        kwargs: dict[str, Any],
        *,
        attempt: int,
        retry_reason: str,
        length_retries: int,
    ) -> str | None:
        if not self.enabled:
            return None
        try:
            return self._start(
                kwargs, attempt=attempt, retry_reason=retry_reason,
                length_retries=length_retries,
            )
        except Exception as exc:
            # A diagnostic failure must not prevent the actual provider call.
            logger.warning("LLM diagnostics unavailable error_type=%s", type(exc).__name__)
            return None

    def _start(
        self,
        kwargs: dict[str, Any],
        *,
        attempt: int,
        retry_reason: str,
        length_retries: int,
    ) -> str:
        metadata = {"purpose": "json_completion", **_request_scope.get()}
        messages = kwargs["messages"]
        fingerprints = tuple(
            (_digest(message), len(str(message.get("content") or "")))
            for message in messages
        )
        parameters = {
            "model": kwargs["model"],
            "temperature": kwargs.get("temperature"),
            "max_tokens": kwargs.get("max_tokens"),
            "response_format": kwargs.get("response_format", {"type": "text"}),
            "thinking": kwargs.get("extra_body", {}).get("thinking", "provider_default"),
            "reasoning_effort": kwargs.get("reasoning_effort", "provider_default"),
        }
        parameter_hash = _digest(parameters)
        key = (
            metadata.get("session_id"), metadata.get("purpose"),
            metadata.get("actor_id"), kwargs["model"],
        )
        # Unscoped callers must not compare unrelated conversations.
        previous = self._previous.get(key) if metadata.get("session_id") else None
        common = 0
        if previous:
            for left, right in zip(previous.messages, fingerprints):
                if left != right:
                    break
                common += 1
        call_id = uuid4().hex
        record = {
            **metadata, "call_id": call_id, "attempt": attempt,
            "retry_reason": retry_reason, "length_retries": length_retries,
            **parameters, "parameter_hash": parameter_hash,
            "message_count": len(messages),
            "input_chars": sum(size for _, size in fingerprints),
            "messages_hash": _digest([digest for digest, _ in fingerprints]),
            "system_count": sum(m.get("role") == "system" for m in messages),
            "system_hash": _digest([m for m in messages if m.get("role") == "system"]),
            "previous_call_id": previous.call_id if previous else None,
            "common_prefix_messages": common if previous else None,
            "common_prefix_chars": sum(size for _, size in fingerprints[:common]) if previous else None,
            "previous_messages_preserved": common == len(previous.messages) if previous else None,
            "parameters_changed": previous.parameters != parameter_hash if previous else None,
        }
        logger.info("LLM request %s", json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        if metadata.get("session_id"):
            self._previous[key] = _RequestFingerprint(call_id, fingerprints, parameter_hash)
            self._previous.move_to_end(key)
            while len(self._previous) > self.max_threads:
                self._previous.popitem(last=False)
        return call_id

    def finish(self, call_id: str | None, response: Any, *, content: str, finish_reason: str) -> None:
        if call_id is None:
            return
        record = {
            "call_id": call_id,
            "request_id": getattr(response, "id", None),
            "finish_reason": finish_reason,
            "content_chars": len(content),
            "content_empty": not content.strip(),
            "content_whitespace_only": bool(content) and not content.strip(),
        }
        logger.info("LLM result %s", json.dumps(record, separators=(",", ":")))

    def error(self, call_id: str | None, exc: Exception) -> None:
        if call_id is not None:
            logger.warning("LLM request failed call_id=%s error_type=%s", call_id, type(exc).__name__)
