"""Shared chronological ordering for dialogue messages and memory events."""

from datetime import datetime, timezone
from typing import Any


_UNKNOWN_TIME = datetime.min.replace(tzinfo=timezone.utc)


def parse_history_timestamp(value: Any) -> datetime | None:
    """Normalize ISO timestamps without changing the stored/public value.

    Legacy writers used datetime.now().isoformat(), so naive values are treated
    as server-local time (including the offset for that date), not assumed UTC.
    An old file moved between server timezones needs its original offset added
    before migration; the missing timezone cannot be inferred from the file.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip()).astimezone(timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def history_timestamp_key(value: Any) -> datetime:
    """Unknown timestamps sort first, but must never establish a reset cutoff."""
    return parse_history_timestamp(value) or _UNKNOWN_TIME


def history_record_sort_key(record: dict[str, Any]) -> tuple[datetime, str, int]:
    """Use the same deterministic ties for API history, recovery and checkpoints.

    Within a timestamp/session, numeric indices order messages. Equal keys keep
    file/line traversal order via Python's stable sort. Cross-session ties cannot
    establish causality; retain the existing session-ID tie-breaker.
    """
    try:
        message_index = int(record.get("message_index") or 0)
    except (TypeError, ValueError, OverflowError):
        message_index = 0
    return (
        history_timestamp_key(record.get("timestamp")),
        str(record.get("session_id") or ""),
        message_index,
    )
