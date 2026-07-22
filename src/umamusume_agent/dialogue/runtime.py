"""Reliable LLM execution for a single character reply."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, MutableSet

from openai import APIStatusError

from ..config import config
from .models import CharacterReplyContext
from .protocol import (
    REGENERATE_JSON_PROMPT,
    REPAIR_JSON_PROMPT,
    SAFE_PARSE_FAILURE_REPLY,
    StructuredReply,
    is_json_reply_enabled,
    json_output_mode,
    parse_structured_reply,
    structured_reply_from_legacy_text,
)


logger = logging.getLogger(__name__)


class CharacterRuntime:
    """Call the configured provider and normalize its reply.

    The runtime intentionally does not know about FastAPI, sessions, history
    persistence, actor scheduling, or TTS.
    """

    def __init__(
        self,
        *,
        llm_client: Any,
        settings=config,
        response_format_unsupported: MutableSet[tuple[str, str]] | None = None,
    ):
        self.llm_client = llm_client
        self.settings = settings
        self.response_format_unsupported = (
            response_format_unsupported
            if response_format_unsupported is not None
            else set()
        )

    @staticmethod
    def extract_completion_text(response: Any) -> str:
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise ValueError("上游模型返回空响应（choices 为空）")

        first_choice = choices[0]
        message = getattr(first_choice, "message", None)
        if message is None:
            raise ValueError("上游模型响应缺少 message 字段")

        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if content is None:
            return ""
        return str(content)

    def _json_capability_key(self) -> tuple[str, str]:
        return (
            self.settings.ROLEPLAY_LLM_MODEL_BASE_URL or "",
            self.settings.ROLEPLAY_LLM_MODEL_NAME or "",
        )

    @staticmethod
    def _looks_like_unsupported_response_format(exc: Exception) -> bool:
        if not isinstance(exc, APIStatusError):
            return False
        if exc.status_code not in {400, 422}:
            return False

        response = getattr(exc, "response", None)
        if response is not None:
            try:
                payload = response.json()
                message = json.dumps(payload, ensure_ascii=False)
            except Exception:
                message = str(exc)
        else:
            message = str(exc)

        message = message.lower()
        response_format_terms = ("response_format", "json_object")
        unsupported_terms = (
            "unsupported",
            "unknown parameter",
            "unrecognized",
            "invalid parameter",
            "not supported",
        )
        return (
            any(term in message for term in response_format_terms)
            and any(term in message for term in unsupported_terms)
        )

    async def create_json_completion(
        self,
        messages: list[Dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        force_prompt_only: bool = False,
    ) -> str:
        mode = json_output_mode(self.settings)
        key = self._json_capability_key()
        send_response_format = (
            not force_prompt_only
            and is_json_reply_enabled(self.settings)
            and mode in {"auto", "response_format"}
            and not (
                mode == "auto"
                and key in self.response_format_unsupported
            )
        )

        kwargs: Dict[str, Any] = {
            "model": self.settings.ROLEPLAY_LLM_MODEL_NAME,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if send_response_format:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = await self.llm_client.chat.completions.create(**kwargs)
            return self.extract_completion_text(response)
        except Exception as exc:
            if (
                send_response_format
                and mode == "auto"
                and self.settings.LLM_JSON_RETRY_WITHOUT_RESPONSE_FORMAT_ON_ERROR
                and self._looks_like_unsupported_response_format(exc)
            ):
                self.response_format_unsupported.add(key)
                logger.warning(
                    "LLM response_format=json_object unsupported for base_url=%s model=%s; fallback to prompt-only JSON.",
                    key[0],
                    key[1],
                )
                return await self.create_json_completion(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    force_prompt_only=True,
                )
            raise

    async def generate_reply(
        self,
        context: CharacterReplyContext,
    ) -> StructuredReply:
        messages = list(context.messages)
        if not is_json_reply_enabled(self.settings):
            response = await self.llm_client.chat.completions.create(
                model=self.settings.ROLEPLAY_LLM_MODEL_NAME,
                messages=messages,
                temperature=0.7,
            )
            return structured_reply_from_legacy_text(
                self.extract_completion_text(response)
            )

        raw = await self.create_json_completion(
            messages,
            temperature=self.settings.LLM_JSON_TEMPERATURE,
            max_tokens=self.settings.LLM_JSON_MAX_TOKENS,
        )
        try:
            return parse_structured_reply(raw)
        except Exception as first_error:
            logger.warning(
                "Failed to parse JSON reply, retry=%s: %s",
                self.settings.LLM_JSON_MAX_RETRIES,
                first_error,
            )

        retries = max(0, self.settings.LLM_JSON_MAX_RETRIES)
        for _attempt in range(retries):
            repair_messages = [
                *messages,
                {"role": "assistant", "content": raw},
                {"role": "user", "content": REPAIR_JSON_PROMPT},
            ]
            raw = await self.create_json_completion(
                repair_messages,
                temperature=self.settings.LLM_JSON_TEMPERATURE,
                max_tokens=self.settings.LLM_JSON_MAX_TOKENS,
                force_prompt_only=True,
            )
            try:
                return parse_structured_reply(
                    raw,
                    source_format="json_v2_repaired",
                )
            except Exception as repair_error:
                logger.warning(
                    "Failed to parse repaired JSON reply: %s",
                    repair_error,
                )

        if self.settings.LLM_JSON_REGENERATE_ON_PARSE_FAILURE:
            regenerate_attempts = max(
                0,
                self.settings.LLM_JSON_MAX_REGENERATE_ATTEMPTS,
            )
            for _attempt in range(regenerate_attempts):
                regenerate_messages = [
                    *messages,
                    {"role": "user", "content": REGENERATE_JSON_PROMPT},
                ]
                raw = await self.create_json_completion(
                    regenerate_messages,
                    temperature=self.settings.LLM_JSON_TEMPERATURE,
                    max_tokens=self.settings.LLM_JSON_MAX_TOKENS,
                    force_prompt_only=True,
                )
                try:
                    return parse_structured_reply(
                        raw,
                        source_format="json_v2_regenerated",
                    )
                except Exception as regenerate_error:
                    logger.warning(
                        "Failed to parse regenerated JSON reply: %s",
                        regenerate_error,
                    )

        return StructuredReply(
            action="无",
            dialogue=SAFE_PARSE_FAILURE_REPLY,
            source_format="parse_error",
        )

