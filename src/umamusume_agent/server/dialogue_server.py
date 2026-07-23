"""
Dialogue Server - 交互式角色对话服务
提供基于角色人格的对话API和语音生成
"""

import asyncio
import json
import logging
import shutil
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional, AsyncGenerator, Dict, Any
from datetime import datetime
from time import monotonic
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse, Response, JSONResponse
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware

from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, APIStatusError

from ..character import CharacterManager, CharacterConfig
from ..dialogue.context import LegacyDialogueContextBuilder
from ..dialogue.history import (
    InvalidHistoryImport,
    collect_history_messages,
    create_history_file_path,
    extract_character_names_from_record,
    extract_safe_name_from_session_dir,
    iter_user_history_files,
    load_persistent_history,
    name_tokens,
    normalize_import_messages,
    parse_history_file,
    resolve_character_query_names,
    slugify,
)
from ..dialogue.models import (
    EVENT_SCHEMA_VERSION,
    ActorRef,
    CharacterReplyContext,
    DialogueEventType,
    DialogueInputEvent,
    actor_from_character,
    default_player_actor,
)
from ..dialogue.protocol import (
    SAFE_PARSE_FAILURE_REPLY as _SAFE_PARSE_FAILURE_REPLY,
    STRUCTURED_REPLY_SCHEMA_VERSION as _STRUCTURED_REPLY_SCHEMA_VERSION,
    StructuredReply,
    extract_dialogue_text as _extract_dialogue_text,
    is_json_reply_enabled as _protocol_is_json_reply_enabled,
    json_output_mode as _protocol_json_output_mode,
    load_json_object_from_text as _load_json_object_from_text,
    normalize_assistant_record as _normalize_assistant_record,
    normalize_structured_reply as _normalize_structured_reply,
    parse_structured_reply as _parse_structured_reply,
    split_action_dialogue as _split_action_dialogue,
    strip_stage_directions as _strip_stage_directions,
    structured_reply_from_legacy_text as _structured_reply_from_legacy_text,
    structured_reply_message as _structured_reply_message,
    to_compact_context_message as _to_compact_context_message,
)
from ..dialogue.runtime import CharacterRuntime
from ..dialogue.service import DialogueService
from ..dialogue.session import DialogueSession
from ..director.context import CharacterSceneContextBuilder, DirectorContextBuilder
from ..director.runtime import DirectorRuntime
from ..director.service import DirectorService
from ..director.session import SceneSession
from ..director.templates import SceneTemplateRepository
from ..tts import IndexTTSMCPClient, VoiceService
from ..config import config
from .director_routes import create_director_router

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 初始化组件
character_manager = CharacterManager()
llm_client = AsyncOpenAI(
    api_key=config.ROLEPLAY_LLM_MODEL_API_KEY,
    base_url=config.ROLEPLAY_LLM_MODEL_BASE_URL,
    timeout=max(5.0, config.ROLEPLAY_LLM_TIMEOUT_SECONDS),
    max_retries=max(0, config.ROLEPLAY_LLM_MAX_RETRIES),
)
tts_client = IndexTTSMCPClient()

OUTPUTS_DIR = Path(config.OUTPUTS_DIRECTORY)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
DIALOGUE_HISTORY_DIR = Path(config.DIALOGUE_HISTORY_DIRECTORY)
DIALOGUE_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
DIRECTOR_HISTORY_DIR = Path(config.DIRECTOR_HISTORY_DIRECTORY)
DIRECTOR_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
CHARACTERS_DIR = Path(config.CHARACTERS_DIRECTORY)
SESSION_TTL_SECONDS = max(0, config.DIALOGUE_SESSION_TTL_SECONDS)
SESSION_HISTORY_MAX_MESSAGES = max(0, config.DIALOGUE_SESSION_HISTORY_MAX_MESSAGES)
SESSION_CLEANUP_INTERVAL_SECONDS = max(5, config.DIALOGUE_SESSION_CLEANUP_INTERVAL_SECONDS)
API_ACCESS_KEY = (config.API_ACCESS_KEY or "").strip()
API_RATE_LIMIT_ENABLED = config.API_RATE_LIMIT_ENABLED
API_RATE_LIMIT_WINDOW_SECONDS = max(1, config.API_RATE_LIMIT_WINDOW_SECONDS)
API_RATE_LIMIT_MAX_REQUESTS = max(1, config.API_RATE_LIMIT_MAX_REQUESTS)
API_CHAT_RATE_LIMIT_MAX_REQUESTS = max(1, config.API_CHAT_RATE_LIMIT_MAX_REQUESTS)
ENABLE_TTS = config.ENABLE_TTS
API_AUTH_EXEMPT_PATHS = {"/", "/audio"}
_CHAT_ENDPOINTS = {
    "/chat",
    "/chat_stream",
    "/director/turn",
    "/director/turn_stream",
}
_rate_limit_buckets: dict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = asyncio.Lock()
_response_format_unsupported: set[tuple[str, str]] = set()

voice_service = VoiceService(
    client=tts_client,
    outputs_dir=OUTPUTS_DIR,
    characters_dir=CHARACTERS_DIR,
)
character_runtime = CharacterRuntime(
    llm_client=llm_client,
    settings=config,
    response_format_unsupported=_response_format_unsupported,
)
legacy_context_builder = LegacyDialogueContextBuilder(settings=config)
dialogue_service = DialogueService(
    runtime=character_runtime,
    context_builder=legacy_context_builder,
)
scene_template_repository = SceneTemplateRepository(
    config.SCENE_TEMPLATES_DIRECTORY
)
director_context_builder = DirectorContextBuilder(
    settings=config,
    max_speakers=config.DIRECTOR_MAX_SPEAKERS_PER_TURN,
)
character_scene_context_builder = CharacterSceneContextBuilder(settings=config)
director_runtime = DirectorRuntime(
    json_runtime=character_runtime,
    settings=config,
    max_speakers=config.DIRECTOR_MAX_SPEAKERS_PER_TURN,
)
director_service = DirectorService(
    character_manager=character_manager,
    character_runtime=character_runtime,
    director_runtime=director_runtime,
    template_repository=scene_template_repository,
    director_context_builder=director_context_builder,
    character_context_builder=character_scene_context_builder,
    history_dir=DIRECTOR_HISTORY_DIR,
    max_participants=config.DIRECTOR_MAX_PARTICIPANTS,
)

# FastAPI 应用
app = FastAPI(title="Umamusume-Dialogue-Server", version="0.2.0")

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

director_sessions: dict[str, SceneSession] = {}
app.include_router(
    create_director_router(
        service=director_service,
        sessions=director_sessions,
        session_ttl_seconds=config.DIRECTOR_SESSION_TTL_SECONDS,
    )
)

# 会话存储（简单内存存储，生产环境应使用数据库）
sessions = {}


# ============= 请求/响应模型 =============

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


class HistoryImportMessage(BaseModel):
    """导入历史消息"""
    role: str
    content: str = ""
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


class HistoryImportRequest(BaseModel):
    """导入历史请求"""
    session_id: str
    messages: list[HistoryImportMessage]
    replace_current: bool = True
    source: str = "manual"


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


def _story_event_metadata(
    request: DialogueRequest,
    session: DialogueSession,
) -> Dict[str, Any]:
    if not request.context_events and all(
        value is None
        for value in (
            request.speaker,
            request.event_type,
            request.target_actor_ids,
        )
    ):
        return {}

    speaker = request.speaker or default_player_actor()
    return {
        "actor": speaker.model_dump(),
        "event_type": request.event_type or "dialogue",
        "target_actor_ids": list(
            request.target_actor_ids
            if request.target_actor_ids is not None
            else [session.character.id]
        ),
        "event_schema_version": EVENT_SCHEMA_VERSION,
    }


def _append_context_events(
    session: DialogueSession,
    events: Optional[list[DialogueInputEvent]],
) -> None:
    for event in events or []:
        speaker = event.speaker or default_player_actor()
        session.add_message(
            "user",
            event.content,
            actor=speaker.model_dump(),
            event_type=event.event_type or "dialogue",
            target_actor_ids=list(
                event.target_actor_ids
                if event.target_actor_ids is not None
                else [session.character.id]
            ),
            event_schema_version=EVENT_SCHEMA_VERSION,
        )


def _character_reply_event_metadata(
    request: DialogueRequest,
    session: DialogueSession,
) -> Dict[str, Any]:
    input_metadata = _story_event_metadata(request, session)
    if not input_metadata:
        return {}
    speaker = request.speaker or default_player_actor()
    event_type = request.event_type or "dialogue"
    return {
        "actor": actor_from_character(session.character).model_dump(),
        "event_type": "dialogue",
        "target_actor_ids": (
            []
            if event_type in {"scene_event", "narration"}
            else [speaker.actor_id]
        ),
        "event_schema_version": EVENT_SCHEMA_VERSION,
    }


# ============= 会话管理 =============

def _get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _requires_api_key(request: Request) -> bool:
    if request.method == "OPTIONS":
        return False
    if not API_ACCESS_KEY:
        return False
    return request.url.path not in API_AUTH_EXEMPT_PATHS


async def _check_rate_limit(request: Request) -> Optional[JSONResponse]:
    if not API_RATE_LIMIT_ENABLED:
        return None

    path = request.url.path
    if request.method == "OPTIONS":
        return None
    if path in API_AUTH_EXEMPT_PATHS:
        return None

    client_ip = _get_client_ip(request)
    is_chat = path in _CHAT_ENDPOINTS
    bucket_key = f"{client_ip}:{'chat' if is_chat else 'default'}"
    max_requests = API_CHAT_RATE_LIMIT_MAX_REQUESTS if is_chat else API_RATE_LIMIT_MAX_REQUESTS
    now = monotonic()

    async with _rate_limit_lock:
        bucket = _rate_limit_buckets[bucket_key]
        while bucket and (now - bucket[0]) >= API_RATE_LIMIT_WINDOW_SECONDS:
            bucket.popleft()

        if len(bucket) >= max_requests:
            retry_after = max(1, int(API_RATE_LIMIT_WINDOW_SECONDS - (now - bucket[0])))
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Rate limit exceeded for {path}. Please retry later.",
                    "retry_after": retry_after,
                },
                headers={"Retry-After": str(retry_after)},
            )

        bucket.append(now)

    return None


@app.middleware("http")
async def protect_api(request: Request, call_next):
    if _requires_api_key(request):
        provided_key = request.headers.get("x-api-key", "").strip()
        if provided_key != API_ACCESS_KEY:
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing API key."})

    rate_limit_response = await _check_rate_limit(request)
    if rate_limit_response is not None:
        return rate_limit_response

    return await call_next(request)


def _supports_prefix_cache_provider() -> bool:
    return legacy_context_builder.supports_prefix_cache_provider()


def _should_attach_prefix_cache(system_prompt: str) -> bool:
    return legacy_context_builder.should_attach_prefix_cache(system_prompt)


def _extract_upstream_error_detail(exc: APIStatusError) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return f"上游模型服务返回 {exc.status_code}"

    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str) and message.strip():
                    return f"上游模型服务返回 {exc.status_code}: {message.strip()}"
            detail = payload.get("detail")
            if isinstance(detail, str) and detail.strip():
                return f"上游模型服务返回 {exc.status_code}: {detail.strip()}"
    except Exception:
        pass

    return f"上游模型服务返回 {exc.status_code}"


def _translate_llm_exception(exc: Exception) -> HTTPException:
    if isinstance(exc, APITimeoutError):
        return HTTPException(status_code=504, detail="上游模型服务超时，请稍后重试。")
    if isinstance(exc, APIConnectionError):
        return HTTPException(status_code=502, detail="无法连接到上游模型服务，请稍后重试。")
    if isinstance(exc, APIStatusError):
        status_code = exc.status_code or 502
        mapped_status = status_code if 400 <= status_code <= 599 else 502
        return HTTPException(status_code=mapped_status, detail=_extract_upstream_error_detail(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=502, detail=str(exc))
    return HTTPException(status_code=500, detail=f"对话失败: {str(exc)}")


def _extract_completion_text(response: Any) -> str:
    return CharacterRuntime.extract_completion_text(response)


def _json_output_mode() -> str:
    return _protocol_json_output_mode(config)


def _is_json_reply_enabled() -> bool:
    return _protocol_is_json_reply_enabled(config)


def _json_capability_key() -> tuple[str, str]:
    return character_runtime._json_capability_key()


def _looks_like_unsupported_response_format(exc: Exception) -> bool:
    return CharacterRuntime._looks_like_unsupported_response_format(exc)


def _sync_character_runtime_client() -> None:
    """Keep legacy tests that replace this module's client working."""
    character_runtime.llm_client = llm_client


async def _create_json_completion(
    messages: list[Dict[str, Any]],
    *,
    temperature: float,
    max_tokens: int,
    force_prompt_only: bool = False,
) -> str:
    _sync_character_runtime_client()
    return await character_runtime.create_json_completion(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        force_prompt_only=force_prompt_only,
    )


def _extract_stream_delta_text(chunk: Any) -> str:
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""

    first_choice = choices[0]
    delta = getattr(first_choice, "delta", None)
    if delta is None:
        return ""

    content = getattr(delta, "content", None)
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return str(content)


def _normalize_user_uuid(user_uuid: Optional[str]) -> str:
    if not user_uuid:
        return str(uuid4())
    try:
        return str(UUID(str(user_uuid)))
    except ValueError:
        logger.warning("Invalid user_uuid received, generate a new one: %s", user_uuid)
        return str(uuid4())


def _require_valid_user_uuid(user_uuid: str) -> str:
    try:
        return str(UUID(str(user_uuid)))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid user_uuid: {user_uuid}") from e


def _create_history_file_path(
    user_uuid: str,
    character: CharacterConfig,
    created_at: datetime,
    session_id: str,
) -> Path:
    return create_history_file_path(
        DIALOGUE_HISTORY_DIR,
        user_uuid,
        character,
        created_at,
        session_id,
    )


def _extract_character_names_from_record(record: Dict[str, Any]) -> list[str]:
    return extract_character_names_from_record(record)


def _name_tokens(names: list[str]) -> set[str]:
    return name_tokens(names)


def _resolve_character_query_names(character_name: Optional[str]) -> list[str]:
    return resolve_character_query_names(character_name, character_manager)


def _extract_safe_name_from_session_dir(session_dir_name: str) -> str:
    return extract_safe_name_from_session_dir(session_dir_name)


def _iter_user_history_files(user_uuid: str) -> list[Path]:
    return iter_user_history_files(DIALOGUE_HISTORY_DIR, user_uuid)


def _parse_history_file(
    history_file: Path,
) -> tuple[list[Dict[str, Any]], set[str]]:
    return parse_history_file(history_file)


def _collect_history_messages(
    user_uuid: str,
    character_name: Optional[str] = None,
) -> list[Dict[str, Any]]:
    return collect_history_messages(
        DIALOGUE_HISTORY_DIR,
        user_uuid,
        character_name=character_name,
        character_manager=character_manager,
    )


def _normalize_import_messages(
    raw_messages: list[HistoryImportMessage],
) -> list[Dict[str, Any]]:
    try:
        return normalize_import_messages(raw_messages)
    except InvalidHistoryImport as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _load_persistent_history(
    user_uuid: str,
    character: CharacterConfig,
) -> list[Dict[str, str]]:
    return load_persistent_history(
        DIALOGUE_HISTORY_DIR,
        user_uuid,
        character,
        history_max_messages=SESSION_HISTORY_MAX_MESSAGES,
    )


def create_session(character: CharacterConfig, user_uuid: Optional[str] = None) -> DialogueSession:
    """创建新会话"""
    session_id = str(uuid4())
    normalized_user_uuid = _normalize_user_uuid(user_uuid)
    restored_history = _load_persistent_history(normalized_user_uuid, character)
    created_at = datetime.now()
    session = DialogueSession(
        session_id,
        character,
        normalized_user_uuid,
        output_dir=_create_output_dir(character, created_at),
        history_file=_create_history_file_path(
            normalized_user_uuid,
            character,
            created_at,
            session_id,
        ),
        context_builder=legacy_context_builder,
        history_max_messages=SESSION_HISTORY_MAX_MESSAGES,
        created_at=created_at,
        initial_history=restored_history,
    )
    sessions[session_id] = session
    logger.info(
        "Created session %s for character %s (user_uuid=%s, restored=%s)",
        session_id,
        character.name_zh,
        normalized_user_uuid,
        len(restored_history),
    )
    return session


def _slugify(name: str) -> str:
    return slugify(name)


def _create_output_dir(character: CharacterConfig, created_at: datetime) -> Path:
    return voice_service.create_output_dir(character, created_at)


def _build_audio_url(path: Path) -> str:
    return voice_service.build_audio_url(path)


def _is_allowed_audio_path(path: Path) -> bool:
    return voice_service.is_allowed_audio_path(path)


async def _complete_structured_reply(messages: list[Dict[str, Any]]) -> StructuredReply:
    _sync_character_runtime_client()
    return await character_runtime.generate_reply(
        CharacterReplyContext(messages=messages)
    )


def get_session(session_id: str) -> Optional[DialogueSession]:
    """获取会话"""
    session = sessions.get(session_id)
    if not session:
        return None
    if _is_session_expired(session):
        sessions.pop(session_id, None)
        session.mark_closed("expired_on_access")
        logger.info(f"Session expired and removed on access: {session_id}")
        return None
    session.touch()
    return session


def _is_session_expired(session: DialogueSession, now: Optional[datetime] = None) -> bool:
    if SESSION_TTL_SECONDS <= 0:
        return False
    current_time = now or datetime.now()
    idle_seconds = (current_time - session.last_active_at).total_seconds()
    return idle_seconds > SESSION_TTL_SECONDS


def _cleanup_expired_sessions() -> int:
    if SESSION_TTL_SECONDS <= 0:
        return 0
    now = datetime.now()
    expired_session_ids = [
        session_id
        for session_id, session in list(sessions.items())
        if _is_session_expired(session, now=now)
    ]
    for session_id in expired_session_ids:
        session = sessions.pop(session_id, None)
        if session:
            session.mark_closed("expired_by_cleanup_worker")
    if expired_session_ids:
        logger.info(f"Cleaned up {len(expired_session_ids)} expired sessions")
    return len(expired_session_ids)


async def _session_cleanup_worker():
    logger.info(
        f"Session cleanup worker started: ttl={SESSION_TTL_SECONDS}s, interval={SESSION_CLEANUP_INTERVAL_SECONDS}s"
    )
    try:
        while True:
            await asyncio.sleep(SESSION_CLEANUP_INTERVAL_SECONDS)
            _cleanup_expired_sessions()
    except asyncio.CancelledError:
        logger.info("Session cleanup worker stopped")
        raise


def _should_generate_voice(request: DialogueRequest, session: 'DialogueSession') -> bool:
    if not ENABLE_TTS:
        return False
    if request.generate_voice and request.text_only:
        logger.info("text_only=true, skip voice generation for this request")
        return False
        
    voice_config = session.character.get_voice_config()
    if voice_config.get("no_voice") or not voice_config.get("ref_audio_path"):
        return False
        
    return request.generate_voice


# ============= API 端点 =============

@app.on_event("startup")
async def startup_session_cleanup():
    if SESSION_TTL_SECONDS <= 0:
        logger.info("Session TTL disabled, cleanup worker not started")
        app.state.session_cleanup_task = None
        return
    app.state.session_cleanup_task = asyncio.create_task(_session_cleanup_worker())


@app.on_event("shutdown")
async def shutdown_session_cleanup():
    task = getattr(app.state, "session_cleanup_task", None)
    if not task:
        for session in list(sessions.values()):
            session.mark_closed("server_shutdown")
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        for session in list(sessions.values()):
            session.mark_closed("server_shutdown")


@app.get("/")
async def root():
    """根路径"""
    return {
        "service": "Umamusume Dialogue Server",
        "version": "0.2.0",
        "status": "running"
    }


@app.get("/capabilities")
async def capabilities():
    """Expose additive protocol features for independently deployed clients."""
    return {
        "dialogue_api_version": 2,
        "dialogue_events": EVENT_SCHEMA_VERSION,
        "context_event_batch": 1,
        "director_mode": 1,
        "director_schema_version": 1,
        "director_custom_scenes": 1,
        "director_story_outline": 1,
        "director_history_resume": 1,
        "director_browser_recovery": 1,
        "director_max_participants": config.DIRECTOR_MAX_PARTICIPANTS,
        "director_max_speakers_per_turn": (
            config.DIRECTOR_MAX_SPEAKERS_PER_TURN
        ),
        "supported_event_types": [
            "dialogue",
            "action",
            "narration",
            "scene_event",
        ],
    }


@app.post("/load_character")
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
        session = create_session(character, user_uuid=request.user_uuid)
        
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
                _build_audio_url(Path(character.get_voice_config()["ref_audio_path"]))
                if ENABLE_TTS and character.get_voice_config().get("ref_audio_path")
                else None
            ),
        }
    
    except FileNotFoundError as e:
        logger.error(f"Character not found: {e}")
        raise HTTPException(status_code=404, detail=f"角色未找到: {request.character_name}。请先构建角色配置。")
    
    except Exception as e:
        logger.error(f"Failed to load character: {e}")
        raise HTTPException(status_code=500, detail=f"加载角色失败: {str(e)}")


@app.post("/chat")
async def chat(request: DialogueRequest):
    """
    发送消息并获取回复（非流式）
    
    返回: {action: str, dialogue: str, message: object, voice: object (optional)}
    """
    session = get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    try:
        _sync_character_runtime_client()
        turn_result = await dialogue_service.execute_turn(
            session=session,
            message=request.message,
            text_only=request.text_only,
            speaker=request.speaker,
            event_type=request.event_type,
            target_actor_ids=request.target_actor_ids,
            context_events=request.context_events,
        )
        result = turn_result.to_api_dict()
        
        # 生成语音（如果需要）
        if _should_generate_voice(request, session):
            voice_plan = _reserve_voice_output(session)
            voice_info = await _generate_voice_for_reply(
                session,
                turn_result.reply.dialogue,
                voice_plan,
            )
            if voice_info:
                result["voice"] = voice_info
        
        return result
    
    except Exception as e:
        logger.error(f"Chat failed: {e}")
        raise _translate_llm_exception(e)


@app.post("/chat_stream")
async def chat_stream(request: DialogueRequest):
    """
    发送消息并流式获取回复
    
    返回: SSE 流
    """
    session = get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            if _is_json_reply_enabled():
                _sync_character_runtime_client()
                turn_result = await dialogue_service.execute_turn(
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
                yield f"event: done\ndata: {{}}\n\n"

                if _should_generate_voice(request, session):
                    voice_plan = _reserve_voice_output(session)
                    voice_payload = json.dumps(voice_plan, ensure_ascii=False)
                    yield f"event: voice_pending\ndata: {voice_payload}\n\n"
                    asyncio.create_task(
                        _generate_voice_for_reply(
                            session,
                            turn_result.reply.dialogue,
                            voice_plan,
                        )
                    )
                return

            # 旧两行协议仍保持 token 流式行为。
            _append_context_events(session, request.context_events)
            session.add_message(
                "user",
                request.message,
                **_story_event_metadata(request, session),
            )
            
            # 流式调用 LLM
            stream = await llm_client.chat.completions.create(
                model=config.ROLEPLAY_LLM_MODEL_NAME,
                messages=session.get_messages(text_only=request.text_only),
                temperature=0.7,
                stream=True
            )
            
            full_reply_raw = ""
            async for chunk in stream:
                content = _extract_stream_delta_text(chunk)
                if not content:
                    continue
                full_reply_raw += content
                
                # 发送 SSE 事件
                yield f"data: {content}\n\n"

            full_reply = _normalize_structured_reply(full_reply_raw)
            structured_reply = _structured_reply_from_legacy_text(full_reply, source_format="legacy_text")
            
            # 添加完整回复到历史
            session.add_message(
                "assistant",
                structured_reply.dialogue,
                action=structured_reply.action,
                dialogue=structured_reply.dialogue,
                source_format=structured_reply.source_format,
                schema_version=structured_reply.schema_version,
                **_character_reply_event_metadata(request, session),
            )
            
            # 发送完成事件
            yield f"event: done\ndata: {{}}\n\n"

            if _should_generate_voice(request, session):
                voice_plan = _reserve_voice_output(session)
                payload = json.dumps(voice_plan, ensure_ascii=False)
                yield f"event: voice_pending\ndata: {payload}\n\n"
                asyncio.create_task(_generate_voice_for_reply(session, structured_reply.dialogue, voice_plan))
        
        except Exception as e:
            logger.error(f"Stream chat failed: {e}")
            translated = _translate_llm_exception(e)
            yield f"event: error\ndata: {translated.detail}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )


@app.get("/sessions")
async def list_sessions():
    """列出所有活跃会话"""
    _cleanup_expired_sessions()
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
        for s in sessions.values()
    ]


@app.delete("/session/{session_id}")
async def delete_session(session_id: str):
    """删除会话"""
    session = sessions.pop(session_id, None)
    if session:
        session.mark_closed("deleted_by_api")
        return {"status": "deleted", "session_id": session_id}
    else:
        raise HTTPException(status_code=404, detail="会话不存在")


@app.get("/history")
async def get_history(
    user_uuid: str,
    character_name: Optional[str] = None,
    limit: int = 200,
):
    """查询用户历史对话，支持按角色过滤。"""
    normalized_user_uuid = _require_valid_user_uuid(user_uuid)
    if limit < 0:
        raise HTTPException(status_code=400, detail="limit must be >= 0")

    all_messages = _collect_history_messages(normalized_user_uuid, character_name=character_name)
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


@app.post("/history/import")
async def import_history(request: HistoryImportRequest):
    """导入历史对话到当前 session，使其参与后续 LLM 上下文。"""
    session = get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if request.messages:
        messages = _normalize_import_messages(request.messages)
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


@app.delete("/history")
async def clear_history(user_uuid: str, character_name: str):
    """清除指定用户与指定角色的历史对话。"""
    normalized_user_uuid = _require_valid_user_uuid(user_uuid)
    query_names = _resolve_character_query_names(character_name)
    query_tokens = _name_tokens(query_names)
    if not query_tokens:
        raise HTTPException(status_code=400, detail="character_name is required")

    deleted_files = 0
    deleted_messages = 0
    for history_file in _iter_user_history_files(normalized_user_uuid):
        try:
            file_messages, file_character_names = _parse_history_file(history_file)
        except Exception:
            logger.exception("Failed to parse history file: %s", history_file)
            continue

        file_tokens = _name_tokens(list(file_character_names))
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
    for session in sessions.values():
        if session.user_uuid != normalized_user_uuid:
            continue
        session_tokens = _name_tokens(
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


@app.get("/characters")
async def list_characters():
    """列出所有可用角色"""
    try:
        characters = character_manager.list_characters()
        return {"characters": characters}
    except Exception as e:
        logger.error(f"Failed to list characters: {e}")
        raise HTTPException(status_code=500, detail=f"获取角色列表失败: {str(e)}")


@app.get("/audio")
async def get_audio(path: str):
    if not path:
        raise HTTPException(status_code=400, detail="Missing audio path")
    audio_path = Path(path)
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    if not _is_allowed_audio_path(audio_path):
        raise HTTPException(status_code=403, detail="Audio path not allowed")
    return FileResponse(audio_path)


@app.head("/audio")
async def head_audio(path: str):
    if not path:
        raise HTTPException(status_code=400, detail="Missing audio path")
    audio_path = Path(path)
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    if not _is_allowed_audio_path(audio_path):
        raise HTTPException(status_code=403, detail="Audio path not allowed")
    headers = {
        "Content-Length": str(audio_path.stat().st_size),
        "Accept-Ranges": "bytes",
    }
    return Response(status_code=200, headers=headers)


# ============= 辅助函数 =============

def _reserve_voice_output(session: DialogueSession) -> Dict[str, Any]:
    return voice_service.reserve_output(session)


async def _generate_voice_for_reply(
    session: DialogueSession,
    dialogue_text: str,
    voice_plan: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    voice_service.client = tts_client
    return await voice_service.generate_for_reply(
        session,
        dialogue_text,
        voice_plan,
    )


# ============= 启动入口 =============

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=1111)
