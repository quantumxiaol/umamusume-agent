"""
Test IndexTTS MCP tools directly.

python tests/test_tts_mcp_tool.py --base-url http://127.0.0.1:8890/mcp \
  --prompt-path "result-voices/Admire Vega/Admire Vega_1.mp3" \
  --text-dir "/Users/quantumxiaol/Desktop/dev/index-tts/inputs"
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from langchain_mcp_adapters.tools import load_mcp_tools


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


async def _open_session(base_url: str, transport: str):
    transport = transport.lower()
    if transport in {"sse", "sse_http"}:
        context = sse_client(base_url)
        read, write = await context.__aenter__()
        return read, write, context

    context = streamablehttp_client(base_url)
    read, write, _session_id = await context.__aenter__()
    return read, write, context


async def _close_session(context):
    await context.__aexit__(None, None, None)


async def async_main(base_url: str, prompt_path: str, transport: str, text_dir: str) -> None:
    read, write, context = await _open_session(base_url, transport)
    try:
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await load_mcp_tools(session)
            print("Available tools:", [tool.name for tool in tools])

            synth_args = {
                "text": "训练员你好，这是 IndexTTS MCP 工具测试。",
                "prompt_wav_path": prompt_path,
                "output_name": "test_tts_synthesize.wav",
            }
            synth_result = await session.call_tool("tts_synthesize", synth_args)
            print("tts_synthesize result:", _extract_result_payload(synth_result))

            if text_dir:
                batch_dir = Path(text_dir).expanduser()
                batch_dir.mkdir(parents=True, exist_ok=True)
                batch_file = batch_dir / "test_tts_batch.txt"
                batch_file.write_text("第一句测试。\\n第二句测试。\\n", encoding="utf-8")

                batch_args = {
                    "text_file": str(batch_file.resolve()),
                    "prompt_wav_path": prompt_path,
                    "output_prefix": "test_tts_batch",
                }
                batch_result = await session.call_tool("tts_batch_file", batch_args)
                print("tts_batch_file result:", _extract_result_payload(batch_result))
            else:
                print("Skip tts_batch_file: text dir not provided or not found.")
    finally:
        await _close_session(context)


def main() -> int:
    parser = argparse.ArgumentParser(description="Test IndexTTS MCP tools.")
    parser.add_argument("--base-url", default=os.getenv("INDEXTTS_MCP_URL", "http://127.0.0.1:8890/mcp"))
    parser.add_argument("--prompt-path", default="result-voices/Admire Vega/Admire Vega_1.mp3")
    parser.add_argument("--transport", default=os.getenv("INDEXTTS_MCP_TRANSPORT", "streamable_http"))
    parser.add_argument(
        "--text-dir",
        default=os.getenv("INDEXTTS_INPUT_DIR", os.getenv("TTS_INPUT_DIR", "")),
        help="IndexTTS MCP inputs directory (for batch text file).",
    )
    args = parser.parse_args()

    prompt_path = Path(args.prompt_path)
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt audio not found: {prompt_path}")

    text_dir = args.text_dir.strip()
    asyncio.run(async_main(args.base_url, str(prompt_path.resolve()), args.transport, text_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
