"""JSONL persistence for director-mode scene sessions."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import ActorInstance, SceneEvent, SceneTemplate
from .timeline import SceneTimeline


class InvalidSceneHistory(ValueError):
    """Raised when a scene history cannot be safely restored."""


@dataclass(frozen=True)
class LoadedSceneHistory:
    path: Path
    session_id: str
    user_uuid: str
    template: SceneTemplate
    player: dict[str, Any]
    participants: list[ActorInstance]
    story_outline: str
    events: list[SceneEvent]
    created_at: datetime
    updated_at: datetime


def _parse_datetime(value: Any, *, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except ValueError:
            pass
    return fallback or datetime.now()


def create_scene_history_path(
    history_dir: Path,
    *,
    user_uuid: str,
    template_id: str,
    session_id: str,
    created_at: datetime,
) -> Path:
    timestamp = created_at.strftime("%Y%m%d_%H%M%S")
    session_dir = (
        history_dir
        / user_uuid
        / f"{template_id}_{timestamp}_{session_id[:8]}"
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir / "scene.jsonl"


def iter_scene_history_files(history_dir: Path, *, user_uuid: str) -> list[Path]:
    user_dir = (history_dir / user_uuid).resolve()
    if not user_dir.exists() or not user_dir.is_dir():
        return []
    return sorted(
        user_dir.glob("*/scene.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def load_scene_history(path: Path) -> LoadedSceneHistory:
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                payload = json.loads(stripped)
                if not isinstance(payload, dict):
                    raise InvalidSceneHistory(
                        f"历史第 {line_number} 行不是 JSON object"
                    )
                records.append(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidSceneHistory(f"无法读取导演场景历史: {path.name}") from exc

    start = next(
        (record for record in records if record.get("event") == "scene_start"),
        None,
    )
    if start is None:
        raise InvalidSceneHistory("导演场景历史缺少 scene_start")
    try:
        session_id = str(start["session_id"])
        user_uuid = str(start["user_uuid"])
        template = SceneTemplate.model_validate(start["scene_template"])
        participants = [
            ActorInstance.model_validate(item)
            for item in start.get("participants", [])
        ]
        player = dict(start["player"])
        events = [
            SceneEvent.model_validate(record)
            for record in records
            if record.get("event") == "scene_event"
        ]
    except Exception as exc:
        raise InvalidSceneHistory("导演场景历史结构无效") from exc

    if not session_id or not user_uuid or not participants:
        raise InvalidSceneHistory("导演场景历史缺少会话或参与者信息")
    for record in records:
        if str(record.get("session_id", session_id)) != session_id:
            raise InvalidSceneHistory("导演场景历史包含不一致的 session_id")
        if str(record.get("user_uuid", user_uuid)) != user_uuid:
            raise InvalidSceneHistory("导演场景历史包含不一致的 user_uuid")

    created_at = _parse_datetime(start.get("created_at") or start.get("timestamp"))
    updated_at = max(
        (_parse_datetime(record.get("timestamp"), fallback=created_at) for record in records),
        default=created_at,
    )
    return LoadedSceneHistory(
        path=path,
        session_id=session_id,
        user_uuid=user_uuid,
        template=template,
        player=player,
        participants=participants,
        story_outline=str(start.get("story_outline") or "").strip(),
        events=events,
        created_at=created_at,
        updated_at=updated_at,
    )


def find_scene_history(
    history_dir: Path,
    *,
    user_uuid: str,
    session_id: str,
) -> LoadedSceneHistory:
    for path in iter_scene_history_files(history_dir, user_uuid=user_uuid):
        try:
            history = load_scene_history(path)
        except InvalidSceneHistory:
            continue
        if history.session_id == session_id and history.user_uuid == user_uuid:
            return history
    raise FileNotFoundError("导演场景历史不存在")


def scene_history_summary(history: LoadedSceneHistory) -> dict[str, Any]:
    timeline = SceneTimeline(
        initial_state=history.template.initial_state,
        events=history.events,
    )
    public_events = timeline.public_events()
    latest = public_events[-1] if public_events else None
    preview = ""
    if latest is not None:
        preview = (latest.dialogue or latest.content or latest.action).strip()
    character_names = [
        item.actor.display_name
        for item in history.participants
        if item.actor.actor_type in {"umamusume", "npc"}
    ]
    return {
        "session_id": history.session_id,
        "template_id": history.template.template_id,
        "scene_name": history.template.name,
        "location": timeline.state.location,
        "time": timeline.state.time,
        "character_names": character_names,
        "turn_index": max((event.turn_index for event in history.events), default=0),
        "event_count": len(public_events),
        "preview": preview[:160],
        "created_at": history.created_at.isoformat(),
        "updated_at": history.updated_at.isoformat(),
        "is_custom": history.template.template_id.startswith("custom_"),
    }


def delete_scene_history(history: LoadedSceneHistory, *, history_dir: Path) -> None:
    user_dir = (history_dir / history.user_uuid).resolve()
    session_dir = history.path.parent.resolve()
    if user_dir not in session_dir.parents:
        raise InvalidSceneHistory("导演场景历史路径越界")
    if session_dir.exists():
        shutil.rmtree(session_dir)


class SceneHistoryWriter:
    def __init__(
        self,
        *,
        path: Path,
        session_id: str,
        user_uuid: str,
        template_id: str,
    ):
        self.path = path
        self.session_id = session_id
        self.user_uuid = user_uuid
        self.template_id = template_id

    def append(self, payload: dict[str, Any]) -> None:
        record = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "user_uuid": self.user_uuid,
            "template_id": self.template_id,
            **payload,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
