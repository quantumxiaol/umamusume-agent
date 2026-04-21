#!/usr/bin/env python3
"""
最小 LLM 连通性测试：

使用项目当前的 ROLEPLAY_LLM_* 配置，通过 OpenAI 兼容接口发送一句“你好”。
"""

from __future__ import annotations

import sys
from pathlib import Path

from openai import OpenAI
from openai import APIConnectionError, APITimeoutError, APIStatusError

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from umamusume_agent.config import config


def main() -> int:
    client = OpenAI(
        api_key=config.ROLEPLAY_LLM_MODEL_API_KEY,
        base_url=config.ROLEPLAY_LLM_MODEL_BASE_URL,
        timeout=max(5.0, config.ROLEPLAY_LLM_TIMEOUT_SECONDS),
        max_retries=max(0, config.ROLEPLAY_LLM_MAX_RETRIES),
    )

    print("Testing roleplay LLM endpoint...")
    print(f"model={config.ROLEPLAY_LLM_MODEL_NAME}")
    print(f"base_url={config.ROLEPLAY_LLM_MODEL_BASE_URL}")

    try:
        response = client.chat.completions.create(
            model=config.ROLEPLAY_LLM_MODEL_NAME,
            messages=[{"role": "user", "content": "你好"}],
            temperature=0.7,
        )
    except APIStatusError as exc:
        print(f"\nAPIStatusError: status={exc.status_code}")
        body = getattr(exc, "response", None)
        if body is not None:
            print(str(body))
        return 1
    except APITimeoutError as exc:
        print(f"\nAPITimeoutError: {exc}")
        return 1
    except APIConnectionError as exc:
        print(f"\nAPIConnectionError: {exc}")
        return 1

    content = response.choices[0].message.content or ""
    print("\nResponse:\n")
    print(content.strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
