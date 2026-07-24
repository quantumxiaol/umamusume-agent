"""Project-local MCP server that owns TTS translation and Fish Speech jobs."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from openai import AsyncOpenAI

from ..config import config
from .agent import JapaneseDialoguePreparer
from .fish_client import FishSpeechHttpClient
from .jobs import TTSJobManager, TTSJobNotFound
from .models import TTSSubmitRequest


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


translation_client = AsyncOpenAI(
    api_key=config.TTS_TRANSLATION_LLM_API_KEY,
    base_url=config.TTS_TRANSLATION_LLM_BASE_URL,
    timeout=max(5.0, config.TTS_TRANSLATION_TIMEOUT_SECONDS),
    max_retries=max(0, config.TTS_TRANSLATION_MAX_RETRIES),
)
preparer = JapaneseDialoguePreparer(
    client=translation_client,
    model=config.TTS_TRANSLATION_LLM_MODEL_NAME,
    base_url=config.TTS_TRANSLATION_LLM_BASE_URL,
    temperature=config.TTS_TRANSLATION_TEMPERATURE,
    max_tokens=config.TTS_TRANSLATION_MAX_TOKENS,
    prefix_cache_enabled=config.TTS_TRANSLATION_PREFIX_CACHE_ENABLED,
    repair_attempts=1,
    thread_ttl_seconds=config.TTS_TRANSLATION_THREAD_TTL_SECONDS,
    max_threads=config.TTS_TRANSLATION_MAX_THREADS,
)
fish_client = FishSpeechHttpClient(
    base_url=config.FISHSPEECH_BASE_URL,
    timeout_seconds=config.FISHSPEECH_TIMEOUT_SECONDS,
    api_key=config.FISHSPEECH_API_KEY,
)
job_manager = TTSJobManager(
    preparer=preparer,
    fish_client=fish_client,
    outputs_dir=Path(config.OUTPUTS_DIRECTORY),
    max_concurrent_jobs=config.TTS_MAX_CONCURRENT_JOBS,
    audio_format=config.FISHSPEECH_AUDIO_FORMAT,
    speaker_prefix=config.FISHSPEECH_SPEAKER_PREFIX,
    fish_generation_options={
        "max_new_tokens": config.FISHSPEECH_MAX_NEW_TOKENS,
        "chunk_length": config.FISHSPEECH_CHUNK_LENGTH,
        "top_p": config.FISHSPEECH_TOP_P,
        "temperature": config.FISHSPEECH_TEMPERATURE,
        "repetition_penalty": config.FISHSPEECH_REPETITION_PENALTY,
        "use_memory_cache": config.FISHSPEECH_USE_MEMORY_CACHE,
    },
    job_ttl_seconds=config.TTS_JOB_TTL_SECONDS,
)

mcp = FastMCP(
    "umamusume-tts",
    instructions=(
        "Translate one newly generated character dialogue into Japanese and "
        "synthesize it with the external Fish Speech service."
    ),
    host=config.TTS_MCP_HOST,
    port=config.TTS_MCP_PORT,
    streamable_http_path="/mcp",
)


@mcp.tool()
async def tts_submit(request: dict[str, Any]) -> dict[str, Any]:
    """Submit one new character dialogue and return immediately."""

    validated = TTSSubmitRequest.model_validate(request)
    snapshot = await job_manager.submit(validated)
    return snapshot.public_dict()


@mcp.tool()
async def tts_get_job(job_id: str, user_uuid: str) -> dict[str, Any]:
    """Return the current job state; ownership is scoped by browser UUID."""

    try:
        snapshot = await job_manager.get(
            job_id=job_id,
            user_uuid=user_uuid,
        )
    except TTSJobNotFound as exc:
        raise ValueError("TTS job not found") from exc
    return snapshot.model_dump(mode="json")


@mcp.tool()
async def tts_cancel(job_id: str, user_uuid: str) -> dict[str, Any]:
    """Cancel a queued or running TTS job."""

    try:
        snapshot = await job_manager.cancel(
            job_id=job_id,
            user_uuid=user_uuid,
        )
    except TTSJobNotFound as exc:
        raise ValueError("TTS job not found") from exc
    return snapshot.public_dict()


@mcp.tool()
async def tts_health() -> dict[str, Any]:
    """Check the MCP process and the external Fish Speech service."""

    fish = await fish_client.health()
    return {
        "status": "ok",
        "translation_model": config.TTS_TRANSLATION_LLM_MODEL_NAME,
        "fishspeech": fish,
    }


def main() -> None:
    logger.info(
        "Starting TTS MCP server on %s:%s",
        config.TTS_MCP_HOST,
        config.TTS_MCP_PORT,
    )
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
