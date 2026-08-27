"""Turns Director output into data for a browser-owned StageRuntime."""

from __future__ import annotations

from dataclasses import dataclass

from ..dialogue.models import DialogueInputEvent
from ..director.models import SceneEvent
from ..director.service import DirectorService
from ..director.session import SceneSession
from .models import (
    AgentStageAction,
    LiveStageContext,
    StageActorBinding,
    StageDialogueAction,
)


@dataclass(frozen=True)
class StageTurnResult:
    events: list[SceneEvent]
    actions: list[AgentStageAction]


class StageSceneService:
    def __init__(self, director_service: DirectorService):
        self.director_service = director_service

    async def execute_turn(
        self,
        session: SceneSession,
        input_events: list[DialogueInputEvent],
        *,
        live_stage: LiveStageContext,
        actor_bindings: list[StageActorBinding],
    ) -> StageTurnResult:
        binding_by_agent = self._validate_bindings(
            session,
            live_stage=live_stage,
            actor_bindings=actor_bindings,
        )
        stage_context = live_stage.model_dump(mode="json")
        stage_context["actor_bindings"] = [
            binding.model_dump(mode="json") for binding in actor_bindings
        ]
        player_stage_ids = [
            actor.actor_id
            for actor in live_stage.actors
            if actor.control_owner == "player"
        ]
        if len(player_stage_ids) != 1:
            raise ValueError("单角色 Stage 当前要求 live_stage 中恰好有一个 player")
        stage_context["logical_actor_bindings"] = [
            {
                "logical_actor_id": session.player.actor_id,
                "stage_actor_id": player_stage_ids[0],
            },
            *[
                {
                    "logical_actor_id": binding.agent_actor_id,
                    "stage_actor_id": binding.stage_actor_id,
                }
                for binding in actor_bindings
            ],
        ]
        if len(session.character_actor_ids) == 1:
            events, director_actions = await (
                self.director_service.execute_single_character_stage_turn(
                    session,
                    input_events,
                    stage_context=stage_context,
                    allowed_stage_actor_ids={
                        binding.stage_actor_id for binding in actor_bindings
                    },
                )
            )
            actions = list(director_actions)
        else:
            events, director_actions = await self.director_service.execute_stage_turn(
                session,
                input_events,
                stage_context=stage_context,
                allowed_stage_actor_ids={
                    binding.stage_actor_id for binding in actor_bindings
                },
            )
            actions = list(director_actions)
        for event in events:
            if (
                event.event_type != "character_reply"
                or event.actor is None
                or not event.dialogue.strip()
            ):
                continue
            binding = binding_by_agent.get(event.actor.actor_id)
            if binding is None:
                continue
            actions.append(
                StageDialogueAction(
                    type="dialogue.say",
                    actor_id=binding.stage_actor_id,
                    text=event.dialogue.strip(),
                )
            )
        return StageTurnResult(events=events, actions=actions)

    @staticmethod
    def _validate_bindings(
        session: SceneSession,
        *,
        live_stage: LiveStageContext,
        actor_bindings: list[StageActorBinding],
    ) -> dict[str, StageActorBinding]:
        binding_by_agent: dict[str, StageActorBinding] = {}
        seen_stage_ids: set[str] = set()
        stage_actors = {
            actor.actor_id: actor
            for actor in live_stage.actors
            if actor.control_owner == "agent"
        }
        for binding in actor_bindings:
            if binding.agent_actor_id in binding_by_agent:
                raise ValueError(f"重复 Agent 角色绑定: {binding.agent_actor_id}")
            if binding.stage_actor_id in seen_stage_ids:
                raise ValueError(f"重复 Stage 角色绑定: {binding.stage_actor_id}")
            if binding.agent_actor_id not in session.characters:
                raise ValueError(f"角色不属于当前会话: {binding.agent_actor_id}")
            if binding.stage_actor_id not in stage_actors:
                raise ValueError(
                    f"Stage 角色不存在或不受 agent 控制: {binding.stage_actor_id}"
                )
            binding_by_agent[binding.agent_actor_id] = binding
            seen_stage_ids.add(binding.stage_actor_id)

        missing = set(session.character_actor_ids) - set(binding_by_agent)
        if missing:
            raise ValueError(f"缺少 Stage 角色绑定: {', '.join(sorted(missing))}")
        return binding_by_agent
