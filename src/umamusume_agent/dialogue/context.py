"""Model context builders for dialogue modes."""

from __future__ import annotations

from typing import Any, Sequence

from ..character import CharacterConfig
from ..config import config
from .models import CharacterReplyContext
from .protocol import (
    HIDDEN_JSON_FORMAT_REINJECTION_PROMPT,
    HIDDEN_LEGACY_FORMAT_REINJECTION_PROMPT,
    JSON_RESPONSE_FORMAT_INSTRUCTION,
    LEGACY_RESPONSE_FORMAT_INSTRUCTION,
    PLAIN_TEXT_RESPONSE_FORMAT_INSTRUCTION,
    is_json_reply_enabled,
)


class LegacyDialogueContextBuilder:
    """Reproduce the original single-character model context exactly."""

    def __init__(
        self,
        *,
        settings=config,
        prefix_cache_enabled: bool | None = None,
        prefix_cache_min_chars: int | None = None,
        hidden_reinjection_enabled: bool | None = None,
        hidden_reinjection_interval_messages: int | None = None,
    ):
        self.settings = settings
        self.prefix_cache_enabled = (
            settings.DIALOGUE_PREFIX_CACHE_ENABLED
            if prefix_cache_enabled is None
            else prefix_cache_enabled
        )
        self.prefix_cache_min_chars = max(
            0,
            settings.DIALOGUE_PREFIX_CACHE_MIN_CHARS
            if prefix_cache_min_chars is None
            else prefix_cache_min_chars,
        )
        self.hidden_reinjection_enabled = (
            settings.DIALOGUE_HIDDEN_FORMAT_REINJECTION_ENABLED
            if hidden_reinjection_enabled is None
            else hidden_reinjection_enabled
        )
        self.hidden_reinjection_interval_messages = max(
            0,
            settings.DIALOGUE_HIDDEN_FORMAT_REINJECTION_INTERVAL_MESSAGES
            if hidden_reinjection_interval_messages is None
            else hidden_reinjection_interval_messages,
        )
        self._roleplay_base_url = (
            settings.ROLEPLAY_LLM_MODEL_BASE_URL or ""
        ).lower()
        self._roleplay_model_name = (
            settings.ROLEPLAY_LLM_MODEL_NAME or ""
        ).lower()

    def supports_prefix_cache_provider(self) -> bool:
        if "dashscope.aliyuncs.com" in self._roleplay_base_url:
            return True
        if "bailian" in self._roleplay_base_url:
            return True
        return self._roleplay_model_name.startswith("qwen")

    def should_attach_prefix_cache(self, system_prompt: str) -> bool:
        if not self.prefix_cache_enabled:
            return False
        if not self.supports_prefix_cache_provider():
            return False
        return len(system_prompt) >= self.prefix_cache_min_chars

    def _append_history(
        self,
        messages: list[dict[str, Any]],
        history: Sequence[dict[str, Any]],
    ) -> None:
        if (
            not self.hidden_reinjection_enabled
            or self.hidden_reinjection_interval_messages <= 0
        ):
            messages.extend(history)
            return

        for index, message in enumerate(history, start=1):
            messages.append(message)
            if index % self.hidden_reinjection_interval_messages == 0:
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            HIDDEN_JSON_FORMAT_REINJECTION_PROMPT
                            if is_json_reply_enabled(self.settings)
                            else HIDDEN_LEGACY_FORMAT_REINJECTION_PROMPT
                        ),
                    }
                )

    def build(
        self,
        *,
        character: CharacterConfig,
        history: Sequence[dict[str, Any]],
        text_only: bool = False,
    ) -> CharacterReplyContext:
        response_instruction = (
            JSON_RESPONSE_FORMAT_INSTRUCTION
            if is_json_reply_enabled(self.settings)
            else LEGACY_RESPONSE_FORMAT_INSTRUCTION
        )
        if text_only:
            response_instruction = (
                f"{response_instruction}\n{PLAIN_TEXT_RESPONSE_FORMAT_INSTRUCTION}"
            )

        system_prompt = (
            f"{character.get_system_prompt()}\n\n{response_instruction}"
        )
        system_message: dict[str, Any] = {
            "role": "system",
            "content": system_prompt,
        }
        if self.should_attach_prefix_cache(system_prompt):
            system_message["cache_control"] = {"type": "ephemeral"}

        messages = [system_message]
        self._append_history(messages, history)
        return CharacterReplyContext(messages=messages)

