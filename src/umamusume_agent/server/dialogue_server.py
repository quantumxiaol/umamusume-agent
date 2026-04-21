"""
Dialogue Server - 交互式角色对话服务
提供基于角色人格的对话API和语音生成
"""

import asyncio
import json
import logging
import re
import shutil
from collections import defaultdict, deque
from urllib.parse import quote
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
from ..tts import IndexTTSMCPClient, MCPToolError
from ..config import config

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
CHARACTERS_DIR = Path(config.CHARACTERS_DIRECTORY)
SESSION_TTL_SECONDS = max(0, config.DIALOGUE_SESSION_TTL_SECONDS)
SESSION_HISTORY_MAX_MESSAGES = max(0, config.DIALOGUE_SESSION_HISTORY_MAX_MESSAGES)
SESSION_CLEANUP_INTERVAL_SECONDS = max(5, config.DIALOGUE_SESSION_CLEANUP_INTERVAL_SECONDS)
PREFIX_CACHE_ENABLED = config.DIALOGUE_PREFIX_CACHE_ENABLED
PREFIX_CACHE_MIN_CHARS = max(0, config.DIALOGUE_PREFIX_CACHE_MIN_CHARS)
ROLEPLAY_BASE_URL = (config.ROLEPLAY_LLM_MODEL_BASE_URL or "").lower()
ROLEPLAY_MODEL_NAME = (config.ROLEPLAY_LLM_MODEL_NAME or "").lower()
API_ACCESS_KEY = (config.API_ACCESS_KEY or "").strip()
API_RATE_LIMIT_ENABLED = config.API_RATE_LIMIT_ENABLED
API_RATE_LIMIT_WINDOW_SECONDS = max(1, config.API_RATE_LIMIT_WINDOW_SECONDS)
API_RATE_LIMIT_MAX_REQUESTS = max(1, config.API_RATE_LIMIT_MAX_REQUESTS)
API_CHAT_RATE_LIMIT_MAX_REQUESTS = max(1, config.API_CHAT_RATE_LIMIT_MAX_REQUESTS)
ENABLE_TTS = config.ENABLE_TTS
API_AUTH_EXEMPT_PATHS = {"/", "/audio"}
_CHAT_ENDPOINTS = {"/chat", "/chat_stream"}
_rate_limit_buckets: dict[str, deque[float]] = defaultdict(deque)
_rate_limit_lock = asyncio.Lock()

_STRUCTURED_RESPONSE_FORMAT_INSTRUCTION = (
    "【回复格式硬性规范】\n"
    "你正在进行沉浸式角色扮演。请始终使用中文，并且只输出两行，顺序固定如下：\n"
    "动作：<描写角色动作、神态或心理活动；简洁；不写台词>\n"
    "对白：<角色说的话；只写口语台词；不写动作或旁白>\n\n"
    "【输出边界】\n"
    "1) 必须包含且只包含这两行。\n"
    "2) 不要添加额外标题、编号、解释或第三行。\n"
    "3) 每行必须以完整标签“动作：”和“对白：”开头。\n"
    "4) 对白将直接用于 TTS，请保证自然可朗读。\n\n"
    "【正确示例（模板）】\n"
    "动作：【角色】耳朵轻轻抖动。\n"
    "对白：我是【角色名】，目标是成为优秀的赛马娘。"
)

_PLAIN_TEXT_RESPONSE_FORMAT_INSTRUCTION = (
    "本次不需要生成语音文件，但输出格式仍必须是“动作：”和“对白：”两行。"
)

_ACTION_PREFIXES = ("动作：", "动作:", "神态：", "神态:", "场景：", "场景:")
_DIALOGUE_PREFIXES = (
    "对白：", "对白:",
    "台词：", "台词:",
    "对话：", "对话:",
    "TTS：", "TTS:",
)
_ACTION_LABELS = {"动作", "神态", "场景", "神情", "表情"}
_DIALOGUE_LABELS = {"对白", "台词", "对话", "tts", "dialogue", "speech"}
_LABELLED_LINE_PATTERN = re.compile(r"^(?P<label>[\u4e00-\u9fffA-Za-z]{1,8})[：:]\s*(?P<content>.*)$")
_INLINE_SECOND_LABEL_PATTERN = re.compile(
    r"^(?P<action>.*?[。！？；;…])\s*(?P<label>[\u4e00-\u9fffA-Za-z]{1,8})[：:]\s*(?P<dialogue>.+)$"
)

_STAGE_PATTERNS = [
    r"\\*[^\\*]+\\*",
    r"（[^）]*）",
    r"\\([^)]*\\)",
    r"【[^】]*】",
    r"\\[[^\\]]*]",
    r"〔[^〕]*〕",
    r"＜[^＞]*＞",
    r"<[^>]*>",
    r"《[^》]*》",
]
_SESSION_DIR_NAME_PATTERN = re.compile(r"^(?P<safe_name>.+)_\d{8}_\d{6}_[0-9a-fA-F]{8}$")

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


# ============= 会话管理 =============

def _get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _requires_api_key(request: Request) -> bool:
    if not API_ACCESS_KEY:
        return False
    return request.url.path not in API_AUTH_EXEMPT_PATHS


async def _check_rate_limit(request: Request) -> Optional[JSONResponse]:
    if not API_RATE_LIMIT_ENABLED:
        return None

    path = request.url.path
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
    if "dashscope.aliyuncs.com" in ROLEPLAY_BASE_URL:
        return True
    if "bailian" in ROLEPLAY_BASE_URL:
        return True
    if ROLEPLAY_MODEL_NAME.startswith("qwen"):
        return True
    return False


def _should_attach_prefix_cache(system_prompt: str) -> bool:
    if not PREFIX_CACHE_ENABLED:
        return False
    if not _supports_prefix_cache_provider():
        return False
    return len(system_prompt) >= PREFIX_CACHE_MIN_CHARS


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
    return HTTPException(status_code=500, detail=f"对话失败: {str(exc)}")


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
    safe_name = _slugify(character.name_en or character.name_zh)
    timestamp = created_at.strftime("%Y%m%d_%H%M%S")
    session_dir = DIALOGUE_HISTORY_DIR / user_uuid / f"{safe_name}_{timestamp}_{session_id[:8]}"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir / "history.jsonl"


def _extract_character_names_from_record(record: Dict[str, Any]) -> list[str]:
    names = []
    for key in ("character_name_en", "character_name_zh", "character_name_jp"):
        value = record.get(key)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped:
                names.append(stripped)
    return names


def _name_tokens(names: list[str]) -> set[str]:
    tokens: set[str] = set()
    for name in names:
        if not isinstance(name, str):
            continue
        stripped = name.strip()
        if not stripped:
            continue
        tokens.add(stripped.lower())
        tokens.add(_slugify(stripped))
    return tokens


def _resolve_character_query_names(character_name: Optional[str]) -> list[str]:
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
                with config_path.open("r", encoding="utf-8") as f:
                    config_data = json.load(f)
                for key in ("name_zh", "name_en", "name_jp"):
                    value = config_data.get(key)
                    if isinstance(value, str) and value.strip():
                        names.append(value.strip())
                names.append(character_dir.name)
    except Exception:
        logger.exception("Failed to resolve character aliases for query: %s", query)

    # 去重，保留顺序
    seen = set()
    deduped: list[str] = []
    for name in names:
        key = name.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(name)
    return deduped


def _extract_safe_name_from_session_dir(session_dir_name: str) -> str:
    match = _SESSION_DIR_NAME_PATTERN.match(session_dir_name)
    if match:
        return match.group("safe_name")
    return session_dir_name


def _iter_user_history_files(user_uuid: str) -> list[Path]:
    user_dir = DIALOGUE_HISTORY_DIR / user_uuid
    if not user_dir.exists():
        return []
    files = [path for path in user_dir.glob("*/history.jsonl") if path.is_file()]
    files.sort(key=lambda path: path.parent.name)
    return files


def _parse_history_file(history_file: Path) -> tuple[list[Dict[str, Any]], set[str]]:
    fallback_safe_name = _extract_safe_name_from_session_dir(history_file.parent.name)
    character_names: set[str] = {fallback_safe_name} if fallback_safe_name else set()
    messages: list[Dict[str, Any]] = []

    with history_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skip invalid history line: %s", history_file)
                continue

            for name in _extract_character_names_from_record(record):
                character_names.add(name)

            if record.get("event") != "message":
                continue
            role = record.get("role")
            content = record.get("content")
            if role not in {"user", "assistant"}:
                continue
            if not isinstance(content, str) or not content.strip():
                continue

            message_character_name = record.get("character_name_en")
            if not isinstance(message_character_name, str) or not message_character_name.strip():
                extracted_names = _extract_character_names_from_record(record)
                if extracted_names:
                    message_character_name = extracted_names[0]
                else:
                    message_character_name = fallback_safe_name

            messages.append(
                {
                    "session_id": record.get("session_id"),
                    "role": role,
                    "content": content,
                    "timestamp": record.get("timestamp"),
                    "message_index": record.get("message_index"),
                    "character_name_en": message_character_name,
                }
            )

    return messages, character_names


def _collect_history_messages(user_uuid: str, character_name: Optional[str] = None) -> list[Dict[str, Any]]:
    query_tokens = _name_tokens(_resolve_character_query_names(character_name))
    messages: list[Dict[str, Any]] = []

    for history_file in _iter_user_history_files(user_uuid):
        try:
            file_messages, file_character_names = _parse_history_file(history_file)
        except Exception:
            logger.exception("Failed to parse history file: %s", history_file)
            continue

        if query_tokens:
            file_tokens = _name_tokens(list(file_character_names))
            if not (file_tokens & query_tokens):
                continue

        messages.extend(file_messages)

    def _message_sort_key(item: Dict[str, Any]) -> tuple[str, str, int]:
        raw_index = item.get("message_index")
        try:
            message_index = int(raw_index or 0)
        except (TypeError, ValueError):
            message_index = 0
        return (
            str(item.get("timestamp") or ""),
            str(item.get("session_id") or ""),
            message_index,
        )

    messages.sort(key=_message_sort_key)
    return messages


def _load_persistent_history(user_uuid: str, character: CharacterConfig) -> list[Dict[str, str]]:
    """
    按 user_uuid + 角色聚合历史消息，用于在新会话中恢复上下文记忆。
    """
    user_dir = DIALOGUE_HISTORY_DIR / user_uuid
    if not user_dir.exists():
        return []

    safe_name = _slugify(character.name_en or character.name_zh)
    history_files = sorted(
        [path for path in user_dir.glob(f"{safe_name}_*/history.jsonl") if path.is_file()],
        key=lambda path: path.parent.name,
    )

    expected_character_name = character.name_en or character.name_zh
    messages: list[Dict[str, str]] = []
    for history_file in history_files:
        try:
            with history_file.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning("Skip invalid history line: %s", history_file)
                        continue

                    if record.get("event") != "message":
                        continue
                    recorded_character_name = record.get("character_name_en")
                    if (
                        isinstance(recorded_character_name, str)
                        and recorded_character_name.strip()
                        and recorded_character_name != expected_character_name
                    ):
                        continue
                    role = record.get("role")
                    content = record.get("content")
                    if role not in {"user", "assistant"}:
                        continue
                    if not isinstance(content, str) or not content.strip():
                        continue
                    messages.append({"role": role, "content": content})
        except Exception:
            logger.exception("Failed to load history file: %s", history_file)

    if SESSION_HISTORY_MAX_MESSAGES > 0 and len(messages) > SESSION_HISTORY_MAX_MESSAGES:
        messages = messages[-SESSION_HISTORY_MAX_MESSAGES:]

    return messages


class DialogueSession:
    """对话会话"""

    def __init__(
        self,
        session_id: str,
        character: CharacterConfig,
        user_uuid: str,
        initial_history: Optional[list[Dict[str, str]]] = None,
    ):
        self.session_id = session_id
        self.user_uuid = user_uuid
        self.character = character
        self.created_at = datetime.now()
        self.last_active_at = self.created_at
        self.history = list(initial_history or [])
        self.message_count = len(self.history)
        self.voice_index = 0
        self.output_dir = _create_output_dir(character, self.created_at)
        self.audio_history: list[Dict[str, Any]] = []
        self.history_file = _create_history_file_path(user_uuid, character, self.created_at, session_id)
        self._closed = False
        self._trim_history()

        self._append_history_event(
            {
                "event": "session_start",
                "created_at": self.created_at.isoformat(),
                "restored_history_messages": len(self.history),
            }
        )

    def _append_history_event(self, payload: Dict[str, Any]):
        record: Dict[str, Any] = {
            "session_id": self.session_id,
            "user_uuid": self.user_uuid,
            "character_name_en": self.character.name_en or self.character.name_zh,
            "timestamp": datetime.now().isoformat(),
        }
        record.update(payload)

        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with self.history_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("Failed to persist dialogue history: session_id=%s", self.session_id)

    def mark_closed(self, reason: str):
        if self._closed:
            return
        self._closed = True
        self._append_history_event({"event": "session_end", "reason": reason})

    def touch(self):
        self.last_active_at = datetime.now()

    def _trim_history(self):
        if SESSION_HISTORY_MAX_MESSAGES <= 0:
            return
        overflow = len(self.history) - SESSION_HISTORY_MAX_MESSAGES
        if overflow > 0:
            del self.history[:overflow]

    def add_message(self, role: str, content: str):
        """添加消息到历史"""
        self.history.append({"role": role, "content": content})
        self.message_count += 1
        self.touch()
        self._trim_history()
        self._append_history_event(
            {
                "event": "message",
                "role": role,
                "content": content,
                "message_index": self.message_count,
            }
        )

    def get_messages(self, text_only: bool = False) -> list:
        """获取完整消息列表（包含系统提示）"""
        response_instruction = _STRUCTURED_RESPONSE_FORMAT_INSTRUCTION
        if text_only:
            # text_only 仅代表不生成语音，不改变输出结构
            response_instruction = f"{response_instruction}\n{_PLAIN_TEXT_RESPONSE_FORMAT_INSTRUCTION}"
        system_prompt = f"{self.character.get_system_prompt()}\n\n{response_instruction}"
        system_message: Dict[str, Any] = {
            "role": "system",
            "content": system_prompt,
        }
        if _should_attach_prefix_cache(system_prompt):
            system_message["cache_control"] = {"type": "ephemeral"}
        messages = [system_message]
        messages.extend(self.history)
        return messages


def create_session(character: CharacterConfig, user_uuid: Optional[str] = None) -> DialogueSession:
    """创建新会话"""
    session_id = str(uuid4())
    normalized_user_uuid = _normalize_user_uuid(user_uuid)
    restored_history = _load_persistent_history(normalized_user_uuid, character)
    session = DialogueSession(
        session_id,
        character,
        normalized_user_uuid,
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
    return name.strip().replace(" ", "_").replace("　", "_").lower()


def _create_output_dir(character: CharacterConfig, created_at: datetime) -> Path:
    safe_name = _slugify(character.name_en or character.name_zh)
    timestamp = created_at.strftime("%Y%m%d_%H%M%S")
    base_name = f"{safe_name}_{timestamp}"
    output_dir = OUTPUTS_DIR / base_name
    if output_dir.exists():
        counter = 1
        while (OUTPUTS_DIR / f"{base_name}_{counter}").exists():
            counter += 1
        output_dir = OUTPUTS_DIR / f"{base_name}_{counter}"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _build_audio_url(path: Path) -> str:
    return f"/audio?path={quote(str(path))}"


def _is_allowed_audio_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
        return resolved.is_relative_to(OUTPUTS_DIR) or resolved.is_relative_to(CHARACTERS_DIR)
    except Exception:
        return False


def _strip_stage_directions(text: str) -> str:
    cleaned = text
    for pattern in _STAGE_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned)
    lines = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.fullmatch(r"[~!！。，、.…\\-—·\\s]+", stripped):
            continue
        lines.append(stripped)
    return " ".join(lines).strip()


def _parse_labelled_line(line: str) -> tuple[str, str]:
    for prefix in _ACTION_PREFIXES:
        if line.startswith(prefix):
            return "action", line[len(prefix):].strip()
    for prefix in _DIALOGUE_PREFIXES:
        if line.startswith(prefix):
            return "dialogue", line[len(prefix):].strip()

    match = _LABELLED_LINE_PATTERN.match(line)
    if not match:
        return "none", line.strip()

    label = match.group("label").strip().lower()
    content = match.group("content").strip()
    if label in _ACTION_LABELS:
        return "action", content
    if label in _DIALOGUE_LABELS:
        return "dialogue", content
    return "unknown", content


def _split_action_payload_by_inline_label(payload: str) -> tuple[str, str]:
    match = _INLINE_SECOND_LABEL_PATTERN.match(payload.strip())
    if not match:
        return "", ""

    action = match.group("action").strip()
    label = match.group("label").strip().lower()
    dialogue = match.group("dialogue").strip()
    if not action or not dialogue:
        return "", ""
    if label in _ACTION_LABELS:
        return "", ""

    # 结构容错：只要出现第二个“短标签:内容”段，就按对白处理
    return action, dialogue


def _split_action_payload(payload: str) -> tuple[str, str]:
    if not payload:
        return "", ""

    for marker in _DIALOGUE_PREFIXES:
        if marker in payload:
            left, right = payload.split(marker, 1)
            return left.strip(), right.strip()

    inline_action, inline_dialogue = _split_action_payload_by_inline_label(payload)
    if inline_dialogue:
        return inline_action, inline_dialogue

    quote_match = re.search(r"[「“\"]([^」”\"]+)[」”\"]", payload)
    if quote_match:
        dialogue = quote_match.group(1).strip()
        action = (payload[:quote_match.start()] + payload[quote_match.end():]).strip(" ，。;；")
        if dialogue:
            return action, dialogue

    sentence_split = re.search(r"[。！？；;…](?=.)", payload)
    if sentence_split:
        split_idx = sentence_split.end()
        action = payload[:split_idx].strip()
        dialogue = payload[split_idx:].strip()
        if dialogue:
            return action, dialogue

    keyword_patterns = ("我是", "我叫", "我会", "我必须", "我不", "我想", "我现在", "你", "训练员")
    candidate_indexes = []
    for pat in keyword_patterns:
        idx = payload.find(pat)
        if idx > 6:
            candidate_indexes.append(idx)
    if candidate_indexes:
        split_idx = min(candidate_indexes)
        action = payload[:split_idx].strip(" ，。;；")
        dialogue = payload[split_idx:].strip()
        if action and dialogue:
            return action, dialogue

    return payload.strip(), ""


def _split_action_dialogue(reply: str) -> tuple[str, str]:
    if not reply:
        return "", ""

    action_lines: list[str] = []
    dialogue_lines: list[str] = []
    unmarked_lines: list[str] = []
    capture_dialogue = False
    has_marker = False

    for raw_line in reply.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        label_kind, content = _parse_labelled_line(line)
        if label_kind == "action":
            has_marker = True
            capture_dialogue = False
            if content:
                action_lines.append(content)
            continue

        if label_kind == "dialogue":
            has_marker = True
            capture_dialogue = True
            if content:
                dialogue_lines.append(content)
            continue

        if label_kind == "unknown" and has_marker:
            # 已经出现结构标签后，后续未知短标签更可能是“对白标签变体”
            has_marker = True
            capture_dialogue = True
            if content:
                dialogue_lines.append(content)
            continue

        if capture_dialogue:
            dialogue_lines.append(line)
        elif has_marker:
            action_lines.append(line)
        else:
            unmarked_lines.append(line)

    action_text = " ".join([x for x in action_lines if x]).strip()
    dialogue_text = " ".join([x for x in dialogue_lines if x]).strip()
    if dialogue_text:
        return action_text, dialogue_text

    if action_text and not dialogue_text:
        inline_action, inline_dialogue = _split_action_payload(action_text)
        if inline_dialogue:
            return inline_action, inline_dialogue
        return inline_action, ""

    if unmarked_lines:
        return "", " ".join(unmarked_lines).strip()

    return "", reply.strip()


def _normalize_structured_reply(reply: str) -> str:
    action_text, dialogue_text = _split_action_dialogue(reply)
    if not dialogue_text:
        dialogue_text = _strip_stage_directions(reply)
    if not dialogue_text:
        dialogue_text = reply.strip()
    if not action_text:
        action_text = "无"
    return f"动作：{action_text}\n对白：{dialogue_text}"


def _extract_dialogue_text(text: str) -> str:
    _, dialogue_text = _split_action_dialogue(text)
    if dialogue_text:
        return dialogue_text
    cleaned = _strip_stage_directions(text)
    return cleaned.strip()


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
    
    返回: {reply: str, voice_url: str (optional)}
    """
    session = get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    try:
        # 添加用户消息
        session.add_message("user", request.message)
        
        # 调用 LLM
        response = await llm_client.chat.completions.create(
            model=config.ROLEPLAY_LLM_MODEL_NAME,
            messages=session.get_messages(text_only=request.text_only),
            temperature=0.7
        )
        
        reply_raw = response.choices[0].message.content or ""
        reply = _normalize_structured_reply(reply_raw)
        
        # 添加助手回复
        session.add_message("assistant", reply)
        
        result = {"reply": reply}
        
        # 生成语音（如果需要）
        if _should_generate_voice(request, session):
            voice_plan = _reserve_voice_output(session)
            voice_info = await _generate_voice_for_reply(session, reply, voice_plan)
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
            # 添加用户消息
            session.add_message("user", request.message)
            
            # 流式调用 LLM
            stream = await llm_client.chat.completions.create(
                model=config.ROLEPLAY_LLM_MODEL_NAME,
                messages=session.get_messages(text_only=request.text_only),
                temperature=0.7,
                stream=True
            )
            
            full_reply_raw = ""
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_reply_raw += content
                    
                    # 发送 SSE 事件
                    yield f"data: {content}\n\n"

            full_reply = _normalize_structured_reply(full_reply_raw)
            
            # 添加完整回复到历史
            session.add_message("assistant", full_reply)
            
            # 发送完成事件
            yield f"event: done\ndata: {{}}\n\n"

            if _should_generate_voice(request, session):
                voice_plan = _reserve_voice_output(session)
                payload = json.dumps(voice_plan, ensure_ascii=False)
                yield f"event: voice_pending\ndata: {payload}\n\n"
                asyncio.create_task(_generate_voice_for_reply(session, full_reply, voice_plan))
        
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
    session.voice_index += 1
    output_name = f"reply_{session.voice_index:03d}.wav"
    target_path = session.output_dir / output_name
    return {
        "audio_path": str(target_path),
        "audio_url": _build_audio_url(target_path),
        "index": session.voice_index,
        "output_dir": str(session.output_dir),
        "output_name": output_name,
    }


async def _generate_voice_for_reply(
    session: DialogueSession,
    text: str,
    voice_plan: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    try:
        voice_config = session.character.get_voice_config()
        if voice_config.get("no_voice") or not voice_config.get("ref_audio_path"):
            logger.info(f"Skipping TTS generation for session {session.session_id}: no_voice is True or ref_audio_path is missing")
            return None
            
        prompt_audio_path = voice_config["ref_audio_path"]
        output_name = voice_plan["output_name"]
        target_path = Path(voice_plan["audio_path"])
        tts_text = _extract_dialogue_text(text)
        if not tts_text:
            tts_text = _strip_stage_directions(text)
        if not tts_text:
            tts_text = text

        result = await tts_client.synthesize(
            text=tts_text,
            prompt_wav_path=prompt_audio_path,
            output_name=output_name,
        )

        source_path = Path(result.get("audio_path", ""))
        if not source_path.exists():
            raise FileNotFoundError(f"TTS output not found: {source_path}")

        if source_path.resolve() != target_path.resolve():
            if target_path.exists():
                target_path.unlink()
            shutil.move(str(source_path), str(target_path))

        voice_info = {
            "audio_path": str(target_path),
            "audio_url": _build_audio_url(target_path),
            "prompt_audio_path": result.get("prompt_audio_path"),
            "sample_rate": result.get("sample_rate"),
            "index": voice_plan["index"],
            "output_dir": voice_plan["output_dir"],
            "tts_text": tts_text,
        }
        session.audio_history.append(voice_info)
        return voice_info

    except (MCPToolError, FileNotFoundError) as e:
        logger.error(f"TTS MCP error: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to generate voice: {e}")
        return None


# ============= 启动入口 =============

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=1111)
