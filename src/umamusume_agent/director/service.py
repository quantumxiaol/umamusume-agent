"""Director-mode session creation and bounded sequential orchestration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from ..character import CharacterManager
from ..dialogue.models import (
    ActorRef,
    DialogueInputEvent,
    actor_from_character,
    default_player_actor,
)
from ..dialogue.runtime import CharacterRuntime
from .context import CharacterSceneContextBuilder, DirectorContextBuilder
from .history import create_scene_history_path
from .models import (
    ActorInstance,
    CustomSceneDefinition,
    SceneEvent,
    SceneState,
    SceneStatePatch,
    SceneTemplate,
)
from .runtime import DirectorRuntime
from .session import SceneSession
from .templates import SceneTemplateRepository


class DirectorService:
    def __init__(
        self,
        *,
        character_manager: CharacterManager,
        character_runtime: CharacterRuntime,
        director_runtime: DirectorRuntime,
        template_repository: SceneTemplateRepository,
        director_context_builder: DirectorContextBuilder,
        character_context_builder: CharacterSceneContextBuilder,
        history_dir: Path,
        max_participants: int,
    ):
        self.character_manager = character_manager
        self.character_runtime = character_runtime
        self.director_runtime = director_runtime
        self.template_repository = template_repository
        self.director_context_builder = director_context_builder
        self.character_context_builder = character_context_builder
        self.history_dir = history_dir
        self.max_participants = max(1, max_participants)

    async def create_session(
        self,
        *,
        user_uuid: str,
        template_id: str | None,
        character_names: list[str],
        custom_scene: CustomSceneDefinition | None = None,
        story_outline: str = "",
    ) -> SceneSession:
        normalized_names = [name.strip() for name in character_names if name.strip()]
        if not normalized_names:
            raise ValueError("至少选择一个角色")
        if len(normalized_names) > self.max_participants:
            raise ValueError(f"导演模式最多选择 {self.max_participants} 个角色")

        characters = []
        seen_ids: set[str] = set()
        for name in normalized_names:
            character = await self.character_manager.load_character(name)
            if character.id in seen_ids:
                continue
            seen_ids.add(character.id)
            characters.append(character)
        if not characters:
            raise ValueError("没有可用角色")

        template = self._resolve_scene_template(
            template_id=template_id,
            custom_scene=custom_scene,
        )
        player = default_player_actor()
        participants = [ActorInstance(actor=player, position="场景内")]
        character_map = {}
        for character in characters:
            actor = actor_from_character(character)
            participants.append(ActorInstance(actor=actor, position="场景内"))
            character_map[actor.actor_id] = character

        director_thread = self.director_context_builder.create_thread(
            template=template,
            participants=participants,
            story_outline=story_outline,
        )
        actor_threads = {
            character.id: self.character_context_builder.create_thread(
                character=character,
                template=template,
                participants=participants,
            )
            for character in characters
        }
        session_id = uuid4().hex
        created_at = datetime.now()
        history_file = create_scene_history_path(
            self.history_dir,
            user_uuid=user_uuid,
            template_id=template.template_id,
            session_id=session_id,
            created_at=created_at,
        )
        session = SceneSession(
            session_id=session_id,
            user_uuid=user_uuid,
            template=template,
            player=player,
            participants=participants,
            characters=character_map,
            director_thread=director_thread,
            actor_threads=actor_threads,
            history_file=history_file,
            story_outline=story_outline,
        )
        if template.opening_narration.strip():
            session.append_event(
                SceneEvent(
                    event_type="narration",
                    actor=self._narrator_actor(),
                    content=template.opening_narration.strip(),
                )
            )
        return session

    def _resolve_scene_template(
        self,
        *,
        template_id: str | None,
        custom_scene: CustomSceneDefinition | None,
    ) -> SceneTemplate:
        normalized_template_id = (template_id or "").strip()
        if custom_scene is not None and normalized_template_id:
            raise ValueError("场景预设和自定义场景只能选择一种")
        if custom_scene is None:
            if not normalized_template_id:
                raise ValueError("请选择场景预设或填写自定义场景")
            return self.template_repository.get(normalized_template_id)

        state = custom_scene.initial_state
        location = state.location.strip()
        if not location:
            raise ValueError("自定义场景必须填写地点")
        normalized_state = SceneState(
            location=location,
            sub_location=(state.sub_location or "").strip() or None,
            time=state.time.strip(),
            weather=state.weather.strip(),
            lighting=state.lighting.strip(),
            atmosphere=state.atmosphere.strip(),
            ambient_sound=state.ambient_sound.strip(),
            props=[item.strip() for item in state.props if item.strip()],
        )
        tags = [item.strip() for item in custom_scene.tags if item.strip()]
        return SceneTemplate(
            template_id=f"custom_{uuid4().hex[:12]}",
            name=custom_scene.name.strip() or "自定义场景",
            description=custom_scene.description.strip(),
            initial_state=normalized_state,
            opening_narration=custom_scene.opening_narration.strip(),
            tags=list(dict.fromkeys(["自定义", *tags])),
        )

    @staticmethod
    def _narrator_actor() -> ActorRef:
        return ActorRef(
            actor_id="narrator",
            actor_type="narrator",
            display_name="环境",
            role_in_scene="environment",
        )

    @staticmethod
    def _director_actor() -> ActorRef:
        return ActorRef(
            actor_id="director",
            actor_type="director",
            display_name="导演",
            role_in_scene="director",
        )

    def _append_input_event(
        self,
        session: SceneSession,
        item: DialogueInputEvent,
    ) -> SceneEvent:
        content = item.content.strip()
        if not content:
            raise ValueError("场景事件内容不能为空")
        event_type = item.event_type or "dialogue"
        actor = item.speaker
        if actor is None:
            actor = (
                self._narrator_actor()
                if event_type in {"scene_event", "narration"}
                else session.player
            )
        if actor.actor_id not in {session.player.actor_id, "narrator"}:
            raise ValueError("导演模式 V1 只允许训练员或环境提交输入事件")
        targets = [
            target
            for target in (item.target_actor_ids or session.character_actor_ids)
            if target in session.character_actor_ids
        ]
        return session.append_event(
            SceneEvent(
                turn_index=session.turn_index,
                event_type=event_type,
                actor=actor,
                target_actor_ids=list(dict.fromkeys(targets)),
                content=content,
            )
        )

    @staticmethod
    def _scene_patch_content(patch: SceneStatePatch) -> str:
        updates = patch.updates()
        labels = {
            "location": "地点",
            "sub_location": "位置",
            "time": "时间",
            "weather": "天气",
            "lighting": "光线",
            "atmosphere": "氛围",
            "ambient_sound": "环境声",
            "props": "场景物品",
        }
        parts = []
        for key, value in updates.items():
            rendered = "、".join(value) if isinstance(value, list) else str(value)
            parts.append(f"{labels.get(key, key)}变为{rendered}")
        return "；".join(parts)

    async def stream_turn(
        self,
        session: SceneSession,
        input_events: list[DialogueInputEvent],
    ) -> AsyncIterator[SceneEvent]:
        if not input_events:
            raise ValueError("至少需要一个输入事件")

        async with session.lock:
            session.turn_index += 1
            fallback_actor_ids: list[str] = []
            for item in input_events:
                event = self._append_input_event(session, item)
                fallback_actor_ids.extend(event.target_actor_ids)
                yield event

            self.director_context_builder.append_turn(
                session.director_thread,
                timeline=session.timeline,
            )
            plan = await self.director_runtime.generate_plan(
                session.director_thread.snapshot(),
                allowed_actor_ids=set(session.character_actor_ids),
                allowed_target_ids={
                    session.player.actor_id,
                    *session.character_actor_ids,
                },
                fallback_actor_ids=list(dict.fromkeys(fallback_actor_ids)),
            )
            self.director_context_builder.record_plan(
                session.director_thread,
                plan,
            )
            session.append_event(
                SceneEvent(
                    turn_index=session.turn_index,
                    event_type="director_plan",
                    actor=self._director_actor(),
                    content=plan.model_dump_json(exclude_none=True),
                    visible_to=[],
                    hidden=True,
                )
            )

            if plan.scene_patch.updates():
                scene_event = session.append_event(
                    SceneEvent(
                        turn_index=session.turn_index,
                        event_type="scene_change",
                        actor=self._narrator_actor(),
                        content=self._scene_patch_content(plan.scene_patch),
                        scene_patch=plan.scene_patch,
                    )
                )
                yield scene_event

            if plan.narration.strip():
                narration_event = session.append_event(
                    SceneEvent(
                        turn_index=session.turn_index,
                        event_type="narration",
                        actor=self._narrator_actor(),
                        content=plan.narration.strip(),
                    )
                )
                yield narration_event

            participant_by_id = {
                item.actor.actor_id: item
                for item in session.participants
            }
            for speaker_plan in plan.speakers:
                character = session.characters[speaker_plan.actor_id]
                actor = participant_by_id[speaker_plan.actor_id].actor
                thread = session.actor_threads[speaker_plan.actor_id]
                session.append_event(
                    SceneEvent(
                        turn_index=session.turn_index,
                        event_type="actor_directive",
                        actor=self._director_actor(),
                        target_actor_ids=[speaker_plan.actor_id],
                        visible_to=[speaker_plan.actor_id],
                        content=speaker_plan.intent,
                        hidden=True,
                    )
                )
                context = self.character_context_builder.build_reply_context(
                    thread,
                    actor=actor,
                    timeline=session.timeline,
                    intent=speaker_plan.intent,
                    target_actor_ids=speaker_plan.target_actor_ids,
                )
                reply = await self.character_runtime.generate_reply(context)
                self.character_context_builder.record_reply(thread, reply)
                reply_event = session.append_event(
                    SceneEvent(
                        turn_index=session.turn_index,
                        event_type="character_reply",
                        actor=actor,
                        target_actor_ids=speaker_plan.target_actor_ids,
                        action=reply.action,
                        dialogue=reply.dialogue,
                        content=reply.dialogue,
                    )
                )
                # The actor's own reply is already the assistant tail in its
                # prompt thread, so do not project it again as a user event.
                thread.last_seen_sequence = reply_event.sequence
                yield reply_event

    async def execute_turn(
        self,
        session: SceneSession,
        input_events: list[DialogueInputEvent],
    ) -> list[SceneEvent]:
        return [
            event
            async for event in self.stream_turn(session, input_events)
        ]
