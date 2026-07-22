"""Append-only scene timeline and deterministic state reduction."""

from __future__ import annotations

from collections.abc import Iterable

from .models import SceneEvent, SceneState


def reduce_scene_state(
    initial_state: SceneState,
    events: Iterable[SceneEvent],
) -> SceneState:
    state = initial_state.model_copy(deep=True)
    for event in events:
        if event.scene_patch is None:
            continue
        updates = event.scene_patch.updates()
        if updates:
            state = state.model_copy(update=updates, deep=True)
    return state


class SceneTimeline:
    """Source of truth for public and hidden scene events."""

    def __init__(
        self,
        *,
        initial_state: SceneState,
        events: Iterable[SceneEvent] | None = None,
    ):
        self.initial_state = initial_state.model_copy(deep=True)
        self.events: list[SceneEvent] = []
        for event in events or []:
            self.append(event)

    @property
    def latest_sequence(self) -> int:
        return self.events[-1].sequence if self.events else 0

    @property
    def state(self) -> SceneState:
        return reduce_scene_state(self.initial_state, self.events)

    def append(self, event: SceneEvent) -> SceneEvent:
        stored = event.model_copy(
            update={"sequence": self.latest_sequence + 1},
            deep=True,
        )
        self.events.append(stored)
        return stored

    def since(
        self,
        sequence: int,
        *,
        actor_id: str | None = None,
        include_hidden: bool = False,
    ) -> list[SceneEvent]:
        result: list[SceneEvent] = []
        for event in self.events:
            if event.sequence <= sequence:
                continue
            if event.hidden and not include_hidden:
                continue
            if actor_id and event.visible_to != "all":
                if actor_id not in event.visible_to:
                    continue
            result.append(event)
        return result

    def public_events(self, *, since: int = 0) -> list[SceneEvent]:
        return [
            event
            for event in self.events
            if event.sequence > since and not event.hidden
        ]
