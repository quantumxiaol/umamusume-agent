"""Mutable state for one isolated director-mode scene."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from ..character import CharacterConfig
from ..dialogue.models import ActorRef
from .context import PromptThread
from .history import SceneHistoryWriter
from .models import ActorInstance, SceneEvent, SceneTemplate
from .timeline import SceneTimeline


class SceneSession:
    def __init__(
        self,
        *,
        session_id: str,
        user_uuid: str,
        template: SceneTemplate,
        player: ActorRef,
        participants: list[ActorInstance],
        characters: dict[str, CharacterConfig],
        director_thread: PromptThread,
        actor_threads: dict[str, PromptThread],
        history_file: Path,
        story_outline: str = "",
        created_at: datetime | None = None,
        last_active_at: datetime | None = None,
        write_scene_start: bool = True,
    ):
        self.session_id = session_id
        self.user_uuid = user_uuid
        self.template = template
        self.player = player
        self.participants = participants
        self.characters = characters
        self.director_thread = director_thread
        self.actor_threads = actor_threads
        self.story_outline = story_outline.strip()
        self.timeline = SceneTimeline(initial_state=template.initial_state)
        self.turn_index = 0
        self.created_at = created_at or datetime.now()
        self.last_active_at = last_active_at or self.created_at
        self.lock = asyncio.Lock()
        self.history_file = history_file
        self.history = SceneHistoryWriter(
            path=history_file,
            session_id=session_id,
            user_uuid=user_uuid,
            template_id=template.template_id,
        )
        if write_scene_start:
            self.history.append(
                {
                    "event": "scene_start",
                    "schema_version": 1,
                    "created_at": self.created_at.isoformat(),
                    "scene_template": template.model_dump(mode="json"),
                    "player": player.model_dump(mode="json"),
                    "participants": [
                        participant.model_dump(mode="json")
                        for participant in participants
                    ],
                    "story_outline": self.story_outline,
                }
            )

    @property
    def character_actor_ids(self) -> list[str]:
        return list(self.characters)

    def touch(self) -> None:
        self.last_active_at = datetime.now()

    def append_event(self, event: SceneEvent) -> SceneEvent:
        stored = self.timeline.append(event)
        self.touch()
        self.history.append(
            {
                "event": "scene_event",
                **stored.model_dump(mode="json"),
            }
        )
        return stored

    def replay_event(self, event: SceneEvent) -> SceneEvent:
        """Append an already-persisted event without writing it a second time."""
        return self.timeline.append(event)

    def public_snapshot(self) -> dict:
        return {
            "session_id": self.session_id,
            "user_uuid": self.user_uuid,
            "template": self.template.model_dump(mode="json"),
            "story_outline": self.story_outline,
            "player": self.player.model_dump(mode="json"),
            "participants": [
                participant.model_dump(mode="json")
                for participant in self.participants
            ],
            "scene_state": self.timeline.state.model_dump(mode="json"),
            "turn_index": self.turn_index,
            "events": [
                event.model_dump(mode="json")
                for event in self.timeline.public_events()
            ],
            "created_at": self.created_at.isoformat(),
            "last_active_at": self.last_active_at.isoformat(),
        }
