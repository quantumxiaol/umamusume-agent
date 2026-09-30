"""Conservative estimates calibrated by actual provider usage, request-local only."""
from contextlib import contextmanager
from contextvars import ContextVar
import math

_observations = ContextVar("dialogue_prompt_observations", default=None)


def message_bytes(messages) -> int:
    return sum(len(str(message.get("content", "")).encode("utf-8")) + 12 for message in messages)


def estimate_tokens(messages, ratio=0.5) -> int:
    return math.ceil(message_bytes(messages) * ratio) + 32


def observe_prompt_tokens(value):
    observations = _observations.get()
    if observations is not None and isinstance(value, int) and value > 0:
        observations.append(value)


@contextmanager
def capture_prompt_usage():
    observations = []
    token = _observations.set(observations)
    try:
        yield observations
    finally:
        _observations.reset(token)


def calibrate(session, messages, observations):
    if observations:
        # First call represents the original request; repair prompts differ.
        measured = observations[0] / max(1, message_bytes(messages))
        session.token_ratio = min(1.5, max(0.1, measured * 1.1))
