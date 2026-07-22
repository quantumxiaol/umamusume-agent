"""Internal dialogue service models that are independent from FastAPI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from .protocol import StructuredReply


@dataclass(frozen=True)
class CharacterReplyContext:
    """Fully rendered model messages for one character reply."""

    messages: Sequence[dict[str, Any]]


@dataclass(frozen=True)
class DialogueTurnResult:
    """Result of one legacy user-to-character turn."""

    reply: StructuredReply
    message: dict[str, Any]

    def to_api_dict(self) -> dict[str, Any]:
        return {
            "action": self.reply.action,
            "dialogue": self.reply.dialogue,
            "message": self.message,
        }

