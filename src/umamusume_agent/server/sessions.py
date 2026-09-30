"""HTTP session registry and expiry; dialogue state remains in dialogue/."""
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from ..character import CharacterConfig
from ..dialogue.context import LegacyDialogueContextBuilder
from ..dialogue.history import create_history_file_path, load_persistent_history, load_history_memory
from ..dialogue.session import DialogueSession
from ..tts import VoiceService
from .http_utils import normalize_user_uuid

logger = logging.getLogger(__name__)


class DialogueSessionStore:
    def __init__(
        self, *, history_dir: Path, context_builder: LegacyDialogueContextBuilder,
        voice_service: VoiceService, ttl_seconds: int, history_max_messages: int,
    ):
        self.history_dir = history_dir
        self.context_builder = context_builder
        self.voice_service = voice_service
        self.ttl_seconds = max(0, ttl_seconds)
        self.history_max_messages = max(0, history_max_messages)
        self.sessions: dict[str, DialogueSession] = {}

    def create(self, character: CharacterConfig, user_uuid: Optional[str] = None) -> DialogueSession:
        """创建新会话"""
        session_id = str(uuid4())
        normalized_user_uuid = normalize_user_uuid(user_uuid)
        restored_history = load_persistent_history(
            self.history_dir, normalized_user_uuid, character,
            history_max_messages=self.history_max_messages,
        )
        created_at = datetime.now()
        session = DialogueSession(
            session_id,
            character,
            normalized_user_uuid,
            output_dir=self.voice_service.create_output_dir(character, created_at),
            history_file=create_history_file_path(
                self.history_dir,
                normalized_user_uuid,
                character,
                created_at,
                session_id,
            ),
            context_builder=self.context_builder,
            history_max_messages=self.history_max_messages,
            created_at=created_at,
            initial_history=restored_history,
        )
        self.sessions[session_id] = session
        session.checkpoint, session.checkpoints = load_history_memory(self.history_dir, session)
        if session.checkpoint:
            session.token_ratio = session.checkpoint.token_ratio
        logger.info(
            "Created session %s for character %s (user_uuid=%s, restored=%s)",
            session_id,
            character.name_zh,
            normalized_user_uuid,
            len(restored_history),
        )
        return session

    def get(self, session_id: str) -> Optional[DialogueSession]:
        """获取会话"""
        session = self.sessions.get(session_id)
        if not session:
            return None
        if self.is_expired(session):
            self.sessions.pop(session_id, None)
            session.mark_closed("expired_on_access")
            logger.info(f"Session expired and removed on access: {session_id}")
            return None
        session.touch()
        return session

    def is_expired(self, session: DialogueSession, now: Optional[datetime] = None) -> bool:
        if self.ttl_seconds <= 0 or session.lock.locked():
            return False
        current_time = now or datetime.now()
        idle_seconds = (current_time - session.last_active_at).total_seconds()
        return idle_seconds > self.ttl_seconds

    def cleanup_expired(self) -> int:
        if self.ttl_seconds <= 0:
            return 0
        now = datetime.now()
        expired_session_ids = [
            session_id
            for session_id, session in list(self.sessions.items())
            if self.is_expired(session, now=now)
        ]
        for session_id in expired_session_ids:
            session = self.sessions.pop(session_id, None)
            if session:
                session.mark_closed("expired_by_cleanup_worker")
        if expired_session_ids:
            logger.info(f"Cleaned up {len(expired_session_ids)} expired sessions")
        return len(expired_session_ids)
