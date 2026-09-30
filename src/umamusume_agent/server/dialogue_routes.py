"""Single-character HTTP API; orchestration and persistence are injected."""
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from ..character import CharacterManager
from ..dialogue.history import (
    InvalidHistoryImport, collect_history_messages, iter_user_history_files,
    name_tokens, normalize_import_messages, parse_history_file, resolve_character_query_names,
)
from ..dialogue.protocol import is_json_reply_enabled
from ..dialogue.service import DialogueService
from ..llm_usage import DeepSeekUsageTracker
from ..tts import VoiceService
from .http_utils import require_valid_user_uuid, translate_llm_exception
from .schemas import DialogueRequest, HistoryImportRequest, LoadCharacterRequest
from .sessions import DialogueSessionStore
from .streaming import stream_legacy_reply
from .tts_routes import should_generate_voice, submit_single_voice

logger = logging.getLogger(__name__)


def create_dialogue_router(
    *, service: DialogueService, session_store: DialogueSessionStore,
    character_manager: CharacterManager, voice_service: VoiceService,
    usage_tracker: DeepSeekUsageTracker, settings,
) -> APIRouter:
    router = APIRouter()

    @router.post("/load_character")
    async def load_character(request: LoadCharacterRequest):
        """
        加载角色并创建会话

        返回: {session_id: str, character_name: str, system_prompt: str}
        """
        try:
            logger.info(f"Loading character: {request.character_name}")

            # 加载角色配置
            character = await character_manager.load_character(
                request.character_name,
                force_rebuild=request.force_rebuild
            )

            # 创建会话
            session = session_store.create(character, user_uuid=request.user_uuid)

            return {
                "session_id": session.session_id,
                "user_uuid": session.user_uuid,
                "character_id": character.id,
                "character_name": character.name_zh,
                "character_name_jp": character.name_jp,
                "system_prompt": character.get_system_prompt(),
                "personality": character.personality.model_dump(),
                "created_at": session.created_at.isoformat(),
                "restored_history_messages": len(session.history),
                "output_dir": str(session.output_dir),
                "history_file": str(session.history_file),
                "voice_preview_url": (
                    voice_service.build_audio_url(Path(character.get_voice_config()["ref_audio_path"]))
                    if settings.ENABLE_TTS and character.get_voice_config().get("ref_audio_path")
                    else None
                ),
            }

        except FileNotFoundError as e:
            logger.error(f"Character not found: {e}")
            raise HTTPException(status_code=404, detail=f"角色未找到: {request.character_name}。请先构建角色配置。")

        except Exception as e:
            logger.error(f"Failed to load character: {e}")
            raise HTTPException(status_code=500, detail=f"加载角色失败: {str(e)}")

    @router.post("/chat")
    async def chat(request: DialogueRequest):
        """
        发送消息并获取回复（非流式）

        返回: {action: str, dialogue: str, message: object, voice: object (optional)}
        """
        session = session_store.get(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

        try:
            with usage_tracker.operation(
                user_uuid=session.user_uuid,
                feature="dialogue_turn",
            ):
                turn_result = await service.execute_turn(
                    session=session,
                    message=request.message,
                    text_only=request.text_only,
                    speaker=request.speaker,
                    event_type=request.event_type,
                    target_actor_ids=request.target_actor_ids,
                    context_events=request.context_events,
                )
            result = turn_result.to_api_dict()

            # TTS submission is quick; translation and Fish Speech run inside the
            # project-local MCP server after this API response returns.
            if should_generate_voice(request, session, turn_result.reply, enabled=settings.ENABLE_TTS):
                voice_info = await submit_single_voice(
                    voice_service,
                    session,
                    utterance_id=turn_result.utterance_id,
                    dialogue=turn_result.reply.dialogue,
                    target_actor_ids=turn_result.target_actor_ids,
                )
                if voice_info:
                    result["voice"] = voice_info

            return result

        except Exception as e:
            logger.error(f"Chat failed: {e}")
            raise translate_llm_exception(e)

    @router.post("/chat_stream")
    async def chat_stream(request: DialogueRequest):
        """
        发送消息并流式获取回复

        返回: SSE 流
        """
        session = session_store.get(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

        async def event_generator() -> AsyncGenerator[str, None]:
            try:
                if is_json_reply_enabled(settings):
                    with usage_tracker.operation(
                        user_uuid=session.user_uuid,
                        feature="dialogue_turn",
                    ):
                        turn_result = await service.execute_turn(
                            session=session,
                            message=request.message,
                            text_only=request.text_only,
                            speaker=request.speaker,
                            event_type=request.event_type,
                            target_actor_ids=request.target_actor_ids,
                            context_events=request.context_events,
                        )

                    payload = json.dumps(
                        turn_result.to_api_dict(),
                        ensure_ascii=False,
                    )
                    yield f"event: structured_reply\ndata: {payload}\n\n"

                    if should_generate_voice(
                        request,
                        session,
                        turn_result.reply,
                        enabled=settings.ENABLE_TTS,
                    ):
                        voice_info = await submit_single_voice(
                            voice_service,
                            session,
                            utterance_id=turn_result.utterance_id,
                            dialogue=turn_result.reply.dialogue,
                            target_actor_ids=turn_result.target_actor_ids,
                        )
                        if voice_info:
                            voice_payload = json.dumps(
                                voice_info,
                                ensure_ascii=False,
                            )
                            yield (
                                "event: voice_pending\n"
                                f"data: {voice_payload}\n\n"
                            )
                    yield f"event: done\ndata: {{}}\n\n"
                    return

                async for event in stream_legacy_reply(
                    request=request, session=session, runtime=service.runtime,
                    settings=settings, voice_service=voice_service,
                    enable_tts=settings.ENABLE_TTS, llm_usage_tracker=usage_tracker,
                ):
                    yield event
            except Exception as e:
                logger.error(f"Stream chat failed: {e}")
                translated = translate_llm_exception(e)
                yield f"event: error\ndata: {translated.detail}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream"
        )

    @router.get("/sessions")
    async def list_sessions():
        """列出所有活跃会话"""
        session_store.cleanup_expired()
        return [
            {
                "session_id": s.session_id,
                "user_uuid": s.user_uuid,
                "character_name": s.character.name_zh,
                "created_at": s.created_at.isoformat(),
                "last_active_at": s.last_active_at.isoformat(),
                "message_count": s.message_count,
                "history_size": len(s.history),
                "output_dir": str(s.output_dir),
                "history_file": str(s.history_file),
            }
            for s in session_store.sessions.values()
        ]

    @router.delete("/session/{session_id}")
    async def delete_session(session_id: str):
        """删除会话"""
        session = session_store.sessions.pop(session_id, None)
        if session:
            session.mark_closed("deleted_by_api")
            return {"status": "deleted", "session_id": session_id}
        else:
            raise HTTPException(status_code=404, detail="会话不存在")

    @router.get("/history")
    async def get_history(
        user_uuid: str,
        character_name: Optional[str] = None,
        limit: int = 200,
    ):
        """查询用户历史对话，支持按角色过滤。"""
        normalized_user_uuid = require_valid_user_uuid(user_uuid)
        if limit < 0:
            raise HTTPException(status_code=400, detail="limit must be >= 0")

        all_messages = collect_history_messages(
            session_store.history_dir, normalized_user_uuid,
            character_name=character_name, character_manager=character_manager,
        )
        total_messages = len(all_messages)
        if limit > 0:
            messages = all_messages[-limit:]
        else:
            messages = all_messages

        summary_by_character: Dict[str, Dict[str, Any]] = {}
        for item in all_messages:
            character_key = str(item.get("character_name_en") or "unknown")
            summary = summary_by_character.setdefault(
                character_key,
                {
                    "character_name_en": character_key,
                    "message_count": 0,
                    "last_message_at": "",
                },
            )
            summary["message_count"] += 1
            timestamp = str(item.get("timestamp") or "")
            if timestamp and timestamp > summary["last_message_at"]:
                summary["last_message_at"] = timestamp

        characters = sorted(
            summary_by_character.values(),
            key=lambda item: item["last_message_at"],
            reverse=True,
        )

        return {
            "user_uuid": normalized_user_uuid,
            "character_name": character_name,
            "total_messages": total_messages,
            "returned_messages": len(messages),
            "limit": limit,
            "messages": messages,
            "characters": characters,
        }

    @router.post("/history/import")
    async def import_history(request: HistoryImportRequest):
        """导入历史对话到当前 session，使其参与后续 LLM 上下文。"""
        session = session_store.get(request.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")

        if request.messages:
            try:
                messages = normalize_import_messages(request.messages)
            except InvalidHistoryImport as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        elif request.replace_current:
            messages = []
        else:
            raise HTTPException(status_code=400, detail="No valid messages to import")
        source = (request.source or "manual").strip()[:80] or "manual"
        session.import_messages(messages, replace_current=request.replace_current, source=source)

        return {
            "status": "imported",
            "session_id": session.session_id,
            "user_uuid": session.user_uuid,
            "character_name": session.character.name_en or session.character.name_zh,
            "imported_messages": len(messages),
            "history_size": len(session.history),
            "replace_current": request.replace_current,
            "history_file": str(session.history_file),
        }

    @router.delete("/history")
    async def clear_history(user_uuid: str, character_name: str):
        """清除指定用户与指定角色的历史对话。"""
        normalized_user_uuid = require_valid_user_uuid(user_uuid)
        query_names = resolve_character_query_names(character_name, character_manager)
        query_tokens = name_tokens(query_names)
        if not query_tokens:
            raise HTTPException(status_code=400, detail="character_name is required")

        deleted_files = 0
        deleted_messages = 0
        for history_file in iter_user_history_files(session_store.history_dir, normalized_user_uuid):
            try:
                file_messages, file_character_names = parse_history_file(history_file)
            except Exception:
                logger.exception("Failed to parse history file: %s", history_file)
                continue

            file_tokens = name_tokens(list(file_character_names))
            if not (file_tokens & query_tokens):
                continue

            deleted_messages += len(file_messages)
            session_dir = history_file.parent
            try:
                shutil.rmtree(session_dir)
                deleted_files += 1
            except FileNotFoundError:
                continue
            except Exception:
                logger.exception("Failed to remove history directory: %s", session_dir)

        cleared_active_sessions = 0
        for session in session_store.sessions.values():
            if session.user_uuid != normalized_user_uuid:
                continue
            session_tokens = name_tokens(
                [
                    session.character.name_en,
                    session.character.name_zh,
                    session.character.name_jp,
                ]
            )
            if not (session_tokens & query_tokens):
                continue
            session.history.clear()
            session.message_count = 0
            session._append_history_event(
                {
                    "event": "history_cleared",
                    "character_query": character_name,
                    "cleared_at": datetime.now().isoformat(),
                }
            )
            cleared_active_sessions += 1

        return {
            "status": "deleted",
            "user_uuid": normalized_user_uuid,
            "character_name": character_name,
            "deleted_files": deleted_files,
            "deleted_messages": deleted_messages,
            "cleared_active_sessions": cleared_active_sessions,
        }

    @router.get("/characters")
    async def list_characters():
        """列出所有可用角色"""
        try:
            characters = character_manager.list_characters()
            return {"characters": characters}
        except Exception as e:
            logger.error(f"Failed to list characters: {e}")
            raise HTTPException(status_code=500, detail=f"获取角色列表失败: {str(e)}")


    return router
