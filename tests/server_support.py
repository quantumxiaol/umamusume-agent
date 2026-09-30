"""Isolated settings for offline runtime and application regression tests."""
from pathlib import Path
from types import SimpleNamespace

from umamusume_agent.config import config


def test_settings(root: Path | None = None, **overrides):
    values = {name: getattr(config, name) for name in dir(config) if name.isupper()}
    values.update(
        ROLEPLAY_LLM_MODEL_API_KEY="offline-test",
        ROLEPLAY_LLM_MODEL_BASE_URL="https://llm.example.test/v1",
        ROLEPLAY_LLM_MODEL_NAME="test-model",
        API_ACCESS_KEY="",
        API_RATE_LIMIT_ENABLED=False,
        ENABLE_TTS=False,
        LLM_JSON_ENABLED=True,
        LLM_JSON_OUTPUT_MODE="auto",
        LLM_REQUEST_DIAGNOSTICS_ENABLED=True,
    )
    if root is not None:
        root = root.resolve()
        values.update(
            OUTPUTS_DIRECTORY=str(root / "outputs"),
            DIALOGUE_HISTORY_DIRECTORY=str(root / "dialogue"),
            DIRECTOR_HISTORY_DIRECTORY=str(root / "director"),
            CHARACTERS_DIRECTORY=str(root / "characters"),
        )
    values.update(overrides)
    return SimpleNamespace(**values)
