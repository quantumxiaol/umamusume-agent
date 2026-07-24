"""FastAPI routes for the isolated director-mode scene service."""

from __future__ import annotations

import json
from collections.abc import MutableMapping
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from openai import APIConnectionError, APITimeoutError, APIStatusError
from pydantic import BaseModel

from ..dialogue.models import DialogueInputEvent
from ..director.models import CustomSceneDefinition, SceneRecoverySnapshot
from ..director.service import DirectorService
from ..director.session import SceneSession


class CreateDirectorSessionRequest(BaseModel):
    template_id: str | None = None
    custom_scene: CustomSceneDefinition | None = None
    story_outline: str = ""
    character_names: list[str]
    user_uuid: str | None = None


class DirectorTurnRequest(BaseModel):
    session_id: str
    user_uuid: str
    events: list[DialogueInputEvent]
    generate_voice: bool = False


class DirectorHistoryRequest(BaseModel):
    user_uuid: str


class DirectorRecoveryRequest(BaseModel):
    user_uuid: str
    snapshot: SceneRecoverySnapshot


def _normalize_user_uuid(value: str | None) -> str:
    if not value:
        return str(uuid4())
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid user_uuid") from exc


def _translate_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, APITimeoutError):
        return HTTPException(status_code=504, detail="导演模式上游模型请求超时")
    if isinstance(exc, APIConnectionError):
        return HTTPException(status_code=502, detail="无法连接导演模式上游模型")
    if isinstance(exc, APIStatusError):
        return HTTPException(
            status_code=502,
            detail=f"导演模式上游模型返回错误: {exc.status_code}",
        )
    return HTTPException(status_code=500, detail=f"导演模式执行失败: {exc}")


def create_director_router(
    *,
    service: DirectorService,
    sessions: MutableMapping[str, SceneSession],
    session_ttl_seconds: int,
    voice_service: Any | None = None,
    enable_tts: bool = False,
) -> APIRouter:
    router = APIRouter(prefix="/director", tags=["director"])
    ttl_seconds = max(0, session_ttl_seconds)

    def cleanup_sessions() -> None:
        if ttl_seconds <= 0:
            return
        now = datetime.now()
        expired = [
            session_id
            for session_id, session in sessions.items()
            if (now - session.last_active_at).total_seconds() > ttl_seconds
        ]
        for session_id in expired:
            sessions.pop(session_id, None)

    def get_session(session_id: str, user_uuid: str) -> SceneSession:
        cleanup_sessions()
        session = sessions.get(session_id)
        normalized_user_uuid = _normalize_user_uuid(user_uuid)
        if session is None or session.user_uuid != normalized_user_uuid:
            raise HTTPException(status_code=404, detail="导演场景会话不存在")
        session.touch()
        return session

    def tts_context_events(
        session: SceneSession,
        *,
        before_sequence: int,
    ) -> list[dict[str, Any]]:
        return [
            {
                "event_id": event.event_id,
                "actor_id": event.actor.actor_id if event.actor else "",
                "actor_type": (
                    event.actor.actor_type if event.actor else "narrator"
                ),
                "display_name": (
                    event.actor.display_name if event.actor else "环境"
                ),
                "event_type": event.event_type,
                "content": event.content,
                "action": event.action,
                "dialogue": event.dialogue,
            }
            for event in session.timeline.public_events()
            if event.sequence < before_sequence
        ]

    def tts_cast(session: SceneSession) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for participant in session.participants:
            actor = participant.actor
            character = session.characters.get(actor.actor_id)
            items.append(
                {
                    "actor_id": actor.actor_id,
                    "name_zh": actor.display_name,
                    "name_jp": (
                        character.name_jp
                        if character is not None
                        else (
                            "トレーナー"
                            if actor.actor_type == "trainer"
                            else actor.display_name
                        )
                    ),
                    "actor_type": actor.actor_type,
                }
            )
        return items

    async def submit_event_voice(
        session: SceneSession,
        event: Any,
    ) -> dict[str, Any] | None:
        if (
            not enable_tts
            or voice_service is None
            or event.event_type != "character_reply"
            or event.actor is None
            or not event.dialogue
        ):
            return None
        character = session.characters.get(event.actor.actor_id)
        if character is None:
            return None
        return await voice_service.submit_dialogue(
            user_uuid=session.user_uuid,
            source_session_id=session.session_id,
            utterance_id=event.event_id,
            character=character,
            dialogue_text=event.dialogue,
            actor_id=event.actor.actor_id,
            target_actor_ids=event.target_actor_ids,
            cast=tts_cast(session),
            context_events=tts_context_events(
                session,
                before_sequence=event.sequence,
            ),
        )

    @router.get("/templates")
    async def list_templates() -> dict[str, Any]:
        return {
            "templates": [
                template.model_dump(mode="json")
                for template in service.template_repository.list()
            ]
        }

    @router.get("/history")
    async def list_director_history(
        user_uuid: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        normalized_user_uuid = _normalize_user_uuid(user_uuid)
        return {
            "scenes": service.list_history(
                user_uuid=normalized_user_uuid,
                limit=min(100, max(1, limit)),
            )
        }

    @router.post("/history/{session_id}/resume")
    async def resume_director_history(
        session_id: str,
        request: DirectorHistoryRequest,
    ) -> dict[str, Any]:
        cleanup_sessions()
        user_uuid = _normalize_user_uuid(request.user_uuid)
        live_session = sessions.get(session_id)
        if live_session is not None:
            if live_session.user_uuid != user_uuid:
                raise HTTPException(status_code=404, detail="导演场景历史不存在")
            live_session.touch()
            return live_session.public_snapshot()
        try:
            session = await service.restore_session(
                user_uuid=user_uuid,
                session_id=session_id,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.touch()
        sessions[session.session_id] = session
        return session.public_snapshot()

    @router.delete("/history/{session_id}")
    async def delete_director_history(
        session_id: str,
        user_uuid: str,
    ) -> dict[str, Any]:
        normalized_user_uuid = _normalize_user_uuid(user_uuid)
        try:
            service.delete_history(
                user_uuid=normalized_user_uuid,
                session_id=session_id,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sessions.pop(session_id, None)
        return {"status": "deleted", "session_id": session_id}

    @router.post("/sessions")
    async def create_session(
        request: CreateDirectorSessionRequest,
    ) -> dict[str, Any]:
        cleanup_sessions()
        try:
            session = await service.create_session(
                user_uuid=_normalize_user_uuid(request.user_uuid),
                template_id=request.template_id,
                character_names=request.character_names,
                custom_scene=request.custom_scene,
                story_outline=request.story_outline,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sessions[session.session_id] = session
        return session.public_snapshot()

    @router.post("/sessions/recover")
    async def recover_scene_session(
        request: DirectorRecoveryRequest,
    ) -> dict[str, Any]:
        cleanup_sessions()
        user_uuid = _normalize_user_uuid(request.user_uuid)
        live_session = sessions.get(request.snapshot.session_id)
        if live_session is not None:
            if live_session.user_uuid != user_uuid:
                raise HTTPException(status_code=404, detail="导演场景会话不存在")
            live_session.touch()
            return live_session.public_snapshot()
        try:
            session = await service.recover_browser_snapshot(
                user_uuid=user_uuid,
                snapshot=request.snapshot,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sessions[session.session_id] = session
        return session.public_snapshot()

    @router.get("/sessions/{session_id}")
    async def get_scene_session(
        session_id: str,
        user_uuid: str,
    ) -> dict[str, Any]:
        return get_session(session_id, user_uuid).public_snapshot()

    @router.delete("/sessions/{session_id}")
    async def delete_scene_session(
        session_id: str,
        user_uuid: str,
    ) -> dict[str, Any]:
        session = get_session(session_id, user_uuid)
        if sessions.pop(session.session_id, None) is None:
            raise HTTPException(status_code=404, detail="导演场景会话不存在")
        return {"status": "deleted", "session_id": session_id}

    @router.post("/turn")
    async def director_turn(request: DirectorTurnRequest) -> dict[str, Any]:
        session = get_session(request.session_id, request.user_uuid)
        try:
            events = await service.execute_turn(session, request.events)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise _translate_exception(exc) from exc
        event_payloads: list[dict[str, Any]] = []
        for event in events:
            event_payload = event.model_dump(mode="json")
            if request.generate_voice:
                voice = await submit_event_voice(session, event)
                if voice:
                    event_payload["voice"] = voice
            event_payloads.append(event_payload)

        return {
            "session_id": session.session_id,
            "turn_index": session.turn_index,
            "events": event_payloads,
            "scene_state": session.timeline.state.model_dump(mode="json"),
        }

    @router.post("/turn_stream")
    async def director_turn_stream(
        request: DirectorTurnRequest,
    ) -> StreamingResponse:
        session = get_session(request.session_id, request.user_uuid)

        async def event_generator():
            try:
                async for event in service.stream_turn(session, request.events):
                    event_name = (
                        "character_reply"
                        if event.event_type == "character_reply"
                        else "scene_event"
                    )
                    event_payload = event.model_dump(mode="json")
                    if request.generate_voice:
                        voice = await submit_event_voice(session, event)
                        if voice:
                            event_payload["voice"] = voice
                    payload = json.dumps(event_payload, ensure_ascii=False)
                    yield f"event: {event_name}\ndata: {payload}\n\n"
                state_payload = json.dumps(
                    session.timeline.state.model_dump(mode="json"),
                    ensure_ascii=False,
                )
                yield f"event: scene_state\ndata: {state_payload}\n\n"
                yield "event: done\ndata: {}\n\n"
            except Exception as exc:
                translated = _translate_exception(exc)
                payload = json.dumps(
                    {"detail": translated.detail},
                    ensure_ascii=False,
                )
                yield f"event: error\ndata: {payload}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
        )

    return router
