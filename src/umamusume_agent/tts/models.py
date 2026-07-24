"""Data contracts shared by the dialogue backend and the TTS MCP server."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


TTSJobState = Literal[
    "queued",
    "translating",
    "validating",
    "synthesizing",
    "downloading",
    "ready",
    "failed",
    "cancelled",
]

TERMINAL_TTS_JOB_STATES = {"ready", "failed", "cancelled"}


class TTSContextEvent(BaseModel):
    """One public event used only as translation context."""

    event_id: str
    actor_id: str = ""
    actor_type: str = ""
    display_name: str = ""
    event_type: str = ""
    content: str = ""
    action: str = ""
    dialogue: str = ""


class TTSCharacterProfile(BaseModel):
    """Stable, cache-friendly language and voice profile for one character."""

    actor_id: str
    name_zh: str
    name_jp: str
    system_prompt: str = ""
    speaking_style: str = ""
    first_person: str = ""
    user_address: str = ""
    catchphrases: list[str] = Field(default_factory=list)
    reference_audio_path: str
    reference_text_path: str = ""
    reference_text_ja: str = ""


class TTSCastMember(BaseModel):
    actor_id: str
    name_zh: str
    name_jp: str = ""
    actor_type: str = ""


class TTSSubmitRequest(BaseModel):
    """A single new character utterance to translate and synthesize."""

    user_uuid: str
    source_session_id: str
    utterance_id: str
    subtitle_zh: str
    speaker: TTSCharacterProfile
    target_actor_ids: list[str] = Field(default_factory=list)
    cast: list[TTSCastMember] = Field(default_factory=list)
    context_events: list[TTSContextEvent] = Field(default_factory=list)


class PreparedJapaneseDialogue(BaseModel):
    subtitle_ja: str
    spoken_text_ja: str
    fish_text: str


class TTSJobSnapshot(BaseModel):
    job_id: str
    state: TTSJobState
    user_uuid: str
    source_session_id: str
    utterance_id: str
    speaker_id: str
    speaker_name: str
    subtitle_zh: str
    created_at: datetime
    updated_at: datetime
    subtitle_ja: str = ""
    spoken_text_ja: str = ""
    audio_path: str = ""
    audio_format: str = "wav"
    error: str = ""
    retryable: bool = False

    def public_dict(self, *, include_owner: bool = False) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload.pop("audio_path", None)
        if not include_owner:
            payload.pop("user_uuid", None)
        return payload
