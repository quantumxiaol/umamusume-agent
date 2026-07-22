"""Application service for the existing single-character dialogue flow."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .context import LegacyDialogueContextBuilder
from .models import (
    EVENT_SCHEMA_VERSION,
    ActorRef,
    DialogueEventType,
    DialogueInputEvent,
    DialogueTurnResult,
    actor_from_character,
    default_player_actor,
)
from .protocol import structured_reply_message
from .runtime import CharacterRuntime


class DialogueService:
    """Execute one complete legacy dialogue turn without HTTP concerns."""

    def __init__(
        self,
        *,
        runtime: CharacterRuntime,
        context_builder: LegacyDialogueContextBuilder,
    ):
        self.runtime = runtime
        self.context_builder = context_builder

    async def execute_turn(
        self,
        *,
        session: Any,
        message: str,
        text_only: bool = False,
        speaker: ActorRef | None = None,
        event_type: DialogueEventType | None = None,
        target_actor_ids: list[str] | None = None,
        context_events: Sequence[DialogueInputEvent] | None = None,
    ) -> DialogueTurnResult:
        queued_events = list(context_events or [])
        has_event_metadata = any(
            value is not None
            for value in (speaker, event_type, target_actor_ids)
        ) or bool(queued_events)

        for queued_event in queued_events:
            queued_speaker = queued_event.speaker or default_player_actor()
            queued_event_type: DialogueEventType = (
                queued_event.event_type or "dialogue"
            )
            queued_targets = list(
                queued_event.target_actor_ids
                if queued_event.target_actor_ids is not None
                else [session.character.id]
            )
            session.add_message(
                "user",
                queued_event.content,
                actor=queued_speaker.model_dump(),
                event_type=queued_event_type,
                target_actor_ids=queued_targets,
                event_schema_version=EVENT_SCHEMA_VERSION,
            )

        resolved_speaker = speaker or default_player_actor()
        resolved_event_type: DialogueEventType = event_type or "dialogue"
        resolved_targets = list(
            target_actor_ids
            if target_actor_ids is not None
            else ([session.character.id] if has_event_metadata else [])
        )

        input_metadata = {}
        if has_event_metadata:
            input_metadata = {
                "actor": resolved_speaker.model_dump(),
                "event_type": resolved_event_type,
                "target_actor_ids": resolved_targets,
                "event_schema_version": EVENT_SCHEMA_VERSION,
            }
        session.add_message("user", message, **input_metadata)

        context = self.context_builder.build(
            character=session.character,
            history=session.history,
            text_only=text_only,
        )
        reply = await self.runtime.generate_reply(context)

        reply_actor = (
            actor_from_character(session.character)
            if has_event_metadata
            else None
        )
        reply_targets = (
            []
            if resolved_event_type in {"scene_event", "narration"}
            else [resolved_speaker.actor_id]
        )
        reply_metadata = {}
        if has_event_metadata:
            reply_metadata = {
                "actor": reply_actor.model_dump(),
                "event_type": "dialogue",
                "target_actor_ids": reply_targets,
                "event_schema_version": EVENT_SCHEMA_VERSION,
            }
        session.add_message(
            "assistant",
            reply.dialogue,
            action=reply.action,
            dialogue=reply.dialogue,
            source_format=reply.source_format,
            schema_version=reply.schema_version,
            **reply_metadata,
        )

        return DialogueTurnResult(
            reply=reply,
            message=structured_reply_message(
                reply,
                actor=reply_actor,
                event_type="dialogue" if has_event_metadata else None,
                target_actor_ids=reply_targets,
                event_schema_version=EVENT_SCHEMA_VERSION,
            ),
            speaker=reply_actor,
            event_type="dialogue" if has_event_metadata else None,
            target_actor_ids=tuple(reply_targets),
        )
