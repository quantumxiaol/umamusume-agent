"""Legacy two-line token streaming, isolated from the structured reply route."""
import json
from contextlib import nullcontext
from time import monotonic
from typing import Any, AsyncGenerator, Dict, Optional
from uuid import uuid4

from ..dialogue.models import EVENT_SCHEMA_VERSION, DialogueInputEvent, actor_from_character, default_player_actor
from ..dialogue.protocol import normalize_structured_reply, structured_reply_from_legacy_text
from ..dialogue.runtime import CharacterRuntime
from ..dialogue.session import DialogueSession
from ..llm_usage import DeepSeekUsageTracker
from ..tts import VoiceService
from .schemas import DialogueRequest
from .tts_routes import should_generate_voice, submit_single_voice


def _story_event_metadata(
    request: DialogueRequest,
    session: DialogueSession,
) -> Dict[str, Any]:
    if not request.context_events and all(
        value is None
        for value in (
            request.speaker,
            request.event_type,
            request.target_actor_ids,
        )
    ):
        return {}

    speaker = request.speaker or default_player_actor()
    return {
        "actor": speaker.model_dump(),
        "event_type": request.event_type or "dialogue",
        "target_actor_ids": list(
            request.target_actor_ids
            if request.target_actor_ids is not None
            else [session.character.id]
        ),
        "event_schema_version": EVENT_SCHEMA_VERSION,
    }


def _append_context_events(
    session: DialogueSession,
    events: Optional[list[DialogueInputEvent]],
) -> None:
    for event in events or []:
        speaker = event.speaker or default_player_actor()
        session.add_message(
            "user",
            event.content,
            actor=speaker.model_dump(),
            event_type=event.event_type or "dialogue",
            target_actor_ids=list(
                event.target_actor_ids
                if event.target_actor_ids is not None
                else [session.character.id]
            ),
            event_schema_version=EVENT_SCHEMA_VERSION,
        )


def _character_reply_event_metadata(
    request: DialogueRequest,
    session: DialogueSession,
) -> Dict[str, Any]:
    input_metadata = _story_event_metadata(request, session)
    if not input_metadata:
        return {}
    speaker = request.speaker or default_player_actor()
    event_type = request.event_type or "dialogue"
    return {
        "actor": actor_from_character(session.character).model_dump(),
        "event_type": "dialogue",
        "target_actor_ids": (
            []
            if event_type in {"scene_event", "narration"}
            else [speaker.actor_id]
        ),
        "event_schema_version": EVENT_SCHEMA_VERSION,
    }


def _extract_stream_delta_text(chunk: Any) -> str:
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""

    first_choice = choices[0]
    delta = getattr(first_choice, "delta", None)
    if delta is None:
        return ""

    content = getattr(delta, "content", None)
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return str(content)


async def stream_legacy_reply(
    *, request: DialogueRequest, session: DialogueSession,
    runtime: CharacterRuntime, settings, voice_service: VoiceService,
    enable_tts: bool, llm_usage_tracker: DeepSeekUsageTracker,
    usage_scoped: bool = False,
) -> AsyncGenerator[str, None]:
    # 旧两行协议仍保持 token 流式行为。
    _append_context_events(session, request.context_events)
    session.add_message(
        "user",
        request.message,
        **_story_event_metadata(request, session),
    )

    # 流式调用 LLM。DeepSeek 的最后一个 chunk 携带整次请求的
    # usage；在同一个 operation scope 中记录，避免轮询余额接口。
    stream_kwargs: Dict[str, Any] = {
        "model": settings.ROLEPLAY_LLM_MODEL_NAME,
        "messages": session.get_messages(text_only=request.text_only),
        "temperature": 0.7,
        "stream": True,
    }
    if llm_usage_tracker.enabled:
        stream_kwargs["stream_options"] = {"include_usage": True}

    full_reply_raw = ""
    scope = nullcontext() if usage_scoped else llm_usage_tracker.operation(
        user_uuid=session.user_uuid,
        feature="dialogue_turn",
    )
    with scope:
        request_started = monotonic()
        stream = await runtime.llm_client.chat.completions.create(
            **stream_kwargs
        )
        async for chunk in stream:
            if getattr(chunk, "usage", None) is not None:
                runtime.log_usage(
                    chunk,
                    finish_reason=(
                        runtime.extract_finish_reason(chunk)
                    ),
                    latency_ms=round(
                        (monotonic() - request_started) * 1000
                    ),
                )
            content = _extract_stream_delta_text(chunk)
            if not content:
                continue
            full_reply_raw += content

            # 发送 SSE 事件
            yield f"data: {content}\n\n"

    full_reply = normalize_structured_reply(full_reply_raw)
    structured_reply = structured_reply_from_legacy_text(full_reply, source_format="legacy_text")
    utterance_id = uuid4().hex

    # 添加完整回复到历史
    session.add_message(
        "assistant",
        structured_reply.dialogue,
        action=structured_reply.action,
        dialogue=structured_reply.dialogue,
        source_format=structured_reply.source_format,
        schema_version=structured_reply.schema_version,
        utterance_id=utterance_id,
        **_character_reply_event_metadata(request, session),
    )

    # 发送完成事件
    yield f"event: done\ndata: {{}}\n\n"

    if should_generate_voice(request, session, structured_reply, enabled=enable_tts):
        voice_info = await submit_single_voice(
            voice_service, session,
            utterance_id=utterance_id,
            dialogue=structured_reply.dialogue,
        )
        if voice_info:
            payload = json.dumps(voice_info, ensure_ascii=False)
            yield f"event: voice_pending\ndata: {payload}\n\n"
