"""Data contracts for director-mode scenes."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
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


class CustomSceneDefinition(BaseModel):
    """User-authored opening environment; it is not persisted as a preset."""

    name: str = "自定义场景"
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
    source_format: str = ""
    # Persisted explicitly in JSONL, omitted from public events/snapshots.
    model_content: str = Field(default="", exclude=True)
    revision: int = 0
    scene_patch: SceneStatePatch | None = None
    hidden: bool = False
    created_at: datetime = Field(default_factory=datetime.now)


class DirectorSpeakerPlan(BaseModel):
    actor_id: str
    target_actor_ids: list[str] = Field(default_factory=list)
    intent: str


class DirectorMoveAction(BaseModel):
    type: Literal["actor.move_to"]
    actor_id: str
    anchor_id: str
    slot_id: str | None = None


class DirectorFaceAction(BaseModel):
    type: Literal["actor.face"]
    actor_id: str
    facing: Literal["down", "left", "right", "up"]


class DirectorFaceActorAction(BaseModel):
    type: Literal["actor.face_actor"]
    actor_id: str
    target_actor_id: str


class DirectorApproachAction(BaseModel):
    type: Literal["actor.approach"]
    actor_id: str
    target_actor_id: str
    distance_tiles: int = Field(default=1, ge=1, le=4)


class DirectorFollowAction(BaseModel):
    type: Literal["actor.follow"]
    actor_id: str
    target_actor_id: str
    distance_tiles: int = Field(default=2, ge=1, le=4)


class DirectorStopFollowAction(BaseModel):
    type: Literal["actor.stop_follow"]
    actor_id: str


class DirectorStopAction(BaseModel):
    type: Literal["actor.stop"]
    actor_id: str


DirectorStageAction = Annotated[
    DirectorMoveAction
    | DirectorFaceAction
    | DirectorFaceActorAction
    | DirectorApproachAction
    | DirectorFollowAction
    | DirectorStopFollowAction
    | DirectorStopAction,
    Field(discriminator="type"),
]


class DirectorPlan(BaseModel):
    schema_version: int = DIRECTOR_SCHEMA_VERSION
    scene_patch: SceneStatePatch = Field(default_factory=SceneStatePatch)
    narration: str = ""
    speakers: list[DirectorSpeakerPlan] = Field(default_factory=list)
    stage_actions: list[DirectorStageAction] = Field(default_factory=list)
    model_content: str = Field(default="", exclude=True)


class SceneMemoryCheckpoint(BaseModel):
    """One shared public memory, bound to an immutable archived prefix."""

    schema_version: Literal[1] = 1
    checkpoint_id: str = Field(default_factory=lambda: uuid4().hex, max_length=64)
    revision: int = Field(default=1, ge=1)
    session_id: str
    user_uuid: str
    covered_events: int = Field(ge=1)
    covered_event_id: str
    trigger_event_count: int = Field(ge=1)
    trigger_event_id: str
    trigger_turn_index: int = Field(ge=0)
    source_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    prompt_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    summary: str = Field(min_length=1, max_length=2_000_000)
    reply_counts: dict[str, int] = Field(default_factory=dict)
    token_ratios: dict[str, float] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)


class SceneRecoverySnapshot(BaseModel):
    """Public browser-owned snapshot used after an ephemeral backend reset."""

    schema_version: int = DIRECTOR_SCHEMA_VERSION
    session_id: str
    user_uuid: str
    template: SceneTemplate
    story_outline: str = ""
    player: ActorRef
    participants: list[ActorInstance]
    scene_state: SceneState
    turn_index: int = 0
    events: list[SceneEvent] = Field(default_factory=list)
    created_at: datetime
    last_active_at: datetime
    # Optional for old clients. Invalid/stale memory never invalidates the archive.
    context_checkpoint: dict[str, Any] | None = None
    context_checkpoints: list[dict[str, Any]] = Field(default_factory=list)
