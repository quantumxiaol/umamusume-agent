"""Reliable LLM execution for a single character reply."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
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


@dataclass(frozen=True)
class JsonCompletionResult:
    """One complete provider response after bounded length retries."""

    content: str
    finish_reason: str
    max_tokens: int
    length_retries: int = 0

    @property
    def can_parse(self) -> bool:
        return self.finish_reason == "stop"


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

    @staticmethod
    def extract_finish_reason(response: Any) -> str:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return "unknown"
        value = getattr(choices[0], "finish_reason", None)
        # Older OpenAI-compatible providers and test doubles may omit this
        # field. Preserve their previous behavior by treating it as complete.
        return str(value).strip().lower() if value else "stop"

    @staticmethod
    def log_usage(response: Any, *, finish_reason: str | None = None) -> None:
        usage = getattr(response, "usage", None)

        def read(source: Any, key: str) -> Any:
            if isinstance(source, dict):
                return source.get(key)
            return getattr(source, key, None)

        prompt_tokens = read(usage, "prompt_tokens")
        completion_tokens = read(usage, "completion_tokens")
        details = (
            read(usage, "prompt_tokens_details")
            or read(usage, "input_tokens_details")
        )
        cached_tokens = read(details, "cached_tokens") if details else None
        if cached_tokens is None:
            cached_tokens = read(usage, "prompt_cache_hit_tokens")
        completion_details = read(usage, "completion_tokens_details")
        reasoning_tokens = (
            read(completion_details, "reasoning_tokens")
            if completion_details
            else None
        )
        logger.info(
            "LLM usage request_id=%s model=%s finish_reason=%s "
            "prompt_tokens=%s completion_tokens=%s reasoning_tokens=%s "
            "cached_tokens=%s",
            getattr(response, "id", None) or "unknown",
            getattr(response, "model", None) or "unknown",
            finish_reason or CharacterRuntime.extract_finish_reason(response),
            prompt_tokens,
            completion_tokens,
            reasoning_tokens,
            cached_tokens,
        )

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

    async def create_json_completion_result(
        self,
        messages: list[Dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        force_prompt_only: bool = False,
        thinking: bool | None = None,
    ) -> JsonCompletionResult:
        mode = json_output_mode(self.settings)
        key = self._json_capability_key()
        current_max_tokens = max(1, int(max_tokens))
        length_retry_limit = max(
            0,
            int(getattr(self.settings, "LLM_JSON_LENGTH_RETRY_ATTEMPTS", 2)),
        )
        dynamic_token_limit = max(
            current_max_tokens,
            int(getattr(self.settings, "LLM_JSON_MAX_DYNAMIC_TOKENS", 8192)),
        )
        length_retries = 0
        prompt_only = force_prompt_only

        while True:
            send_response_format = (
                not prompt_only
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
                "max_tokens": current_max_tokens,
            }
            if send_response_format:
                kwargs["response_format"] = {"type": "json_object"}
            if thinking is not None:
                kwargs["extra_body"] = {
                    "thinking": {
                        "type": "enabled" if thinking else "disabled",
                    }
                }

            try:
                response = await self.llm_client.chat.completions.create(**kwargs)
            except Exception as exc:
                if (
                    send_response_format
                    and mode == "auto"
                    and self.settings.LLM_JSON_RETRY_WITHOUT_RESPONSE_FORMAT_ON_ERROR
                    and self._looks_like_unsupported_response_format(exc)
                ):
                    self.response_format_unsupported.add(key)
                    prompt_only = True
                    logger.warning(
                        "LLM response_format=json_object unsupported for "
                        "base_url=%s model=%s; fallback to prompt-only JSON.",
                        key[0],
                        key[1],
                    )
                    continue
                raise

            finish_reason = self.extract_finish_reason(response)
            self.log_usage(response, finish_reason=finish_reason)
            result = JsonCompletionResult(
                content=self.extract_completion_text(response),
                finish_reason=finish_reason,
                max_tokens=current_max_tokens,
                length_retries=length_retries,
            )
            if finish_reason != "length":
                return result

            if (
                length_retries >= length_retry_limit
                or current_max_tokens >= dynamic_token_limit
            ):
                logger.warning(
                    "LLM completion remained truncated after %s length "
                    "retries (max_tokens=%s); discard partial output",
                    length_retries,
                    current_max_tokens,
                )
                return result

            next_max_tokens = min(
                dynamic_token_limit,
                current_max_tokens * 2,
            )
            length_retries += 1
            logger.warning(
                "LLM finish_reason=length; discard partial output and retry "
                "original messages with max_tokens=%s (previous=%s, retry=%s/%s)",
                next_max_tokens,
                current_max_tokens,
                length_retries,
                length_retry_limit,
            )
            current_max_tokens = next_max_tokens

    async def create_json_completion(
        self,
        messages: list[Dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        force_prompt_only: bool = False,
    ) -> str:
        """Compatibility wrapper returning only the final content string."""
        result = await self.create_json_completion_result(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            force_prompt_only=force_prompt_only,
        )
        return result.content

    @staticmethod
    def _safe_parse_failure_reply() -> StructuredReply:
        return StructuredReply(
            action="无",
            dialogue=SAFE_PARSE_FAILURE_REPLY,
            source_format="parse_error",
        )

    @staticmethod
    def _can_parse_completion(
        result: JsonCompletionResult,
        *,
        stage: str,
    ) -> bool:
        if result.can_parse:
            return True
        logger.warning(
            "Skip JSON %s because finish_reason=%s after %s length retries; "
            "partial output will not enter repair context",
            stage,
            result.finish_reason,
            result.length_retries,
        )
        return False

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
            self.log_usage(response)
            return structured_reply_from_legacy_text(
                self.extract_completion_text(response)
            )

        completion = await self.create_json_completion_result(
            messages,
            temperature=self.settings.LLM_JSON_TEMPERATURE,
            max_tokens=self.settings.LLM_JSON_MAX_TOKENS,
        )
        if not self._can_parse_completion(completion, stage="reply"):
            return self._safe_parse_failure_reply()
        raw = completion.content
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
            completion = await self.create_json_completion_result(
                repair_messages,
                temperature=self.settings.LLM_JSON_TEMPERATURE,
                max_tokens=self.settings.LLM_JSON_MAX_TOKENS,
                force_prompt_only=True,
            )
            if not self._can_parse_completion(completion, stage="repair"):
                return self._safe_parse_failure_reply()
            raw = completion.content
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
                completion = await self.create_json_completion_result(
                    regenerate_messages,
                    temperature=self.settings.LLM_JSON_TEMPERATURE,
                    max_tokens=self.settings.LLM_JSON_MAX_TOKENS,
                    force_prompt_only=True,
                )
                if not self._can_parse_completion(
                    completion,
                    stage="regeneration",
                ):
                    return self._safe_parse_failure_reply()
                raw = completion.content
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

        return self._safe_parse_failure_reply()
