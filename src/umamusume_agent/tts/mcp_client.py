"""
IndexTTS MCP client helpers.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

from ..config import config

logger = logging.getLogger(__name__)


class MCPToolError(RuntimeError):
    """Raised when MCP tool call fails."""


@dataclass(frozen=True)
class IndexTTSMCPConfig:
    base_url: str = config.INDEXTTS_MCP_URL
    transport: str = config.INDEXTTS_MCP_TRANSPORT


def _maybe_parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def _extract_result_payload(result: Any) -> Dict[str, Any]:
    if isinstance(result, dict) and "content" not in result:
        return result

    content = None
    if isinstance(result, dict):
        content = result.get("content")
    elif hasattr(result, "content"):
        content = getattr(result, "content")

    if content is None:
        return {"raw": result}

    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, dict):
            if "json" in first:
                return first["json"]
            if "text" in first:
                parsed = _maybe_parse_json(first["text"])
                return parsed if isinstance(parsed, dict) else {"text": parsed}
        if hasattr(first, "text"):
            parsed = _maybe_parse_json(first.text)
            return parsed if isinstance(parsed, dict) else {"text": parsed}
        if isinstance(first, str):
            parsed = _maybe_parse_json(first)
            return parsed if isinstance(parsed, dict) else {"text": parsed}

    if isinstance(content, str):
        parsed = _maybe_parse_json(content)
        return parsed if isinstance(parsed, dict) else {"text": parsed}

    return {"raw": result}


class IndexTTSMCPClient:
    def __init__(self, mcp_config: Optional[IndexTTSMCPConfig] = None):
        self._config = mcp_config or IndexTTSMCPConfig()

    async def _open_session(self) -> Tuple[Any, Any]:
        transport = (self._config.transport or "streamable_http").lower()
        if transport in {"sse", "sse_http"}:
            context = sse_client(self._config.base_url)
            read, write = await context.__aenter__()
            return (read, write, context)

        context = streamablehttp_client(self._config.base_url)
        read, write, _session_id = await context.__aenter__()
        return (read, write, context)

    async def _close_session(self, context: Any) -> None:
        try:
            await context.__aexit__(None, None, None)
        except Exception:
            logger.exception("Failed to close MCP client context")

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        read, write, context = await self._open_session()
        try:
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)

            payload = _extract_result_payload(result)
            if getattr(result, "is_error", False):
                raise MCPToolError(f"MCP tool {name} returned error: {payload}")
            return payload
        finally:
            await self._close_session(context)

    async def synthesize(
        self,
        text: str,
        prompt_wav_path: str,
        output_name: Optional[str] = None,
        emo_audio_prompt: Optional[str] = None,
        emo_alpha: float = 1.0,
        emo_vector: Optional[Sequence[float]] = None,
        use_emo_text: bool = False,
        emo_text: Optional[str] = None,
        use_random: bool = False,
        interval_silence: int = 200,
        max_text_tokens_per_segment: int = 120,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        arguments: Dict[str, Any] = {
            "text": text,
            "prompt_wav_path": prompt_wav_path,
            "output_name": output_name,
            "emo_audio_prompt": emo_audio_prompt,
            "emo_alpha": emo_alpha,
            "emo_vector": list(emo_vector) if emo_vector is not None else None,
            "use_emo_text": use_emo_text,
            "emo_text": emo_text,
            "use_random": use_random,
            "interval_silence": interval_silence,
            "max_text_tokens_per_segment": max_text_tokens_per_segment,
            "verbose": verbose,
        }
        return await self.call_tool("tts_synthesize", arguments)

    async def batch_file(
        self,
        text_file: str,
        prompt_wav_path: str,
        output_prefix: Optional[str] = None,
        emo_audio_prompt: Optional[str] = None,
        emo_alpha: float = 1.0,
        emo_vector: Optional[Sequence[float]] = None,
        use_emo_text: bool = False,
        emo_text: Optional[str] = None,
        use_random: bool = False,
        interval_silence: int = 200,
        max_text_tokens_per_segment: int = 120,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        arguments: Dict[str, Any] = {
            "text_file": text_file,
            "prompt_wav_path": prompt_wav_path,
            "output_prefix": output_prefix,
            "emo_audio_prompt": emo_audio_prompt,
            "emo_alpha": emo_alpha,
            "emo_vector": list(emo_vector) if emo_vector is not None else None,
            "use_emo_text": use_emo_text,
            "emo_text": emo_text,
            "use_random": use_random,
            "interval_silence": interval_silence,
            "max_text_tokens_per_segment": max_text_tokens_per_segment,
            "verbose": verbose,
        }
        return await self.call_tool("tts_batch_file", arguments)
