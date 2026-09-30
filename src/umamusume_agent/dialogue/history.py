"""JSONL history storage helpers for the legacy dialogue mode."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from ..character import CharacterConfig, CharacterManager
from .history_order import history_record_sort_key, parse_history_timestamp
from .protocol import (
    normalize_actor_payload,
    normalize_assistant_record,
    to_compact_context_message,
)


logger = logging.getLogger(__name__)

_SESSION_DIR_NAME_PATTERN = re.compile(
    r"^(?P<safe_name>.+)_\d{8}_\d{6}_[0-9a-fA-F]{8}$"
)


class InvalidHistoryImport(ValueError):
    """Raised when imported history has no valid dialogue messages."""


def slugify(name: str) -> str:
    return name.strip().replace(" ", "_").replace("　", "_").lower()


def create_history_file_path(
    history_dir: Path,
    user_uuid: str,
    character: CharacterConfig,
    created_at: datetime,
    session_id: str,
) -> Path:
    safe_name = slugify(character.name_en or character.name_zh)
    timestamp = created_at.strftime("%Y%m%d_%H%M%S")
    session_dir = (
        history_dir
        / user_uuid
        / f"{safe_name}_{timestamp}_{session_id[:8]}"
    )
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir / "history.jsonl"


def extract_character_names_from_record(record: Dict[str, Any]) -> list[str]:
    names = []
    for key in ("character_name_en", "character_name_zh", "character_name_jp"):
        value = record.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                names.append(stripped)
    return names


def name_tokens(names: list[str]) -> set[str]:
    tokens: set[str] = set()
    for name in names:
        if not isinstance(name, str):
            continue
        stripped = name.strip()
        if not stripped:
            continue
        tokens.add(stripped.lower())
        tokens.add(slugify(stripped))
    return tokens


def resolve_character_query_names(
    character_name: Optional[str],
    character_manager: CharacterManager,
) -> list[str]:
    if not isinstance(character_name, str):
        return []
    query = character_name.strip()
    if not query:
        return []

    names: list[str] = [query]
    try:
        if character_manager.character_exists(query):
            character_dir = character_manager.get_character_dir(query)
            config_path = character_dir / "config.json"
            if config_path.exists():
                with config_path.open("r", encoding="utf-8") as file:
                    config_data = json.load(file)
                for key in ("name_zh", "name_en", "name_jp"):
                    value = config_data.get(key)
                    if isinstance(value, str) and value.strip():
                        names.append(value.strip())
                names.append(character_dir.name)
    except Exception:
        logger.exception(
            "Failed to resolve character aliases for query: %s",
            query,
        )

    seen = set()
    deduped: list[str] = []
    for name in names:
        key = name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(name)
    return deduped


def extract_safe_name_from_session_dir(session_dir_name: str) -> str:
    match = _SESSION_DIR_NAME_PATTERN.match(session_dir_name)
    if match:
        return match.group("safe_name")
    return session_dir_name


def iter_user_history_files(history_dir: Path, user_uuid: str) -> list[Path]:
    user_dir = history_dir / user_uuid
    if not user_dir.exists():
        return []
    files = [
        path
        for path in user_dir.glob("*/history.jsonl")
        if path.is_file()
    ]
    files.sort(key=lambda path: path.parent.name)
    return files


def parse_history_file(
    history_file: Path,
) -> tuple[list[Dict[str, Any]], set[str]]:
    fallback_safe_name = extract_safe_name_from_session_dir(
        history_file.parent.name
    )
    character_names: set[str] = (
        {fallback_safe_name} if fallback_safe_name else set()
    )
    messages: list[Dict[str, Any]] = []

    with history_file.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skip invalid history line: %s", history_file)
                continue

            for name in extract_character_names_from_record(record):
                character_names.add(name)

            if record.get("event") != "message":
                continue
            role = record.get("role")
            if role not in {"user", "assistant"}:
                continue

            if role == "assistant":
                semantic_record = normalize_assistant_record(record)
            else:
                content = record.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                semantic_record = {
                    **record,
                    "role": "user",
                    "content": content.strip(),
                }

            if not str(semantic_record.get("content") or "").strip():
                continue

            message_character_name = record.get("character_name_en")
            if (
                not isinstance(message_character_name, str)
                or not message_character_name.strip()
            ):
                extracted_names = extract_character_names_from_record(record)
                message_character_name = (
                    extracted_names[0]
                    if extracted_names
                    else fallback_safe_name
                )

            event_metadata: Dict[str, Any] = {}
            actor = normalize_actor_payload(
                record.get("actor") or record.get("speaker")
            )
            if any(
                value is not None
                for value in (
                    actor,
                    record.get("event_type"),
                    record.get("target_actor_ids"),
                    record.get("event_schema_version"),
                )
            ):
                event_metadata = {
                    "actor": actor,
                    "event_type": record.get("event_type") or "dialogue",
                    "target_actor_ids": list(
                        record.get("target_actor_ids") or []
                    ),
                    "event_schema_version": (
                        record.get("event_schema_version") or 1
                    ),
                }

            messages.append(
                {
                    "session_id": record.get("session_id"),
                    "role": role,
                    "content": semantic_record.get("content"),
                    "action": semantic_record.get("action"),
                    "dialogue": semantic_record.get("dialogue"),
                    "schema_version": semantic_record.get("schema_version"),
                    "source_format": semantic_record.get("source_format"),
                    "timestamp": record.get("timestamp"),
                    "message_index": record.get("message_index"),
                    "utterance_id": record.get("utterance_id"),
                    **({"model_content": record["model_content"]} if record.get("model_content") else {}),
                    "character_name_en": message_character_name,
                    **event_metadata,
                }
            )

    return messages, character_names


def collect_history_messages(
    history_dir: Path,
    user_uuid: str,
    *,
    character_name: Optional[str],
    character_manager: CharacterManager,
) -> list[Dict[str, Any]]:
    query_tokens = name_tokens(
        resolve_character_query_names(character_name, character_manager)
    )
    messages: list[Dict[str, Any]] = []

    for history_file in iter_user_history_files(history_dir, user_uuid):
        try:
            file_messages, file_character_names = parse_history_file(
                history_file
            )
        except Exception:
            logger.exception("Failed to parse history file: %s", history_file)
            continue

        if query_tokens:
            file_tokens = name_tokens(list(file_character_names))
            if not (file_tokens & query_tokens):
                continue

        messages.extend(file_messages)

    messages.sort(key=history_record_sort_key)
    return _apply_history_resets(history_dir, user_uuid, messages)


def normalize_import_messages(raw_messages: list[Any]) -> list[Dict[str, Any]]:
    messages: list[Dict[str, Any]] = []
    for index, item in enumerate(raw_messages, start=1):
        role = (item.role or "").strip().lower()
        if role not in {"user", "assistant"}:
            raise InvalidHistoryImport(
                f"Invalid role at message {index}: {item.role}"
            )

        content = (item.content or "").strip()
        actor = normalize_actor_payload(
            getattr(item, "actor", None) or getattr(item, "speaker", None)
        )
        event_type = getattr(item, "event_type", None)
        target_actor_ids = getattr(item, "target_actor_ids", None)
        event_schema_version = getattr(item, "event_schema_version", None)
        utterance_id = (
            getattr(item, "utterance_id", None)
            or getattr(item, "utteranceId", None)
        )
        event_metadata: Dict[str, Any] = {}
        if any(
            value is not None
            for value in (
                actor,
                event_type,
                target_actor_ids,
                event_schema_version,
            )
        ):
            event_metadata = {
                "actor": actor,
                "event_type": event_type or "dialogue",
                "target_actor_ids": list(target_actor_ids or []),
                "event_schema_version": event_schema_version or 1,
            }
        if role == "user":
            if not content:
                continue
            user_record = {
                "role": role,
                "content": content,
                "timestamp": item.timestamp,
                "schema_version": item.schema_version or item.schemaVersion,
                **event_metadata,
            }
            if utterance_id:
                user_record["utterance_id"] = utterance_id
            messages.append(user_record)
            continue

        raw_record = {
            "role": "assistant",
            "content": content,
            "model_content": getattr(item, "model_content", "") or getattr(item, "modelContent", ""),
            "action": item.action,
            "dialogue": item.dialogue,
            "timestamp": item.timestamp,
            "schema_version": item.schema_version or item.schemaVersion,
            "source_format": (
                item.source_format or item.sourceFormat or "import"
            ),
            **event_metadata,
        }
        if utterance_id:
            raw_record["utterance_id"] = utterance_id
        if not content and not (
            isinstance(item.dialogue, str) and item.dialogue.strip()
        ):
            continue
        semantic_record = normalize_assistant_record(raw_record)
        if not str(
            semantic_record.get("dialogue")
            or semantic_record.get("content")
            or ""
        ).strip():
            continue
        messages.append(semantic_record)

    if not messages:
        raise InvalidHistoryImport("No valid messages to import")
    return messages


def _apply_history_resets(history_dir, user_uuid, messages):
    # A replacement import is an authoritative conversation revision, not an
    # additional copy to concatenate with older HTTP sessions.
    resets = {}
    for path in iter_user_history_files(history_dir, user_uuid):
        for record in _read_events(path):
            if ((record.get("event") == "history_import" and record.get("replace_current"))
                    or record.get("event") == "history_cleared"):
                timestamp = parse_history_timestamp(record.get("timestamp"))
                if timestamp is None:
                    logger.warning("Ignoring history reset with invalid timestamp: %s", path)
                    continue
                name = record.get("character_name_en")
                resets[name] = max(resets.get(name, timestamp), timestamp)
    kept = []
    for message in messages:
        cutoff = resets.get(message.get("character_name_en"))
        timestamp = parse_history_timestamp(message.get("timestamp"))
        # Unknown dates cannot prove a message predates the reset. Keep the
        # original rather than silently losing it on display or restoration.
        if cutoff is None or timestamp is None or timestamp >= cutoff:
            kept.append(message)
    return kept


def _read_events(path):
    try:
        with path.open(encoding="utf-8") as file:
            for line in file:
                try:
                    record = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        logger.exception("Failed to read history: %s", path)


def load_persistent_history(
    history_dir: Path, user_uuid: str, character: CharacterConfig,
    *, history_max_messages: int,
) -> list[Dict[str, str]]:
    expected = character.name_en or character.name_zh
    records = []
    for path in iter_user_history_files(history_dir, user_uuid):
        try:
            messages, names = parse_history_file(path)
        except Exception:
            logger.exception("Failed to parse history file: %s", path)
            continue
        if name_tokens([expected]) & name_tokens(list(names)):
            records.extend(messages)
    records.sort(key=history_record_sort_key)
    records = _apply_history_resets(history_dir, user_uuid, records)
    messages = [to_compact_context_message(record) for record in records]
    return messages[-history_max_messages:] if history_max_messages > 0 else messages


def load_history_memory(history_dir, session):
    from .memory import HistoryCheckpoint, checkpoint_matches, validated_checkpoints

    expected = session.character.name_en or session.character.name_zh
    candidates = []
    for path in iter_user_history_files(history_dir, session.user_uuid):
        candidates.extend(record for record in _read_events(path)
                          if record.get("event") == "context_checkpoint"
                          and record.get("character_name_en") == expected)
    if not candidates:
        return None, []
    candidates.sort(key=history_record_sort_key)
    snapshots = []
    for record in candidates:
        if isinstance(record.get("checkpoints"), list):
            # A replacement import carries its own authoritative audit history.
            snapshots = list(record["checkpoints"])
        if record.get("checkpoint"):
            snapshots.append(record["checkpoint"])
    active = None
    if candidates[-1].get("checkpoint"):
        try:
            checkpoint = HistoryCheckpoint.model_validate(candidates[-1]["checkpoint"])
            if checkpoint_matches(session, checkpoint):
                active = checkpoint
        except (ValueError, TypeError):
            logger.warning("Ignoring invalid history checkpoint for %s", session.session_id)
    return active, validated_checkpoints(session, snapshots)
