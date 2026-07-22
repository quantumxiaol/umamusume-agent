"""Internal dialogue service models that are independent from FastAPI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Sequence

from pydantic import BaseModel

from .protocol import StructuredReply


EVENT_SCHEMA_VERSION = 1
ActorType = Literal["trainer", "umamusume", "npc", "narrator", "director"]
DialogueEventType = Literal["dialogue", "action", "narration", "scene_event"]


class ActorRef(BaseModel):
    """Identity of a speaker inside the story world."""

    actor_id: str
    actor_type: ActorType
    display_name: str
    character_id: str | None = None
    role_in_scene: str | None = None


class DialogueInputEvent(BaseModel):
    """One user-authored event added before a character reply is generated."""

    content: str
    speaker: ActorRef | None = None
    event_type: DialogueEventType | None = None
    target_actor_ids: list[str] | None = None


def default_player_actor() -> ActorRef:
    return ActorRef(
        actor_id="player",
        actor_type="trainer",
        display_name="训练员",
        role_in_scene="trainer",
    )


def actor_from_character(character: Any) -> ActorRef:
    return ActorRef(
        actor_id=character.id,
        actor_type="umamusume",
        display_name=character.name_zh,
        character_id=character.id,
    )


@dataclass(frozen=True)
class CharacterReplyContext:
    """Fully rendered model messages for one character reply."""

    messages: Sequence[dict[str, Any]]


@dataclass(frozen=True)
class DialogueTurnResult:
    """Result of one legacy user-to-character turn."""

    reply: StructuredReply
    message: dict[str, Any]
    speaker: ActorRef | None = None
    event_type: DialogueEventType | None = None
    target_actor_ids: tuple[str, ...] = ()

    def to_api_dict(self) -> dict[str, Any]:
        payload = {
            "action": self.reply.action,
            "dialogue": self.reply.dialogue,
            "message": self.message,
        }
        if self.speaker is not None:
            payload.update(
                {
                    "event_schema_version": EVENT_SCHEMA_VERSION,
                    "speaker": self.speaker.model_dump(),
                    "event_type": self.event_type or "dialogue",
                    "target_actor_ids": list(self.target_actor_ids),
                }
            )
        return payload
