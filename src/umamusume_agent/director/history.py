"""JSONL persistence for director-mode scene sessions."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


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
