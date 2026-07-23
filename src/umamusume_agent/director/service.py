"""Director-mode session creation and bounded sequential orchestration."""

from __future__ import annotations

import re
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
from ..dialogue.protocol import StructuredReply
from .context import CharacterSceneContextBuilder, DirectorContextBuilder
from .history import (
    InvalidSceneHistory,
    create_scene_history_path,
    delete_scene_history,
    find_scene_history,
    iter_scene_history_files,
    load_scene_history,
    scene_history_summary,
)
from .models import (
    ActorInstance,
    CustomSceneDefinition,
    DirectorPlan,
    SceneEvent,
    SceneRecoverySnapshot,
    SceneState,
    SceneStatePatch,
    SceneTemplate,
)
from .runtime import DirectorRuntime
from .session import SceneSession
from .templates import SceneTemplateRepository
from .timeline import SceneTimeline


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

    def list_history(self, *, user_uuid: str, limit: int = 50) -> list[dict]:
        summaries = []
        for path in iter_scene_history_files(self.history_dir, user_uuid=user_uuid):
            try:
                history = load_scene_history(path)
            except InvalidSceneHistory:
                continue
            if history.user_uuid != user_uuid:
                continue
            summaries.append(scene_history_summary(history))
            if limit > 0 and len(summaries) >= limit:
                break
        return summaries

    async def restore_session(
        self,
        *,
        user_uuid: str,
        session_id: str,
    ) -> SceneSession:
        history = find_scene_history(
            self.history_dir,
            user_uuid=user_uuid,
            session_id=session_id,
        )
        participant_by_id = {
            item.actor.actor_id: item
            for item in history.participants
        }
        character_map = {}
        for participant in history.participants:
            actor = participant.actor
            if actor.actor_type not in {"umamusume", "npc"}:
                continue
            character = await self._load_history_character(actor)
            if character.id != actor.actor_id:
                raise InvalidSceneHistory(
                    f"角色配置与历史不一致: {actor.display_name}"
                )
            character_map[actor.actor_id] = character
        if not character_map:
            raise InvalidSceneHistory("历史中的参加角色已不可用")

        player = ActorRef.model_validate(history.player)
        director_thread = self.director_context_builder.create_thread(
            template=history.template,
            participants=history.participants,
            story_outline=history.story_outline,
        )
        actor_threads = {
            actor_id: self.character_context_builder.create_thread(
                character=character,
                template=history.template,
                participants=history.participants,
            )
            for actor_id, character in character_map.items()
        }
        session = SceneSession(
            session_id=history.session_id,
            user_uuid=history.user_uuid,
            template=history.template,
            player=player,
            participants=history.participants,
            characters=character_map,
            director_thread=director_thread,
            actor_threads=actor_threads,
            history_file=history.path,
            story_outline=history.story_outline,
            created_at=history.created_at,
            last_active_at=history.updated_at,
            write_scene_start=False,
        )

        prepared_actors: set[str] = set()
        for index, event in enumerate(history.events):
            if event.event_type == "director_plan":
                self.director_context_builder.append_turn(
                    session.director_thread,
                    timeline=session.timeline,
                )
                try:
                    plan = DirectorPlan.model_validate_json(event.content)
                except Exception as exc:
                    raise InvalidSceneHistory("历史中的导演计划无法解析") from exc
                self.director_context_builder.record_plan(
                    session.director_thread,
                    plan,
                )
                self._replay_checked(session, event)
                continue

            if event.event_type == "actor_directive":
                self._replay_checked(session, event)
                next_event = (
                    history.events[index + 1]
                    if index + 1 < len(history.events)
                    else None
                )
                actor_id = event.target_actor_ids[0] if event.target_actor_ids else ""
                if (
                    next_event is not None
                    and next_event.event_type == "character_reply"
                    and next_event.actor is not None
                    and next_event.actor.actor_id == actor_id
                    and actor_id in actor_threads
                ):
                    self.character_context_builder.build_reply_context(
                        actor_threads[actor_id],
                        actor=participant_by_id[actor_id].actor,
                        timeline=session.timeline,
                        intent=event.content,
                        target_actor_ids=next_event.target_actor_ids,
                    )
                    prepared_actors.add(actor_id)
                continue

            if event.event_type == "character_reply" and event.actor is not None:
                actor_id = event.actor.actor_id
                thread = actor_threads.get(actor_id)
                participant = participant_by_id.get(actor_id)
                if thread is None or participant is None:
                    raise InvalidSceneHistory("历史中的回复角色已不在参加者列表")
                if actor_id not in prepared_actors:
                    self.character_context_builder.build_reply_context(
                        thread,
                        actor=participant.actor,
                        timeline=session.timeline,
                        intent="结合已发生的公开事件自然回应。",
                        target_actor_ids=event.target_actor_ids,
                    )
                self.character_context_builder.record_reply(
                    thread,
                    StructuredReply(
                        action=event.action or "无",
                        dialogue=event.dialogue or event.content,
                    ),
                )
                stored = self._replay_checked(session, event)
                thread.last_seen_sequence = stored.sequence
                prepared_actors.discard(actor_id)
                continue

            self._replay_checked(session, event)

        session.turn_index = max(
            (event.turn_index for event in history.events),
            default=0,
        )
        session.last_active_at = history.updated_at
        return session

    async def _load_history_character(self, actor: ActorRef):
        candidates = [
            actor.display_name,
            actor.character_id or "",
            actor.actor_id.removeprefix("uma_"),
        ]
        last_error: Exception | None = None
        for candidate in dict.fromkeys(item for item in candidates if item):
            try:
                return await self.character_manager.load_character(candidate)
            except (FileNotFoundError, KeyError, ValueError) as exc:
                last_error = exc
        raise InvalidSceneHistory(
            f"无法加载历史角色: {actor.display_name}"
        ) from last_error

    async def recover_browser_snapshot(
        self,
        *,
        user_uuid: str,
        snapshot: SceneRecoverySnapshot,
    ) -> SceneSession:
        """Rebuild a viable scene from browser-owned public history."""

        if snapshot.schema_version != 1:
            raise InvalidSceneHistory("不支持的浏览器场景快照版本")
        if snapshot.user_uuid != user_uuid:
            raise InvalidSceneHistory("浏览器场景快照不属于当前用户")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", snapshot.session_id):
            raise InvalidSceneHistory("浏览器场景快照的 session_id 无效")
        if not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}",
            snapshot.template.template_id,
        ):
            raise InvalidSceneHistory("浏览器场景快照的场景 ID 无效")
        if (
            snapshot.player.actor_id != "player"
            or snapshot.player.actor_type != "trainer"
        ):
            raise InvalidSceneHistory("浏览器场景快照的训练员身份无效")
        if snapshot.turn_index < 0:
            raise InvalidSceneHistory("浏览器场景快照的轮数无效")
        if len(snapshot.events) > 5000:
            raise InvalidSceneHistory("浏览器场景快照事件过多")
        if len(snapshot.story_outline) > 20_000:
            raise InvalidSceneHistory("浏览器场景快照的剧情大纲过长")
        if len(snapshot.template.model_dump_json()) > 100_000:
            raise InvalidSceneHistory("浏览器场景快照的场景定义过大")

        participant_by_id: dict[str, ActorInstance] = {}
        for participant in snapshot.participants:
            actor = participant.actor
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", actor.actor_id):
                raise InvalidSceneHistory("浏览器场景快照的角色 ID 无效")
            if not actor.display_name.strip() or len(actor.display_name) > 200:
                raise InvalidSceneHistory("浏览器场景快照的角色名称无效")
            if actor.actor_id in participant_by_id:
                raise InvalidSceneHistory("浏览器场景快照包含重复角色")
            participant_by_id[actor.actor_id] = participant
        player_participant = participant_by_id.get(snapshot.player.actor_id)
        if (
            player_participant is None
            or player_participant.actor != snapshot.player
        ):
            raise InvalidSceneHistory("浏览器场景快照缺少训练员")

        character_participants = [
            item
            for item in snapshot.participants
            if item.actor.actor_type in {"umamusume", "npc"}
        ]
        if not character_participants:
            raise InvalidSceneHistory("浏览器场景快照没有参加角色")
        if len(character_participants) > self.max_participants:
            raise InvalidSceneHistory(
                f"导演模式最多恢复 {self.max_participants} 个角色"
            )
        if len(character_participants) + 1 != len(snapshot.participants):
            raise InvalidSceneHistory("浏览器场景快照包含不支持的参加者")

        character_map = {}
        for participant in character_participants:
            actor = participant.actor
            character = await self._load_history_character(actor)
            if character.id != actor.actor_id:
                raise InvalidSceneHistory(
                    f"角色配置与浏览器历史不一致: {actor.display_name}"
                )
            character_map[actor.actor_id] = character

        allowed_actor_ids = {
            snapshot.player.actor_id,
            "narrator",
            *character_map,
        }
        allowed_event_types = {
            "dialogue",
            "action",
            "narration",
            "scene_event",
            "scene_change",
            "character_reply",
            "actor_enter",
            "actor_leave",
        }
        narrator = self._narrator_actor()
        event_ids: set[str] = set()
        previous_sequence = -1
        total_text_length = 0
        for event in snapshot.events:
            if event.hidden or event.visible_to != "all":
                raise InvalidSceneHistory("浏览器场景快照不能包含隐藏事件")
            if event.event_type not in allowed_event_types:
                raise InvalidSceneHistory("浏览器场景快照包含内部事件")
            if not event.event_id or event.event_id in event_ids:
                raise InvalidSceneHistory("浏览器场景快照包含重复事件")
            event_ids.add(event.event_id)
            if event.sequence <= previous_sequence:
                raise InvalidSceneHistory("浏览器场景快照事件顺序无效")
            previous_sequence = event.sequence
            if event.turn_index < 0 or event.turn_index > snapshot.turn_index:
                raise InvalidSceneHistory("浏览器场景快照事件轮数无效")
            if event.actor is None:
                raise InvalidSceneHistory("浏览器场景快照事件缺少发言者")
            actor_id = event.actor.actor_id
            if actor_id not in allowed_actor_ids:
                raise InvalidSceneHistory("浏览器场景快照包含未知发言者")
            expected_actor = (
                narrator
                if actor_id == narrator.actor_id
                else participant_by_id[actor_id].actor
            )
            if event.actor != expected_actor:
                raise InvalidSceneHistory("浏览器场景快照的发言者信息不一致")
            if (
                event.event_type == "character_reply"
                and actor_id not in character_map
            ):
                raise InvalidSceneHistory("浏览器场景快照的角色回复身份无效")
            if (
                event.event_type in {"dialogue", "action"}
                and actor_id != snapshot.player.actor_id
            ):
                raise InvalidSceneHistory("浏览器场景快照的训练员事件身份无效")
            if (
                event.event_type in {"narration", "scene_event", "scene_change"}
                and actor_id != narrator.actor_id
            ):
                raise InvalidSceneHistory("浏览器场景快照的环境事件身份无效")
            if event.scene_patch is not None and event.event_type != "scene_change":
                raise InvalidSceneHistory("浏览器场景快照的环境变更类型无效")
            if any(
                target_id not in allowed_actor_ids
                for target_id in event.target_actor_ids
            ):
                raise InvalidSceneHistory("浏览器场景快照包含未知回应对象")
            event_text_length = (
                len(event.content) + len(event.action) + len(event.dialogue)
            )
            if event_text_length > 50_000:
                raise InvalidSceneHistory("浏览器场景快照单条事件过长")
            total_text_length += event_text_length
        if total_text_length > 2_000_000:
            raise InvalidSceneHistory("浏览器场景快照内容过大")
        recovered_timeline = SceneTimeline(
            initial_state=snapshot.template.initial_state,
            events=snapshot.events,
        )
        if recovered_timeline.state != snapshot.scene_state:
            raise InvalidSceneHistory("浏览器场景快照的环境状态不一致")

        director_thread = self.director_context_builder.create_thread(
            template=snapshot.template,
            participants=snapshot.participants,
            story_outline=snapshot.story_outline,
        )
        actor_threads = {
            actor_id: self.character_context_builder.create_thread(
                character=character,
                template=snapshot.template,
                participants=snapshot.participants,
            )
            for actor_id, character in character_map.items()
        }
        history_file = create_scene_history_path(
            self.history_dir,
            user_uuid=user_uuid,
            template_id=snapshot.template.template_id,
            session_id=snapshot.session_id,
            created_at=datetime.now(),
        )
        session = SceneSession(
            session_id=snapshot.session_id,
            user_uuid=user_uuid,
            template=snapshot.template,
            player=snapshot.player,
            participants=snapshot.participants,
            characters=character_map,
            director_thread=director_thread,
            actor_threads=actor_threads,
            history_file=history_file,
            story_outline=snapshot.story_outline,
            created_at=snapshot.created_at,
            last_active_at=snapshot.last_active_at,
        )
        for event in snapshot.events:
            session.append_event(event)
        session.turn_index = snapshot.turn_index
        session.touch()
        return session

    @staticmethod
    def _replay_checked(session: SceneSession, event: SceneEvent) -> SceneEvent:
        stored = session.replay_event(event)
        if event.sequence > 0 and stored.sequence != event.sequence:
            raise InvalidSceneHistory("导演场景历史事件顺序不连续")
        return stored

    def delete_history(self, *, user_uuid: str, session_id: str) -> None:
        history = find_scene_history(
            self.history_dir,
            user_uuid=user_uuid,
            session_id=session_id,
        )
        delete_scene_history(history, history_dir=self.history_dir)

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
