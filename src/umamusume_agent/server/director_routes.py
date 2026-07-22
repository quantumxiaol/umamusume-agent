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
from ..director.service import DirectorService
from ..director.session import SceneSession


class CreateDirectorSessionRequest(BaseModel):
    template_id: str
    character_names: list[str]
    user_uuid: str | None = None


class DirectorTurnRequest(BaseModel):
    session_id: str
    events: list[DialogueInputEvent]


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

    def get_session(session_id: str) -> SceneSession:
        cleanup_sessions()
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="导演场景会话不存在")
        session.touch()
        return session

    @router.get("/templates")
    async def list_templates() -> dict[str, Any]:
        return {
            "templates": [
                template.model_dump(mode="json")
                for template in service.template_repository.list()
            ]
        }

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
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sessions[session.session_id] = session
        return session.public_snapshot()

    @router.get("/sessions/{session_id}")
    async def get_scene_session(session_id: str) -> dict[str, Any]:
        return get_session(session_id).public_snapshot()

    @router.delete("/sessions/{session_id}")
    async def delete_scene_session(session_id: str) -> dict[str, Any]:
        session = sessions.pop(session_id, None)
        if session is None:
            raise HTTPException(status_code=404, detail="导演场景会话不存在")
        return {"status": "deleted", "session_id": session_id}

    @router.post("/turn")
    async def director_turn(request: DirectorTurnRequest) -> dict[str, Any]:
        session = get_session(request.session_id)
        try:
            events = await service.execute_turn(session, request.events)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise _translate_exception(exc) from exc
        return {
            "session_id": session.session_id,
            "turn_index": session.turn_index,
            "events": [event.model_dump(mode="json") for event in events],
            "scene_state": session.timeline.state.model_dump(mode="json"),
        }

    @router.post("/turn_stream")
    async def director_turn_stream(
        request: DirectorTurnRequest,
    ) -> StreamingResponse:
        session = get_session(request.session_id)

        async def event_generator():
            try:
                async for event in service.stream_turn(session, request.events):
                    event_name = (
                        "character_reply"
                        if event.event_type == "character_reply"
                        else "scene_event"
                    )
                    payload = json.dumps(
                        event.model_dump(mode="json"),
                        ensure_ascii=False,
                    )
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
