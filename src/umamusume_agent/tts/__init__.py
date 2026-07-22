"""
Text-to-Speech helpers.
"""

from .mcp_client import IndexTTSMCPClient, IndexTTSMCPConfig, MCPToolError
from .service import VoiceService

try:
    from .engine import TTSEngine, CosyVoice3Engine, get_tts_engine
except Exception:  # pragma: no cover - optional dependency
    TTSEngine = None  # type: ignore[assignment]
    CosyVoice3Engine = None  # type: ignore[assignment]
    get_tts_engine = None  # type: ignore[assignment]

__all__ = [
    "IndexTTSMCPClient",
    "IndexTTSMCPConfig",
    "MCPToolError",
    "VoiceService",
    "TTSEngine",
    "CosyVoice3Engine",
    "get_tts_engine",
]
