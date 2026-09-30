"""Audio/job HTTP endpoints and single-dialogue voice request adaptation."""
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

from ..dialogue.protocol import StructuredReply
from ..dialogue.session import DialogueSession
from ..tts import MCPToolError, VoiceService
from .http_utils import require_valid_user_uuid
from .schemas import DialogueRequest

logger = logging.getLogger(__name__)


def should_generate_voice(
    request: DialogueRequest,
    session: 'DialogueSession',
    reply: StructuredReply | None = None,
    *, enabled: bool,
) -> bool:
    if not enabled:
        return False
    if request.generate_voice and request.text_only:
        logger.info("text_only=true, skip voice generation for this request")
        return False
    if reply is not None and reply.source_format == "parse_error":
        logger.warning("Skipping TTS for parse-error fallback reply")
        return False

    voice_config = session.character.get_voice_config()
    if voice_config.get("no_voice") or not voice_config.get("ref_audio_path"):
        return False

    return request.generate_voice


def _single_tts_context_events(
    session: DialogueSession,
) -> list[Dict[str, Any]]:
    """Render the public prefix before the current assistant utterance."""

    history = list(session.history)
    if history and history[-1].get("role") == "assistant":
        history = history[:-1]
    events: list[Dict[str, Any]] = []
    for index, message in enumerate(history, start=1):
        role = str(message.get("role") or "")
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        is_character = role == "assistant"
        events.append(
            {
                "event_id": f"message:{index}",
                "actor_id": (
                    session.character.id if is_character else "player"
                ),
                "actor_type": (
                    "umamusume" if is_character else "trainer"
                ),
                "display_name": (
                    session.character.name_zh
                    if is_character
                    else "训练员"
                ),
                "event_type": (
                    "character_reply" if is_character else "dialogue"
                ),
                "content": content,
                "dialogue": content,
            }
        )
    return events


async def submit_single_voice(
    voice_service: VoiceService,
    session: DialogueSession,
    *,
    utterance_id: str,
    dialogue: str,
    target_actor_ids: list[str] | tuple[str, ...] | None = None,
) -> Optional[Dict[str, Any]]:
    return await voice_service.submit_dialogue(
        user_uuid=session.user_uuid,
        # Single-character history is restored across HTTP session IDs, so use
        # a stable per-character thread key for translation prefix reuse.
        source_session_id=f"dialogue:{session.character.id}",
        utterance_id=utterance_id,
        character=session.character,
        dialogue_text=dialogue,
        actor_id=session.character.id,
        target_actor_ids=list(target_actor_ids or ["player"]),
        cast=[
            {
                "actor_id": "player",
                "name_zh": "训练员",
                "name_jp": "トレーナー",
                "actor_type": "trainer",
            },
            {
                "actor_id": session.character.id,
                "name_zh": session.character.name_zh,
                "name_jp": session.character.name_jp,
                "actor_type": "umamusume",
            },
        ],
        context_events=_single_tts_context_events(session),
    )


def create_tts_router(*, voice_service: VoiceService) -> APIRouter:
    router = APIRouter()

    @router.get("/audio")
    async def get_audio(path: str):
        if not path:
            raise HTTPException(status_code=400, detail="Missing audio path")
        audio_path = Path(path)
        if not audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found")
        if not voice_service.is_allowed_audio_path(audio_path):
            raise HTTPException(status_code=403, detail="Audio path not allowed")
        return FileResponse(audio_path)

    @router.get("/tts/jobs/{job_id}")
    async def get_tts_job(job_id: str, user_uuid: str):
        try:
            return await voice_service.get_job(
                job_id=job_id,
                user_uuid=require_valid_user_uuid(user_uuid),
            )
        except MCPToolError as exc:
            raise HTTPException(status_code=404, detail="TTS job not found") from exc

    @router.delete("/tts/jobs/{job_id}")
    async def cancel_tts_job(job_id: str, user_uuid: str):
        try:
            return await voice_service.cancel_job(
                job_id=job_id,
                user_uuid=require_valid_user_uuid(user_uuid),
            )
        except MCPToolError as exc:
            raise HTTPException(status_code=404, detail="TTS job not found") from exc

    @router.get("/tts/jobs/{job_id}/audio")
    async def get_tts_job_audio(job_id: str, user_uuid: str):
        try:
            audio_path = await voice_service.resolve_job_audio(
                job_id=job_id,
                user_uuid=require_valid_user_uuid(user_uuid),
            )
        except (MCPToolError, FileNotFoundError) as exc:
            raise HTTPException(
                status_code=404,
                detail="TTS audio is not ready or has expired",
            ) from exc
        return FileResponse(
            audio_path,
            headers={
                "Cache-Control": "private, no-store",
                "Pragma": "no-cache",
            },
        )

    @router.head("/audio")
    async def head_audio(path: str):
        if not path:
            raise HTTPException(status_code=400, detail="Missing audio path")
        audio_path = Path(path)
        if not audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found")
        if not voice_service.is_allowed_audio_path(audio_path):
            raise HTTPException(status_code=403, detail="Audio path not allowed")
        headers = {
            "Content-Length": str(audio_path.stat().st_size),
            "Accept-Ranges": "bytes",
        }
        return Response(status_code=200, headers=headers)


    return router
