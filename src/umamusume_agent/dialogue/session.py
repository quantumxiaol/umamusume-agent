"""In-memory state for the existing single-character dialogue mode."""

from __future__ import annotations

import json
import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from ..character import CharacterConfig
from .context import LegacyDialogueContextBuilder
from .memory import HistoryCheckpoint, checkpoint_matches, memory_messages, validated_checkpoints
from .protocol import (
    STRUCTURED_REPLY_SCHEMA_VERSION,
    normalize_assistant_record,
    to_compact_context_message,
)


logger = logging.getLogger(__name__)


class DialogueSession:
    """Mutable state for one user and one character."""

    def __init__(
        self,
        session_id: str,
        character: CharacterConfig,
        user_uuid: str,
        *,
        output_dir: Path,
        history_file: Path,
        context_builder: LegacyDialogueContextBuilder,
        history_max_messages: int = 0,
        created_at: Optional[datetime] = None,
        initial_history: Optional[list[Dict[str, str]]] = None,
    ):
        self.session_id = session_id
        self.user_uuid = user_uuid
        self.character = character
        self.created_at = created_at or datetime.now()
        self.last_active_at = self.created_at
        self.history = list(initial_history or [])
        self.lock = asyncio.Lock()
        self.checkpoint: HistoryCheckpoint | None = None
        self.checkpoints: list[HistoryCheckpoint] = []
        self.token_ratio = 0.5
        self.message_count = len(self.history)
        self.voice_index = 0
        self.output_dir = output_dir
        self.audio_history: list[Dict[str, Any]] = []
        self.history_file = history_file
        self.context_builder = context_builder
        self.history_max_messages = max(0, history_max_messages)
        self._closed = False
        self._trim_history()

        self._append_history_event(
            {
                "event": "session_start",
                "created_at": self.created_at.isoformat(),
                "restored_history_messages": len(self.history),
            }
        )

    def _append_history_event(self, payload: Dict[str, Any], *, strict=False):
        record: Dict[str, Any] = {
            "session_id": self.session_id,
            "user_uuid": self.user_uuid,
            "character_name_en": (
                self.character.name_en or self.character.name_zh
            ),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        record.update(payload)

        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with self.history_file.open("a", encoding="utf-8") as file:
                offset = file.tell()
                try:
                    file.write(json.dumps(record, ensure_ascii=False) + "\n")
                    file.flush()
                    if strict:
                        os.fsync(file.fileno())
                except Exception:
                    # Roll back only this append. A partial checkpoint line must
                    # not swallow the next message when writing resumes.
                    try:
                        file.truncate(offset)
                    except Exception:
                        logger.exception("Could not roll back partial history append")
                    raise
        except Exception:
            logger.exception(
                "Failed to persist dialogue history: session_id=%s",
                self.session_id,
            )
            if strict:
                raise

    def _rewrite_history_file(self):
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with self.history_file.open("w", encoding="utf-8"):
                pass
        except Exception:
            logger.exception(
                "Failed to rewrite dialogue history: session_id=%s",
                self.session_id,
            )

    def mark_closed(self, reason: str):
        if self._closed:
            return
        self._closed = True
        self._append_history_event({"event": "session_end", "reason": reason})

    def touch(self):
        self.last_active_at = datetime.now()

    def _trim_history(self):
        if self.history_max_messages <= 0:
            return
        overflow = len(self.history) - self.history_max_messages
        if overflow > 0:
            del self.history[:overflow]

    def add_message(
        self,
        role: str,
        content: str,
        *,
        action: Optional[str] = None,
        dialogue: Optional[str] = None,
        source_format: str = "text",
        schema_version: Optional[int] = None,
        imported_timestamp: Optional[str] = None,
        actor: Optional[Dict[str, Any]] = None,
        event_type: Optional[str] = None,
        target_actor_ids: Optional[list[str]] = None,
        event_schema_version: Optional[int] = None,
        utterance_id: Optional[str] = None,
        model_content: str = "",
    ):
        """Add a message to model history and append its JSONL event."""

        if role == "assistant":
            semantic_record = normalize_assistant_record(
                {
                    "role": role,
                    "content": content,
                    "action": action,
                    "dialogue": dialogue,
                    "source_format": source_format,
                    "model_content": model_content,
                    "schema_version": (
                        schema_version or STRUCTURED_REPLY_SCHEMA_VERSION
                    ),
                    "actor": actor,
                    "event_type": event_type,
                    "target_actor_ids": target_actor_ids,
                    "event_schema_version": event_schema_version,
                }
            )
        else:
            semantic_record = {
                "role": role,
                "content": content,
                "actor": actor,
                "event_type": event_type,
                "target_actor_ids": target_actor_ids,
                "event_schema_version": event_schema_version,
            }

        self.history.append(to_compact_context_message(semantic_record))
        self.message_count += 1
        self.touch()
        self._trim_history()
        payload: Dict[str, Any] = {
            "event": "message",
            "role": role,
            "content": semantic_record.get("content") or "",
            "message_index": self.message_count,
        }
        if imported_timestamp:
            payload["imported_timestamp"] = imported_timestamp
        if utterance_id:
            payload["utterance_id"] = utterance_id
        if any(
            value is not None
            for value in (
                actor,
                event_type,
                target_actor_ids,
                event_schema_version,
            )
        ):
            payload.update(
                {
                    "actor": actor,
                    "event_type": event_type or "dialogue",
                    "target_actor_ids": list(target_actor_ids or []),
                    "event_schema_version": event_schema_version or 1,
                }
            )
        if role == "assistant":
            if model_content:
                payload["model_content"] = model_content
            payload.update(
                {
                    "action": semantic_record.get("action") or "无",
                    "dialogue": (
                        semantic_record.get("dialogue")
                        or semantic_record.get("content")
                        or ""
                    ),
                    "source_format": (
                        semantic_record.get("source_format") or source_format
                    ),
                    "schema_version": (
                        semantic_record.get("schema_version")
                        or STRUCTURED_REPLY_SCHEMA_VERSION
                    ),
                }
            )
        self._append_history_event(payload)

    def import_messages(
        self,
        messages: list[Dict[str, Any]],
        replace_current: bool = True,
        source: str = "manual",
        checkpoint: HistoryCheckpoint | None = None,
        checkpoints: list[HistoryCheckpoint] | None = None,
    ):
        """Import messages into the current model context and JSONL file."""

        candidate = checkpoint or self.checkpoint
        imported_context = [to_compact_context_message(message) for message in messages]
        next_history = imported_context if replace_current else [*self.history, *imported_context]
        valid_checkpoint = candidate if candidate and checkpoint_matches(self, candidate, next_history) else None
        audit_candidates = list(self.checkpoints if checkpoints is None else checkpoints)
        if valid_checkpoint:
            audit_candidates.append(valid_checkpoint)
        valid_checkpoints = validated_checkpoints(self, audit_candidates, next_history)
        if replace_current:
            self.checkpoint = None
            self.history.clear()
            self.message_count = 0
            self.touch()
            self._rewrite_history_file()
            self._append_history_event(
                {
                    "event": "session_start",
                    "created_at": self.created_at.isoformat(),
                    "restored_history_messages": 0,
                    "reset_reason": "history_import",
                }
            )

        self._append_history_event(
            {
                "event": "history_import",
                "source": source,
                "replace_current": replace_current,
                "imported_messages": len(messages),
            }
        )
        for message in messages:
            self.add_message(
                message["role"],
                message.get("content", ""),
                action=message.get("action"),
                dialogue=message.get("dialogue"),
                source_format=message.get("source_format") or source,
                schema_version=message.get("schema_version"),
                imported_timestamp=message.get("timestamp"),
                actor=message.get("actor") or message.get("speaker"),
                event_type=message.get("event_type"),
                target_actor_ids=message.get("target_actor_ids"),
                event_schema_version=message.get("event_schema_version"),
                utterance_id=message.get("utterance_id"),
                model_content=message.get("model_content") or "",
            )
        self.checkpoint = valid_checkpoint
        self.checkpoints = valid_checkpoints
        if valid_checkpoint:
            self.token_ratio = valid_checkpoint.token_ratio
        self._append_history_event({
            "event": "context_checkpoint",
            "checkpoint": valid_checkpoint.model_dump(mode="json") if valid_checkpoint else None,
            "checkpoints": [item.model_dump(mode="json") for item in valid_checkpoints],
        })

    def get_messages(self, text_only: bool = False) -> list:
        """Return the complete model message list, including system prompt."""

        return memory_messages(self, text_only=text_only)
