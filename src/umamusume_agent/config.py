# src/umamusume_agent/config.py
import os
from pathlib import Path
from typing import Optional

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

class Config:
    """
    配置类：集中管理所有环境变量
    """

    # ================== LLM Settings ==================
    # Info LLM (用于信息查询)
    INFO_LLM_MODEL_NAME: str = os.getenv("INFO_LLM_MODEL_NAME", "qwen-max-latest")
    INFO_LLM_MODEL_BASE_URL: str = os.getenv("INFO_LLM_MODEL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    INFO_LLM_MODEL_API_KEY: str = os.getenv("INFO_LLM_MODEL_API_KEY", "")
    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", INFO_LLM_MODEL_API_KEY)  # 兼容旧变量

    # Roleplay LLM (用于角色扮演对话)
    ROLEPLAY_LLM_MODEL_NAME: str = os.getenv("ROLEPLAY_LLM_MODEL_NAME", "qwen-long-latest")
    ROLEPLAY_LLM_MODEL_BASE_URL: str = os.getenv("ROLEPLAY_LLM_MODEL_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    ROLEPLAY_LLM_MODEL_API_KEY: str = os.getenv("ROLEPLAY_LLM_MODEL_API_KEY", "")

    # API protection
    API_ACCESS_KEY: str = os.getenv("API_ACCESS_KEY", "")
    API_RATE_LIMIT_ENABLED: bool = _env_bool("API_RATE_LIMIT_ENABLED", True)
    API_RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("API_RATE_LIMIT_WINDOW_SECONDS", "60"))
    API_RATE_LIMIT_MAX_REQUESTS: int = int(os.getenv("API_RATE_LIMIT_MAX_REQUESTS", "60"))
    API_CHAT_RATE_LIMIT_MAX_REQUESTS: int = int(os.getenv("API_CHAT_RATE_LIMIT_MAX_REQUESTS", "12"))

    # 默认 User-Agent
    USER_AGENT: str = os.getenv("USER_AGENT", "MyApp/1.0")

    # ================Prompt Settings ==================
    _prompt_directory = os.getenv("PROMPT_DIRECTORY", "./resources/prompts")
    PROMPT_DIRECTORY:str = os.path.abspath(
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), _prompt_directory.lstrip("./"))
    )

    # ================== RAG Settings ==================
    HF_Embedding_Model: str = os.getenv("HF_Embedding_Model", "Qwen/Qwen3-Embedding-0.6B")
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "500"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "100"))

    # 注意：RAG_DIRECTORY 是相对路径，需转为绝对路径
    _rag_dir = os.getenv("RAG_DIRECTORY", "./resources/docs")
    RAG_DIRECTORY: str = os.path.abspath(
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), _rag_dir.lstrip("./"))
    )

    # ================== Proxy Settings ==================
    PROXY_TYPE: Optional[str] = os.getenv("PROXY_TYPE")  # http, socks5 等
    PROXY_HOST: Optional[str] = os.getenv("PROXY_HOST")
    PROXY_PORT: Optional[int] = int(os.getenv("PROXY_PORT", "0")) if os.getenv("PROXY_PORT") else None

    HTTP_PROXY: Optional[str] = os.getenv("HTTP_PROXY")
    HTTPS_PROXY: Optional[str] = os.getenv("HTTPS_PROXY")

    @classmethod
    def get_proxy_dict(cls) -> dict:
        """
        返回标准的 proxy 字典，可用于 requests 或 httpx
        """
        if cls.HTTP_PROXY and cls.HTTPS_PROXY:
            return {
                "http://": cls.HTTP_PROXY,
                "https://": cls.HTTPS_PROXY,
            }
        elif cls.PROXY_TYPE and cls.PROXY_HOST and cls.PROXY_PORT:
            scheme = f"{cls.PROXY_TYPE}://{cls.PROXY_HOST}:{cls.PROXY_PORT}"
            return {
                "http://": scheme,
                "https://": scheme,
            }
        return {}

    # ================== Google Search Settings ==================
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    GOOGLE_CSE_ID: str = os.getenv("GOOGLE_CSE_ID", "")

    # ================== TTS Settings (CosyVoice) ==================
    _cosyvoice_model_dir = os.getenv("COSYVOICE_MODEL_DIR", "./cosyvoice/pretrained_models/Fun-CosyVoice3-0.5B")
    COSYVOICE_MODEL_DIR: str = os.path.abspath(
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), _cosyvoice_model_dir.lstrip("./"))
    )
    COSYVOICE_SAMPLE_RATE: int = int(os.getenv("COSYVOICE_SAMPLE_RATE", "22050"))

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
    DIALOGUE_SESSION_HISTORY_MAX_MESSAGES: int = int(os.getenv("DIALOGUE_SESSION_HISTORY_MAX_MESSAGES", "40"))
    DIALOGUE_SESSION_CLEANUP_INTERVAL_SECONDS: int = int(os.getenv("DIALOGUE_SESSION_CLEANUP_INTERVAL_SECONDS", "60"))
    DIALOGUE_PREFIX_CACHE_ENABLED: bool = _env_bool("DIALOGUE_PREFIX_CACHE_ENABLED", True)
    DIALOGUE_PREFIX_CACHE_MIN_CHARS: int = int(os.getenv("DIALOGUE_PREFIX_CACHE_MIN_CHARS", "1000"))

    # ================== Character Settings ==================
    _characters_dir = os.getenv("CHARACTERS_DIRECTORY", "./characters")
    CHARACTERS_DIRECTORY: str = os.path.abspath(
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), _characters_dir.lstrip("./"))
    )
    
    # 角色音频资源目录
    _voice_dir = os.getenv("VOICE_DIRECTORY", "./resources/voice")
    VOICE_DIRECTORY: str = os.path.abspath(
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), _voice_dir.lstrip("./"))
    )

    @classmethod
    def validate(cls):
        """
        验证必要配置是否已设置
        """
        missing = []
        if not cls.INFO_LLM_MODEL_API_KEY and not cls.DASHSCOPE_API_KEY:
            missing.append("INFO_LLM_MODEL_API_KEY or DASHSCOPE_API_KEY")
        if not cls.ROLEPLAY_LLM_MODEL_API_KEY:
            missing.append("ROLEPLAY_LLM_MODEL_API_KEY")
        # if not cls.GOOGLE_API_KEY:
        #     missing.append("GOOGLE_API_KEY")
        # if not cls.GOOGLE_CSE_ID:
        #     missing.append("GOOGLE_CSE_ID")

        if missing:
            raise EnvironmentError(f"Missing required environment variables: {', '.join(missing)}")

# 创建一个全局实例，方便导入
config = Config()
