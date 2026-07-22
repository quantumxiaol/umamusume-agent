"""Data contracts for director-mode scenes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from ..dialogue.models import ActorRef


DIRECTOR_SCHEMA_VERSION = 1
SceneEventType = Literal[
    "dialogue",
    "action",
    "narration",
    "scene_event",
    "scene_change",
    "character_reply",
    "actor_enter",
    "actor_leave",
    "director_plan",
    "actor_directive",
]


class SceneState(BaseModel):
    location: str
    sub_location: str | None = None
    time: str = ""
    weather: str = ""
    lighting: str = ""
    atmosphere: str = ""
    ambient_sound: str = ""
    props: list[str] = Field(default_factory=list)


class SceneStatePatch(BaseModel):
    location: str | None = None
    sub_location: str | None = None
    time: str | None = None
    weather: str | None = None
    lighting: str | None = None
    atmosphere: str | None = None
    ambient_sound: str | None = None
    props: list[str] | None = None

    def updates(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class SceneTemplate(BaseModel):
    template_id: str
    name: str
    description: str = ""
    initial_state: SceneState
    opening_narration: str = ""
    tags: list[str] = Field(default_factory=list)


class ActorInstance(BaseModel):
    actor: ActorRef
    position: str = ""
    status: str = "present"
    present: bool = True


class SceneEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: uuid4().hex)
    sequence: int = 0
    turn_index: int = 0
    event_type: SceneEventType
    actor: ActorRef | None = None
    target_actor_ids: list[str] = Field(default_factory=list)
    visible_to: Literal["all"] | list[str] = "all"
    content: str = ""
    action: str = ""
    dialogue: str = ""
    scene_patch: SceneStatePatch | None = None
    hidden: bool = False
    created_at: datetime = Field(default_factory=datetime.now)


class DirectorSpeakerPlan(BaseModel):
    actor_id: str
    target_actor_ids: list[str] = Field(default_factory=list)
    intent: str


class DirectorPlan(BaseModel):
    schema_version: int = DIRECTOR_SCHEMA_VERSION
    scene_patch: SceneStatePatch = Field(default_factory=SceneStatePatch)
    narration: str = ""
    speakers: list[DirectorSpeakerPlan] = Field(default_factory=list)

