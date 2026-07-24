"""Deterministic Chinese-subtitle to Japanese-spoken-line preparation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from openai import APIStatusError, AsyncOpenAI

from ..dialogue.protocol import load_json_object_from_text
from .models import (
    PreparedJapaneseDialogue,
    TTSCharacterProfile,
    TTSContextEvent,
    TTSSubmitRequest,
)


logger = logging.getLogger(__name__)


TRANSLATION_RULES = """你是赛马娘角色对白的日语配音文本转换器。
你的职责固定且只有一个：把当前角色已经确定的中文字幕改写成符合角色人设、上下文和称呼关系的自然日语口语。

硬性要求：
1. 只转换“当前待配音对白”，不得改写、续写剧情，不得添加新的事实。
2. 不得把动作、神态、心理、环境、旁白或说话人标签写进 spoken_text_ja。
3. 必须保持原句意图、语气、否定关系和人物关系。
4. 人名、第一人称和对他人的称呼必须按以下优先级判断：
   当前中文字幕中明确出现的称呼 > 角色完整人设中的角色专属称呼 > 结构化旧字段 > 在场角色表与常识推断。
5. 当前对白明确写出亲属式或敬称式称呼时必须保留其关系和亲密程度，例如“哥哥/哥哥大人”应使用角色对应的“お兄さま”等称呼，不得泛化成“トレーナーさん”。
6. 不得替其他角色增加台词，不得加入解释。
7. subtitle_ja 和 spoken_text_ja 都必须是干净的日语对白。
8. 只输出一个 JSON object，格式固定为：
{"subtitle_ja":"日语字幕","spoken_text_ja":"实际送入配音的干净日语对白"}
"""


def _completion_text(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", "") if message is not None else ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = getattr(item, "text", None)
            if isinstance(item, dict):
                text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts).strip()
    return ""


def _provider_supports_cache(base_url: str, model: str) -> bool:
    normalized_url = base_url.lower()
    normalized_model = model.lower()
    return (
        "dashscope.aliyuncs.com" in normalized_url
        or "bailian" in normalized_url
        or normalized_model.startswith("qwen")
    )


def _profile_system_prompt(
    profile: TTSCharacterProfile,
    cast_payload: str,
) -> str:
    catchphrases = "；".join(profile.catchphrases) or "无"
    return (
        f"{TRANSLATION_RULES}\n"
        "【当前配音角色固定资料】\n"
        f"中文名：{profile.name_zh}\n"
        f"日文名：{profile.name_jp}\n"
        f"结构化第一人称旧字段（仅参考）：{profile.first_person or '未提供'}\n"
        f"结构化训练员称呼旧字段（仅参考）：{profile.user_address or '未提供'}\n"
        f"说话风格：{profile.speaking_style or '遵循角色完整人设'}\n"
        f"口癖参考：{catchphrases}\n"
        f"日语参考音频文本：{profile.reference_text_ja or '未提供'}\n"
        "若上面的结构化旧字段与当前对白或下方完整人设冲突，"
        "必须忽略旧字段，以当前对白和完整人设为准。\n"
        f"角色完整人设：\n{profile.system_prompt}\n\n"
        f"【本场固定角色表】\n{cast_payload}"
    )


def _render_context_event(event: TTSContextEvent) -> str:
    actor = event.display_name or event.actor_id or "环境"
    if event.event_type == "character_reply":
        parts = [f"【公开事件｜{actor}】"]
        if event.action:
            parts.append(f"动作：{event.action}")
        if event.dialogue:
            parts.append(f"对白：{event.dialogue}")
        return "\n".join(parts)
    if event.event_type == "action":
        return f"【公开事件｜{actor}动作】{event.content}"
    if event.event_type in {"scene_event", "scene_change", "narration"}:
        return f"【公开环境事件】{event.content}"
    return f"【公开事件｜{actor}对白】{event.dialogue or event.content}"


@dataclass
class _TranslationThread:
    profile_hash: str
    messages: list[dict[str, Any]]
    context_fingerprints: list[str] = field(default_factory=list)
    seen_event_ids: set[str] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_used_at: float = field(default_factory=time.monotonic)


class JapaneseDialoguePreparer:
    """A fixed LLM workflow with append-only per-character prompt threads."""

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        model: str,
        base_url: str,
        temperature: float,
        max_tokens: int,
        prefix_cache_enabled: bool,
        repair_attempts: int = 1,
        thread_ttl_seconds: int = 7200,
        max_threads: int = 256,
    ):
        self.client = client
        self.model = model
        self.base_url = base_url
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.prefix_cache_enabled = prefix_cache_enabled
        self.repair_attempts = max(0, repair_attempts)
        self.thread_ttl_seconds = max(0, thread_ttl_seconds)
        self.max_threads = max(1, max_threads)
        self._threads: dict[str, _TranslationThread] = {}
        self._response_format_supported = True

    def _thread_for(self, request: TTSSubmitRequest) -> _TranslationThread:
        cast_payload = json.dumps(
            [item.model_dump() for item in request.cast],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        system_prompt = _profile_system_prompt(request.speaker, cast_payload)
        profile_hash = hashlib.sha256(
            system_prompt.encode("utf-8")
        ).hexdigest()
        thread_key = (
            f"{request.user_uuid}:{request.source_session_id}:"
            f"{request.speaker.actor_id}"
        )
        self._prune_threads(thread_key)
        existing = self._threads.get(thread_key)
        if existing is not None and existing.profile_hash == profile_hash:
            existing.last_used_at = time.monotonic()
            return existing

        system_message: dict[str, Any] = {
            "role": "system",
            "content": system_prompt,
        }
        if (
            self.prefix_cache_enabled
            and _provider_supports_cache(self.base_url, self.model)
        ):
            system_message["cache_control"] = {"type": "ephemeral"}
        thread = _TranslationThread(
            profile_hash=profile_hash,
            messages=[system_message],
        )
        self._threads[thread_key] = thread
        return thread

    def _prune_threads(self, current_key: str) -> None:
        now = time.monotonic()
        if self.thread_ttl_seconds > 0:
            for key, thread in list(self._threads.items()):
                if (
                    key != current_key
                    and not thread.lock.locked()
                    and now - thread.last_used_at > self.thread_ttl_seconds
                ):
                    self._threads.pop(key, None)

        overflow = len(self._threads) - self.max_threads + 1
        if overflow <= 0 or current_key in self._threads:
            return
        candidates = sorted(
            (
                (thread.last_used_at, key)
                for key, thread in self._threads.items()
                if key != current_key and not thread.lock.locked()
            )
        )
        for _last_used_at, key in candidates[:overflow]:
            self._threads.pop(key, None)

    async def prepare(
        self,
        request: TTSSubmitRequest,
    ) -> PreparedJapaneseDialogue:
        thread = self._thread_for(request)
        async with thread.lock:
            thread.last_used_at = time.monotonic()
            incoming_fingerprints = [
                self._event_fingerprint(event)
                for event in request.context_events
            ]
            previous_fingerprints = thread.context_fingerprints
            context_is_append_only = (
                len(incoming_fingerprints) >= len(previous_fingerprints)
                and incoming_fingerprints[: len(previous_fingerprints)]
                == previous_fingerprints
            )
            if not context_is_append_only:
                # Editing, regenerating, importing, or trimming dialogue history
                # invalidates the dynamic translation prefix. Keep only the
                # stable persona/cast system message and rebuild public context.
                thread.messages = [thread.messages[0]]
                thread.context_fingerprints = []
                thread.seen_event_ids.clear()

            for event, fingerprint in zip(
                request.context_events[len(thread.context_fingerprints):],
                incoming_fingerprints[len(thread.context_fingerprints):],
            ):
                thread.messages.append(
                    {"role": "user", "content": _render_context_event(event)}
                )
                thread.seen_event_ids.add(event.event_id)
                thread.context_fingerprints.append(fingerprint)

            request_message = {
                "role": "user",
                "content": (
                    "【当前待配音对白】\n"
                    f"说话角色：{request.speaker.name_zh}\n"
                    f"回应对象 actor_id："
                    f"{json.dumps(request.target_actor_ids, ensure_ascii=False)}\n"
                    f"中文字幕：{request.subtitle_zh}\n"
                    "严格按固定 JSON 格式转换，不要添加动作或旁白。"
                ),
            }
            thread.messages.append(request_message)
            raw = await self._complete(thread.messages)
            thread.messages.append({"role": "assistant", "content": raw})

            try:
                prepared = self._validate(raw)
            except ValueError as first_error:
                prepared = await self._repair(
                    thread,
                    raw,
                    first_error,
                )
            thread.last_used_at = time.monotonic()
            return prepared

    @staticmethod
    def _event_fingerprint(event: TTSContextEvent) -> str:
        payload = event.model_dump(mode="json")
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    async def _complete(self, messages: list[dict[str, Any]]) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self._response_format_supported:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = await self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if (
                "response_format" not in kwargs
                or not self._looks_like_unsupported_response_format(exc)
            ):
                raise
            self._response_format_supported = False
            logger.warning(
                "TTS translation provider does not support "
                "response_format=json_object; retrying prompt-only",
            )
            kwargs.pop("response_format", None)
            response = await self.client.chat.completions.create(**kwargs)
        raw = _completion_text(response)
        if not raw:
            raise ValueError("TTS translation model returned empty content")
        return raw

    @staticmethod
    def _looks_like_unsupported_response_format(exc: Exception) -> bool:
        if not isinstance(exc, APIStatusError):
            return False
        if exc.status_code not in {400, 422}:
            return False
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                message = json.dumps(
                    response.json(),
                    ensure_ascii=False,
                )
            except Exception:
                message = str(exc)
        else:
            message = str(exc)
        normalized = message.lower()
        return (
            any(
                term in normalized
                for term in ("response_format", "json_object")
            )
            and any(
                term in normalized
                for term in (
                    "unsupported",
                    "unknown parameter",
                    "unrecognized",
                    "invalid parameter",
                    "not supported",
                )
            )
        )

    async def _repair(
        self,
        thread: _TranslationThread,
        raw: str,
        error: ValueError,
    ) -> PreparedJapaneseDialogue:
        current_error = error
        current_raw = raw
        for _ in range(self.repair_attempts):
            thread.messages.append(
                {
                    "role": "user",
                    "content": (
                        "上一条输出不符合固定格式。"
                        f"错误：{current_error}。"
                        "只修复格式与缺失字段，保持原意，重新输出 JSON object。"
                    ),
                }
            )
            current_raw = await self._complete(thread.messages)
            thread.messages.append(
                {"role": "assistant", "content": current_raw}
            )
            try:
                return self._validate(current_raw)
            except ValueError as next_error:
                current_error = next_error
        raise current_error

    @staticmethod
    def _validate(raw: str) -> PreparedJapaneseDialogue:
        try:
            payload = load_json_object_from_text(raw)
        except Exception as exc:
            raise ValueError("translation output is not a JSON object") from exc
        subtitle_ja = str(payload.get("subtitle_ja") or "").strip()
        spoken_text_ja = str(payload.get("spoken_text_ja") or "").strip()
        if not subtitle_ja or not spoken_text_ja:
            raise ValueError(
                "translation output requires subtitle_ja and spoken_text_ja"
            )
        if len(spoken_text_ja) > 1000:
            raise ValueError("spoken_text_ja is unreasonably long")
        forbidden_spoken_markers = (
            "<|speaker:",
            "【",
            "】",
            "动作：",
            "動作：",
            "旁白：",
            "环境：",
            "環境：",
            "说话角色：",
            "話者：",
        )
        if any(
            marker in spoken_text_ja
            for marker in forbidden_spoken_markers
        ):
            raise ValueError(
                "spoken_text_ja contains a speaker, action, or narration marker"
            )
        return PreparedJapaneseDialogue(
            subtitle_ja=subtitle_ja,
            spoken_text_ja=spoken_text_ja,
            fish_text=spoken_text_ja,
        )
