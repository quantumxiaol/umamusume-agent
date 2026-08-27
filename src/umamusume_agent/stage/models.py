"""Public request and response contracts for the browser-owned Stage runtime."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from ..dialogue.models import DialogueInputEvent
from ..director.models import CustomSceneDefinition


class StageActorBinding(BaseModel):
    agent_actor_id: str
    stage_actor_id: str


class LiveStageActor(BaseModel):
    actor_id: str
    tile: dict[str, int]
    facing: Literal["down", "left", "right", "up"]
    locomotion: Literal["stand", "walk", "idle"]
    control_owner: Literal["player", "agent", "script", "system"]
    following_actor_id: str | None = None
    follow_distance_tiles: int | None = Field(default=None, ge=1, le=4)
    nearby_anchors: list[str] = Field(default_factory=list)


class LiveStageAnchor(BaseModel):
    id: str
    display_name: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    available_slots: list[str] = Field(default_factory=list)


class LiveStageContext(BaseModel):
    schema_version: Literal["live_stage.v1"]
    stage_id: str
    scene_id: str
    actors: list[LiveStageActor]
    anchors: list[LiveStageAnchor]
    activity_paths: list[dict] = Field(default_factory=list)
    portals: list[dict] = Field(default_factory=list)


class CreateStageSessionRequest(BaseModel):
    template_id: str | None = None
    custom_scene: CustomSceneDefinition | None = None
    story_outline: str = ""
    character_names: list[str]
    user_uuid: str | None = None


class StageTurnRequest(BaseModel):
    session_id: str
    user_uuid: str
    events: list[DialogueInputEvent]
    actor_bindings: list[StageActorBinding]
    live_stage: LiveStageContext
    generate_voice: bool = False


class StageMoveAction(BaseModel):
    type: Literal["actor.move_to"]
    actor_id: str
    anchor_id: str
    slot_id: str | None = None


class StageFaceAction(BaseModel):
    type: Literal["actor.face"]
    actor_id: str
    facing: Literal["down", "left", "right", "up"]


class StageFaceActorAction(BaseModel):
    type: Literal["actor.face_actor"]
    actor_id: str
    target_actor_id: str


class StageApproachAction(BaseModel):
    type: Literal["actor.approach"]
    actor_id: str
    target_actor_id: str
    distance_tiles: int = Field(default=1, ge=1, le=4)


class StageFollowAction(BaseModel):
    type: Literal["actor.follow"]
    actor_id: str
    target_actor_id: str
    distance_tiles: int = Field(default=2, ge=1, le=4)


class StageStopFollowAction(BaseModel):
    type: Literal["actor.stop_follow"]
    actor_id: str


class StageStopAction(BaseModel):
    type: Literal["actor.stop"]
    actor_id: str


class StageDialogueAction(BaseModel):
    type: Literal["dialogue.say"]
    actor_id: str
    text: str


AgentStageAction = Annotated[
    StageMoveAction
    | StageFaceAction
    | StageFaceActorAction
    | StageApproachAction
    | StageFollowAction
    | StageStopFollowAction
    | StageStopAction
    | StageDialogueAction,
    Field(discriminator="type"),
]
