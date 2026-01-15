"""
IndexTTS MCP smoke test using local prompt audio.

python tests/test_tts.py --prompt-path "result-voices/Admire Vega/Admire Vega_1.mp3"
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.umamusume_agent.tts import IndexTTSMCPClient, IndexTTSMCPConfig


async def async_main(prompt_path: str, text: str, base_url: str, transport: str) -> None:
    client = IndexTTSMCPClient(IndexTTSMCPConfig(base_url=base_url, transport=transport))

    result = await client.synthesize(
        text=text,
        prompt_wav_path=prompt_path,
        output_name="test_tts_client.wav",
    )
    print("IndexTTS MCP result:", result)


def main() -> int:
    parser = argparse.ArgumentParser(description="IndexTTS MCP smoke test.")
    parser.add_argument("--prompt-path", default="result-voices/Admire Vega/Admire Vega_1.mp3")
    parser.add_argument("--text", default="训练员你好，这是 IndexTTS MCP 客户端测试。")
    parser.add_argument("--base-url", default=os.getenv("INDEXTTS_MCP_URL", "http://127.0.0.1:8890/mcp"))
    parser.add_argument("--transport", default=os.getenv("INDEXTTS_MCP_TRANSPORT", "streamable_http"))
    args = parser.parse_args()

    prompt_path = Path(args.prompt_path)
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt audio not found: {prompt_path}")

    asyncio.run(async_main(str(prompt_path.resolve()), args.text, args.base_url, args.transport))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
