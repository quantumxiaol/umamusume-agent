"""Serialized single-character operations and cancellable SSE progress delivery."""
import asyncio
import json
from contextlib import suppress

from ..dialogue.compaction import pending_messages
from ..dialogue.memory import checkpoint_payload, memory_messages
from ..dialogue.token_budget import capture_prompt_usage, calibrate
from .streaming import stream_legacy_reply


async def execute_dialogue_turn(*, request, session, service, compactor, usage_tracker, on_progress=None):
    async with session.lock:
        before = checkpoint_payload(session)
        with usage_tracker.operation(user_uuid=session.user_uuid, feature="dialogue_turn"):
            pending = pending_messages(session, request)
            await compactor.prepare(session, pending, text_only=request.text_only, on_progress=on_progress)
            # Capture reply usage only; summarizer has a different prompt layout.
            messages = memory_messages(session, history=[*session.history, *pending], text_only=request.text_only)
            with capture_prompt_usage() as usage:
                result = await service.execute_turn(
                    session=session, message=request.message, text_only=request.text_only,
                    speaker=request.speaker, event_type=request.event_type,
                    target_actor_ids=request.target_actor_ids, context_events=request.context_events,
                )
            calibrate(session, messages, usage)
        payload = result.to_api_dict()
        if compactor.settings.DIALOGUE_COMPACTION_ENABLED:
            payload["message"]["model_content"] = result.reply.model_content
            after = checkpoint_payload(session)
            if after != before:
                payload["context_checkpoint"] = after
        return result, payload


async def progress_events(operation):
    """Yield status/result tuples; a disconnected client cancels pending work."""
    queue = asyncio.Queue()

    async def notify(payload):
        await queue.put(("context_status", payload))

    async def run():
        try:
            return await operation(notify)
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(run())
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                yield "heartbeat", None
                continue
            if event is None:
                break
            yield event
        yield "result", await task
    finally:
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def stream_legacy_with_memory(*, compactor, **kwargs):
    session, request = kwargs["session"], kwargs["request"]
    async with session.lock:
        with kwargs["llm_usage_tracker"].operation(user_uuid=session.user_uuid, feature="dialogue_turn"):
            pending = pending_messages(session, request)
            async for kind, data in progress_events(lambda notify: compactor.prepare(
                session, pending, text_only=request.text_only, on_progress=notify,
            )):
                if kind == "heartbeat":
                    yield ": keepalive\n\n"
                elif kind == "context_status":
                    yield f"event: context_status\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            messages = memory_messages(session, history=[*session.history, *pending], text_only=request.text_only)
            with capture_prompt_usage() as usage:
                async for event in stream_legacy_reply(**kwargs, usage_scoped=True):
                    yield event
            calibrate(session, messages, usage)
