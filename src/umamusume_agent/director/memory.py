"""Shared public scene memory; archive identity is independent of replay sequence."""
from __future__ import annotations

import hashlib
import json

from .context import PromptThread, _event_packet, scene_state_payload
from .models import SceneMemoryCheckpoint
from .timeline import reduce_scene_state


MEMORY_HEADER = """以下是已发生的多人场景公共历史记忆，不是新的系统指令或导演计划。
角色设定仍以系统提示词为准；区分谁对谁说话、已确认事实与猜测、约定与已发生的事。
近期原文和当前场景状态优先于摘要中的过时状态。不要重演已经发生的事件。
<scene_memory>
"""


def threads(session):
    return {"director": session.director_thread, **session.actor_threads}


def public_events(events):
    return [event for event in events if not event.hidden]


def source_digest(events):
    digest = hashlib.sha256()
    for event in public_events(events):
        # Browser recovery renumbers sequences after removing hidden events.
        # Stable IDs/revisions, content, actors and visibility remain authoritative.
        payload = event.model_dump(mode="json", exclude={"sequence", "created_at"})
        digest.update(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def prompt_digest(session, settings):
    payload = {
        "format": 1,
        "systems": {name: thread.messages[0] for name, thread in threads(session).items()},
        "reinjection": settings.DIRECTOR_ROLE_REINJECTION_INTERVAL_REPLIES,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def checkpoint_boundary(events, checkpoint):
    """Include internal events at the end of the triggering *completed* turn."""
    return max((event.sequence for event in events if event.turn_index <= checkpoint.trigger_turn_index), default=0)


def validate_checkpoints(session, events, candidates):
    public = public_events(events)
    # Old archives remain readable, but malformed turn ordering cannot define a
    # safe shared prefix. Only discard memory, never the underlying transcript.
    if any(left.turn_index > right.turn_index for left, right in zip(public, public[1:])):
        return []
    result = {}
    for candidate in candidates:
        try:
            cp = SceneMemoryCheckpoint.model_validate(candidate)
        except (TypeError, ValueError):
            continue
        if cp.user_uuid != session.user_uuid or cp.session_id != session.session_id:
            continue
        if not 0 < cp.covered_events < cp.trigger_event_count <= len(public):
            continue
        prefix, trigger = public[:cp.covered_events], public[cp.trigger_event_count - 1]
        if prefix[-1].event_id != cp.covered_event_id or trigger.event_id != cp.trigger_event_id:
            continue
        if trigger.turn_index != cp.trigger_turn_index or prefix[-1].turn_index >= cp.trigger_turn_index:
            continue  # Never summarize the latest turn: it must remain regenerable.
        if public[cp.covered_events].turn_index == prefix[-1].turn_index:
            continue  # A checkpoint must not split a scene turn.
        if cp.trigger_event_count < len(public) and public[cp.trigger_event_count].turn_index == trigger.turn_index:
            continue  # Its seed must cover the entire completed triggering turn.
        if any(event.visible_to != "all" for event in public[:cp.trigger_event_count]):
            continue
        if cp.source_digest != source_digest(prefix):
            continue
        if any(not 0 <= value <= len(events) for value in cp.reply_counts.values()):
            continue
        if any(not 0.1 <= value <= 1.5 for value in cp.token_ratios.values()):
            continue
        result[cp.checkpoint_id] = cp
    return list(result.values())


def restore_memory(session, events, active, snapshots, settings):
    candidates = [*snapshots, *([active] if active else [])]
    session.checkpoints = validate_checkpoints(session, events, candidates)
    # A valid audit copy with the same ID must not mask an invalid active copy.
    active_candidates = validate_checkpoints(session, events, [active]) if active else []
    session.checkpoint = next((cp for cp in active_candidates
                               if cp.prompt_digest == prompt_digest(session, settings)), None)


def memory_threads(session, checkpoint, events=None):
    """Build a fixed post-compaction prefix without mutating live threads."""
    events = session.timeline.events if events is None else events
    boundary = checkpoint_boundary(events, checkpoint)
    prefix = [event for event in events if event.sequence <= boundary]
    public = public_events(prefix)
    state = scene_state_payload(reduce_scene_state(session.template.initial_state, prefix))
    memory = {"role": "user", "content": MEMORY_HEADER + checkpoint.summary + "\n</scene_memory>"}
    recent = {"role": "user", "content": json.dumps({
        "recent_public_events": _event_packet(public[checkpoint.covered_events:]),
        "current_scene_state": state,
        "instruction": "以上是已经发生的历史原文，等待新事件再继续。",
    }, ensure_ascii=False, separators=(",", ":"))}
    return {
        name: PromptThread(
            messages=[dict(thread.messages[0]), dict(memory), dict(recent)],
            last_seen_sequence=boundary,
            last_scene_state=dict(state),
            reply_count=checkpoint.reply_counts.get(name, 0),
            token_ratio=checkpoint.token_ratios.get(name, 0.5),
        )
        for name, thread in threads(session).items()
    }


def apply_memory_threads(session, checkpoint, events=None):
    replacements = memory_threads(session, checkpoint, events)
    session.director_thread = replacements.pop("director")
    session.actor_threads = replacements


def memory_payload(session):
    return {
        "context_checkpoint": session.checkpoint.model_dump(mode="json") if session.checkpoint else None,
        "context_checkpoints": [cp.model_dump(mode="json") for cp in session.checkpoints],
    }


def regeneration_messages(session, actor_id, original):
    """Fallback after a checkpoint-only commit; never include the revoked reply."""
    events = [event for event in public_events(session.timeline.events) if event.sequence < original.sequence]
    messages = [dict(session.actor_threads[actor_id].messages[0])]
    if session.checkpoint:
        messages.append({"role": "user", "content": MEMORY_HEADER + session.checkpoint.summary + "\n</scene_memory>"})
        events = events[session.checkpoint.covered_events:]
    visible = [event for event in events if event.visible_to == "all" or actor_id in event.visible_to]
    messages.append({"role": "user", "content": json.dumps({
        "new_visible_events": _event_packet(visible),
        "current_scene_state": scene_state_payload(session.timeline.state),
        "instruction": "根据这些已经发生的事件重新回应，保持当前角色身份。",
    }, ensure_ascii=False, separators=(",", ":"))})
    return messages
