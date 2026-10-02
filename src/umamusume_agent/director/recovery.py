"""Validated scene reconstruction from server JSONL or browser public history.

This component has no dependency on online turn orchestration or HTTP routes.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from ..character import CharacterManager
from ..dialogue.models import ActorRef
from ..dialogue.protocol import StructuredReply
from ..input_limits import validate_history_size
from .context import CharacterSceneContextBuilder, DirectorContextBuilder
from .history import InvalidSceneHistory, create_scene_history_path, find_scene_history
from .models import ActorInstance, DirectorPlan, SceneEvent, SceneRecoverySnapshot
from .session import SceneSession
from .timeline import SceneTimeline
from .memory import apply_memory_threads, checkpoint_boundary, restore_memory

logger = logging.getLogger(__name__)


class SceneRecovery:
    def __init__(
        self, *, character_manager: CharacterManager,
        director_context_builder: DirectorContextBuilder,
        character_context_builder: CharacterSceneContextBuilder,
        history_dir: Path, max_participants: int, narrator: ActorRef,
    ):
        self.character_manager = character_manager
        self.director_context_builder = director_context_builder
        self.character_context_builder = character_context_builder
        self.history_dir = history_dir
        self.max_participants = max_participants
        self.narrator = narrator

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

        restore_memory(session, history.events, history.active_checkpoint, history.checkpoints,
                       self.director_context_builder.settings)
        memory_boundary = checkpoint_boundary(history.events, session.checkpoint) if session.checkpoint else 0
        memory_applied = session.checkpoint is None
        prepared_actors: set[str] = set()
        for index, event in enumerate(history.events):
            if session.checkpoint and event.sequence <= memory_boundary:
                self._replay_checked(session, event)
                continue
            if not memory_applied:
                apply_memory_threads(session, session.checkpoint, history.events)
                actor_threads = session.actor_threads
                memory_applied = True
            if event.event_type == "director_plan":
                self.director_context_builder.append_turn(
                    session.director_thread,
                    timeline=session.timeline,
                )
                try:
                    plan = DirectorPlan.model_validate_json(event.content)
                    plan.model_content = event.model_content
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
                        model_content=event.model_content,
                    ),
                )
                stored = self._replay_checked(session, event)
                thread.last_seen_sequence = stored.sequence
                prepared_actors.discard(actor_id)
                continue

            self._replay_checked(session, event)

        if not memory_applied:
            apply_memory_threads(session, session.checkpoint, history.events)
        session.turn_index = max(
            (event.turn_index for event in history.events),
            default=0,
        )
        session.last_active_at = history.updated_at
        logger.info(
            "Scene context rebuilt source=server_history session_id=%s turn_index=%s director_messages=%s",
            session.session_id, session.turn_index, len(session.director_thread.messages),
        )
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

        try:
            validate_history_size(snapshot.events, snapshot.context_checkpoint, snapshot.context_checkpoints)
        except ValueError as exc:
            raise InvalidSceneHistory(str(exc)) from exc
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
        narrator = self.narrator
        event_ids: set[str] = set()
        previous_sequence = -1
        total_text_length = 0
        for event in snapshot.events:
            if event.hidden or event.visible_to != "all" or event.model_content:
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
        restore_memory(session, session.timeline.events, snapshot.context_checkpoint,
                       snapshot.context_checkpoints, self.director_context_builder.settings)
        if session.checkpoints:
            for checkpoint in session.checkpoints:
                session.history.append({"event": "scene_checkpoint", "checkpoint": checkpoint.model_dump(mode="json")})
            session.history.append({"event": "scene_checkpoint", "checkpoint": (
                session.checkpoint.model_dump(mode="json") if session.checkpoint else None
            )}, strict=True)
        if session.checkpoint:
            # Replay the suffix after the checkpoint's trigger as well, so new
            # responses since compaction and manual regeneration remain usable.
            session = await self.restore_session(user_uuid=user_uuid, session_id=session.session_id)
        logger.info(
            "Scene context rebuilt source=browser_snapshot session_id=%s turn_index=%s public_events=%s",
            session.session_id, session.turn_index, len(snapshot.events),
        )
        return session

    @staticmethod
    def _replay_checked(session: SceneSession, event: SceneEvent) -> SceneEvent:
        stored = session.replay_event(event)
        if event.sequence > 0 and stored.sequence != event.sequence:
            raise InvalidSceneHistory("导演场景历史事件顺序不连续")
        return stored
