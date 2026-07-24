# src/umamusume_agent/config.py
import os
from pathlib import Path

from dotenv import dotenv_values


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _is_placeholder_env_value(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("<") and stripped.endswith(">")


def _load_env_defaults() -> None:
    """
    加载配置优先级：
    1. 运行时环境变量（如 HF Space Secrets）
    2. 项目根目录 .env
    3. 项目根目录 .env.template
    """
    merged_defaults: dict[str, str] = {}

    for filename in (".env.template", ".env"):
        env_path = _PROJECT_ROOT / filename
        if not env_path.exists():
            continue

        for key, value in dotenv_values(env_path).items():
            if not key or value is None:
                continue

            normalized_value = value.strip()
            if not normalized_value:
                continue
            if _is_placeholder_env_value(normalized_value):
                continue

            merged_defaults[key] = normalized_value

    for key, value in merged_defaults.items():
        os.environ.setdefault(key, value)


_load_env_defaults()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_project_path(value: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return str(path.resolve())


class Config:
    """
    配置类：集中管理所有环境变量
    """

    # ================== LLM Settings ==================
    # Roleplay LLM (用于角色扮演对话)
    ROLEPLAY_LLM_MODEL_NAME: str = os.getenv("ROLEPLAY_LLM_MODEL_NAME", "qwen-long-latest")
    ROLEPLAY_LLM_MODEL_BASE_URL: str = os.getenv("ROLEPLAY_LLM_MODEL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    ROLEPLAY_LLM_MODEL_API_KEY: str = os.getenv("ROLEPLAY_LLM_MODEL_API_KEY", "")
    ROLEPLAY_LLM_TIMEOUT_SECONDS: float = float(os.getenv("ROLEPLAY_LLM_TIMEOUT_SECONDS", "60"))
    ROLEPLAY_LLM_MAX_RETRIES: int = int(os.getenv("ROLEPLAY_LLM_MAX_RETRIES", "2"))
    LLM_JSON_ENABLED: bool = _env_bool("LLM_JSON_ENABLED", True)
    LLM_JSON_OUTPUT_MODE: str = os.getenv("LLM_JSON_OUTPUT_MODE", "auto")
    LLM_JSON_RETRY_WITHOUT_RESPONSE_FORMAT_ON_ERROR: bool = _env_bool(
        "LLM_JSON_RETRY_WITHOUT_RESPONSE_FORMAT_ON_ERROR",
        True,
    )
    LLM_JSON_PARSE_LOOSE_JSON: bool = _env_bool("LLM_JSON_PARSE_LOOSE_JSON", True)
    LLM_JSON_MAX_RETRIES: int = int(os.getenv("LLM_JSON_MAX_RETRIES", "1"))
    LLM_JSON_REGENERATE_ON_PARSE_FAILURE: bool = _env_bool("LLM_JSON_REGENERATE_ON_PARSE_FAILURE", True)
    LLM_JSON_MAX_REGENERATE_ATTEMPTS: int = int(os.getenv("LLM_JSON_MAX_REGENERATE_ATTEMPTS", "1"))
    LLM_JSON_TEMPERATURE: float = float(os.getenv("LLM_JSON_TEMPERATURE", "0.35"))
    LLM_JSON_MAX_TOKENS: int = int(os.getenv("LLM_JSON_MAX_TOKENS", "320"))

    # API protection
    API_ACCESS_KEY: str = os.getenv("API_ACCESS_KEY", "")
    API_RATE_LIMIT_ENABLED: bool = _env_bool("API_RATE_LIMIT_ENABLED", True)
    API_RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS", "60"))
    API_RATE_LIMIT_MAX_REQUESTS: int = int(os.getenv("API_RATE_LIMIT_MAX_REQUESTS", "60"))
    API_CHAT_RATE_LIMIT_MAX_REQUESTS: int = int(os.getenv("API_CHAT_RATE_LIMIT_MAX_REQUESTS", "12"))
    ENABLE_TTS: bool = _env_bool("ENABLE_TTS", False)

    # ================== TTS Settings (CosyVoice) ==================
    _cosyvoice_model_dir = os.getenv("COSYVOICE_MODEL_DIR", "./cosyvoice/pretrained_models/Fun-CosyVoice3-0.5B")
    COSYVOICE_MODEL_DIR: str = os.path.abspath(
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), _cosyvoice_model_dir.lstrip("./"))
    )

    # ================== TTS Settings (IndexTTS MCP) ==================
    INDEXTTS_MCP_URL: str = os.getenv("INDEXTTS_MCP_URL", "http://127.0.0.1:8890/mcp")
    INDEXTTS_MCP_TRANSPORT: str = os.getenv("INDEXTTS_MCP_TRANSPORT", "streamable_http")
    OUTPUTS_DIRECTORY: str = os.path.abspath(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            os.getenv("OUTPUTS_DIRECTORY", "./outputs").lstrip("./"),
        )
    )
    DIALOGUE_HISTORY_DIRECTORY: str = os.path.abspath(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            os.getenv("DIALOGUE_HISTORY_DIRECTORY", "./outputs/dialogues").lstrip("./"),
        )
    )
    DIALOGUE_SESSION_TTL_SECONDS: int = int(os.getenv("DIALOGUE_SESSION_TTL_SECONDS", "3600"))
    DIALOGUE_SESSION_HISTORY_MAX_MESSAGES: int = int(os.getenv("DIALOGUE_SESSION_HISTORY_MAX_MESSAGES", "0"))
    DIALOGUE_SESSION_CLEANUP_INTERVAL_SECONDS: int = int(os.getenv("DIALOGUE_SESSION_CLEANUP_INTERVAL_SECONDS", "60"))
    DIALOGUE_PREFIX_CACHE_ENABLED: bool = _env_bool("DIALOGUE_PREFIX_CACHE_ENABLED", True)
    DIALOGUE_PREFIX_CACHE_MIN_CHARS: int = int(os.getenv("DIALOGUE_PREFIX_CACHE_MIN_CHARS", "1000"))
    DIALOGUE_HIDDEN_FORMAT_REINJECTION_ENABLED: bool = _env_bool("DIALOGUE_HIDDEN_FORMAT_REINJECTION_ENABLED", True)
    DIALOGUE_HIDDEN_FORMAT_REINJECTION_INTERVAL_MESSAGES: int = int(
        os.getenv("DIALOGUE_HIDDEN_FORMAT_REINJECTION_INTERVAL_MESSAGES", "100")
    )
    DIRECTOR_MAX_PARTICIPANTS: int = int(os.getenv("DIRECTOR_MAX_PARTICIPANTS", "3"))
    DIRECTOR_MAX_SPEAKERS_PER_TURN: int = int(os.getenv("DIRECTOR_MAX_SPEAKERS_PER_TURN", "2"))
    DIRECTOR_LLM_TEMPERATURE: float = float(os.getenv("DIRECTOR_LLM_TEMPERATURE", "0.2"))
    DIRECTOR_LLM_MAX_TOKENS: int = int(os.getenv("DIRECTOR_LLM_MAX_TOKENS", "600"))
    DIRECTOR_JSON_REPAIR_ATTEMPTS: int = int(os.getenv("DIRECTOR_JSON_REPAIR_ATTEMPTS", "1"))
    DIRECTOR_ROLE_REINJECTION_INTERVAL_REPLIES: int = int(
        os.getenv("DIRECTOR_ROLE_REINJECTION_INTERVAL_REPLIES", "25")
    )
    DIRECTOR_SESSION_TTL_SECONDS: int = int(os.getenv("DIRECTOR_SESSION_TTL_SECONDS", "3600"))
    _scene_templates_dir = os.getenv("SCENE_TEMPLATES_DIRECTORY", "./scenes")
    SCENE_TEMPLATES_DIRECTORY: str = _resolve_project_path(_scene_templates_dir)
    DIRECTOR_HISTORY_DIRECTORY: str = _resolve_project_path(
        os.getenv("DIRECTOR_HISTORY_DIRECTORY", "./outputs/director")
    )
    # ================== Character Settings ==================
    _characters_dir = os.getenv("CHARACTERS_DIRECTORY", "./characters")
    CHARACTERS_DIRECTORY: str = os.path.abspath(
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), _characters_dir.lstrip("./"))
    )

    @classmethod
    def validate(cls):
        """
        验证必要配置是否已设置
        """
        missing = []
        if not cls.ROLEPLAY_LLM_MODEL_API_KEY:
            missing.append("ROLEPLAY_LLM_MODEL_API_KEY")

        if missing:
            raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")


# 创建一个全局实例，方便导入
config = Config()
