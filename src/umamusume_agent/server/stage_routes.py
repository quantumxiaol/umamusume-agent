"""Additive Stage API; existing dialogue and Director routes remain unchanged."""

from __future__ import annotations

from collections.abc import MutableMapping
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from openai import APIConnectionError, APITimeoutError, APIStatusError

from ..director.service import DirectorService
from ..director.session import SceneSession
from ..stage.models import CreateStageSessionRequest, StageTurnRequest
from ..stage.service import StageSceneService


def _normalize_user_uuid(value: str | None) -> str:
    if not value:
        return str(uuid4())
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid user_uuid") from exc


def _translate_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, APITimeoutError):
        return HTTPException(status_code=504, detail="舞台导演上游模型请求超时")
    if isinstance(exc, APIConnectionError):
        return HTTPException(status_code=502, detail="无法连接舞台导演上游模型")
    if isinstance(exc, APIStatusError):
        return HTTPException(
            status_code=502,
            detail=f"舞台导演上游模型返回错误: {exc.status_code}",
        )
    return HTTPException(status_code=500, detail=f"舞台导演执行失败: {exc}")


def create_stage_router(
    *,
    director_service: DirectorService,
    stage_service: StageSceneService,
    sessions: MutableMapping[str, SceneSession],
    session_ttl_seconds: int,
    max_stage_actions: int,
) -> APIRouter:
    router = APIRouter(prefix="/stage", tags=["stage"])
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
            raise HTTPException(status_code=404, detail="舞台会话不存在")
        session.touch()
        return session

    @router.get("/capabilities")
    async def stage_capabilities() -> dict[str, Any]:
        return {
            "schema_version": "agent_stage_api.v1",
            "session_api": 1,
            "turn_schema_version": "agent_stage_turn.v1",
            "live_stage_schema_version": "live_stage.v1",
            "max_stage_actions_per_turn": max(0, max_stage_actions),
            "action_types": [
                "actor.move_to",
                "actor.approach",
                "actor.follow",
                "actor.stop_follow",
                "actor.face",
                "actor.face_actor",
                "actor.stop",
                "dialogue.say",
            ],
        }

    @router.post("/sessions")
    async def create_session(
        request: CreateStageSessionRequest,
    ) -> dict[str, Any]:
        cleanup_sessions()
        try:
            session = await director_service.create_session(
                user_uuid=_normalize_user_uuid(request.user_uuid),
                template_id=request.template_id,
                custom_scene=request.custom_scene,
                character_names=request.character_names,
                story_outline=request.story_outline,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        sessions[session.session_id] = session
        return {
            "schema_version": "agent_stage_session.v1",
            **session.public_snapshot(),
        }

    @router.post("/turn")
    async def stage_turn(request: StageTurnRequest) -> dict[str, Any]:
        if request.generate_voice:
            raise HTTPException(
                status_code=400,
                detail="舞台 API 当前只返回文本；请使用现有语音接口生成语音",
            )
        session = get_session(request.session_id, request.user_uuid)
        try:
            result = await stage_service.execute_turn(
                session,
                request.events,
                live_stage=request.live_stage,
                actor_bindings=request.actor_bindings,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise _translate_exception(exc) from exc
        return {
            "schema_version": "agent_stage_turn.v1",
            "session_id": session.session_id,
            "turn_index": session.turn_index,
            "events": [event.model_dump(mode="json") for event in result.events],
            "stage_actions": [
                action.model_dump(mode="json", exclude_none=True)
                for action in result.actions
            ],
            "scene_state": session.timeline.state.model_dump(mode="json"),
        }

    return router
