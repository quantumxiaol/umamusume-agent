"""HTTP request models for the existing single-character API."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, model_validator

from ..dialogue.models import ActorRef, DialogueEventType, DialogueInputEvent
from ..dialogue.memory import HistoryCheckpoint
from ..input_limits import validate_input_batch, validate_history_size


class LoadCharacterRequest(BaseModel):
    """加载角色请求"""
    character_name: str
    force_rebuild: bool = False
    user_uuid: Optional[str] = None


class DialogueRequest(BaseModel):
    """对话请求"""
    session_id: str
    message: str
    generate_voice: bool = False  # 是否生成语音
    text_only: bool = False  # 兼容字段：true 时仅禁用语音生成，回复格式仍为动作/对白
    speaker: Optional[ActorRef] = None
    target_actor_ids: Optional[list[str]] = None
    event_type: Optional[DialogueEventType] = None
    context_events: Optional[list[DialogueInputEvent]] = None

    @model_validator(mode="after")
    def check_input_size(self):
        validate_input_batch([self.message, *[event.content for event in self.context_events or []]])
        return self


class HistoryImportMessage(BaseModel):
    """导入历史消息"""
    role: str
    content: str = ""
    model_content: str = ""
    modelContent: str = ""
    action: Optional[str] = None
    dialogue: Optional[str] = None
    timestamp: Optional[str] = None
    schema_version: Optional[int] = None
    schemaVersion: Optional[int] = None
    source_format: Optional[str] = None
    sourceFormat: Optional[str] = None
    actor: Optional[ActorRef] = None
    speaker: Optional[ActorRef] = None
    event_type: Optional[DialogueEventType] = None
    target_actor_ids: Optional[list[str]] = None
    event_schema_version: Optional[int] = None
    utterance_id: Optional[str] = None
    utteranceId: Optional[str] = None


class HistoryImportRequest(BaseModel):
    """导入历史请求"""
    session_id: str
    messages: list[HistoryImportMessage]
    replace_current: bool = True
    source: str = "manual"
    context_checkpoint: HistoryCheckpoint | None = None
    context_checkpoints: list[HistoryCheckpoint] | None = None

    @model_validator(mode="after")
    def check_history_size(self):
        validate_history_size(self.messages, self.context_checkpoint, self.context_checkpoints)
        return self


class SessionInfo(BaseModel):
    """会话信息"""
    session_id: str
    user_uuid: str
    character_name: str
    created_at: datetime
    last_active_at: datetime
    message_count: int
    history_size: int
    output_dir: Optional[str] = None
    history_file: Optional[str] = None
