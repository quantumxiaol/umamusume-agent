"""One transactional roleplay turn over a private, bidirectional JSONL pipe.

Harness owns durable history. Only a successful result may be committed by the
host; an error, cancellation, or killed worker leaves its previous state intact.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4, uuid5, NAMESPACE_URL

# Set before importing the core: a linked developer checkout must not load .env.
os.environ["UMAMUSUME_SKIP_ENV_FILES"] = "1"

from ..character import CharacterManager
from ..config import Config
from ..dialogue.compaction import HistoryCompactor, pending_messages
from ..dialogue.context import LegacyDialogueContextBuilder
from ..dialogue.memory import HistoryCheckpoint, checkpoint_matches, memory_messages
from ..dialogue.models import ActorRef, DialogueInputEvent, default_player_actor
from ..dialogue.runtime import CharacterRuntime
from ..dialogue.service import DialogueService
from ..dialogue.session import DialogueSession
from ..dialogue.token_budget import capture_prompt_usage, calibrate
from ..input_limits import validate_input_batch, validate_history_size


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def namespace(value):
    if isinstance(value, dict):
        return SimpleNamespace(**{key: namespace(item) for key, item in value.items()})
    if isinstance(value, list):
        return [namespace(item) for item in value]
    return value


class BridgeClient:
    def __init__(self, reader=sys.stdin, writer=emit):
        self.reader, self.writer = reader, writer
        self.chat = SimpleNamespace(completions=self)

    def with_options(self, **_kwargs):
        return self

    async def create(self, **kwargs):
        request_id = uuid4().hex
        self.writer({"type": "model", "id": request_id, "call": kwargs})
        stream = BridgeStream(self.reader, request_id)
        if kwargs.get("stream"):
            return stream
        async for frame in stream.frames():
            if frame["type"] == "completion":
                return namespace(frame["response"])
        raise RuntimeError("Harness model bridge closed before completion")


class BridgeStream:
    def __init__(self, reader, request_id):
        self.reader, self.request_id = reader, request_id

    async def frames(self):
        while True:
            line = await asyncio.to_thread(self.reader.readline)
            if not line:
                raise RuntimeError("Harness disconnected")
            frame = json.loads(line)
            if frame.get("id") != self.request_id:
                raise RuntimeError("Mismatched model response")
            if frame["type"] == "model_error":
                raise RuntimeError(frame["message"])
            yield frame
            if frame["type"] == "completion":
                return

    async def __aiter__(self):
        async for frame in self.frames():
            if frame["type"] == "chunk":
                yield namespace(frame["chunk"])

    async def close(self):
        pass  # The host owns the child and cancels its provider request together.


class HarnessSession(DialogueSession):
    def _append_history_event(self, payload, *, strict=False):
        # Python proposes state; the Harness session log commits it atomically.
        pass


def settings_for(options):
    settings = SimpleNamespace(**{key: getattr(Config, key) for key in dir(Config) if key.isupper()})
    settings.ROLEPLAY_LLM_MODEL_NAME = options["model"]
    settings.ROLEPLAY_LLM_MODEL_BASE_URL = "harness://local"
    settings.ROLEPLAY_LLM_MODEL_API_KEY = ""
    settings.LLM_JSON_ENABLED = True
    # Harness has a provider-neutral text API; JSON validation/repair is ours.
    settings.LLM_JSON_OUTPUT_MODE = "prompt_only"
    settings.LLM_REQUEST_DIAGNOSTICS_ENABLED = False
    settings.ENABLE_TTS = False
    capacity = int(options["contextWindow"])
    if capacity < 8192:
        raise ValueError("角色对话需要至少 8192 tokens 上下文，请选择更大容量的模型。")
    output = min(int(options["maxOutputTokens"]), capacity // 8)
    settings.LLM_JSON_MAX_TOKENS = output
    settings.LLM_JSON_MAX_DYNAMIC_TOKENS = min(output * 2, capacity // 4)
    settings.DIALOGUE_CONTEXT_MAX_TOKENS = capacity
    settings.DIALOGUE_CONTEXT_RESERVE_TOKENS = capacity // 4
    settings.DIALOGUE_COMPACTION_TRIGGER_TOKENS = int(capacity * .65)
    settings.DIALOGUE_COMPACTION_TARGET_TOKENS = int(capacity * .4)
    settings.DIALOGUE_COMPACTION_MEMORY_TOKENS = int(capacity * .12)
    settings.DIALOGUE_COMPACTION_RECENT_TOKENS = int(capacity * .2)
    settings.DIALOGUE_COMPACTION_CHUNK_TOKENS = int(capacity * .2)
    settings.DIALOGUE_COMPACTION_MAX_TOKENS = min(16384, capacity // 8)
    settings.DIALOGUE_COMPACTION_MAX_DYNAMIC_TOKENS = min(32768, capacity // 4)
    return settings


async def execute(input, client=None, progress=None):
    state = input["state"]
    if state.get("version") != 1:
        raise ValueError("Unsupported roleplay snapshot version")
    manager = CharacterManager(input["charactersDirectory"])
    character = await manager.load_character(state["character"])
    settings = settings_for(input["options"])
    builder = LegacyDialogueContextBuilder(settings=settings)
    history = state["history"]
    validate_history_size(history, state.get("checkpoint"), state.get("checkpoints"))
    regenerate = bool(state.get("retry"))
    turn_input = state["lastInput"] if regenerate else input["input"]
    if not turn_input:
        raise ValueError("没有可以重新生成的轮次")
    replace_from = len(history)
    if regenerate:
        replace_from = turn_input["historyStart"]
        history = history[:replace_from]
    request = SimpleNamespace(message=turn_input["message"], event_type=turn_input["mode"],
        context_events=[DialogueInputEvent.model_validate(event) for event in turn_input.get("queue", [])],
        speaker=ActorRef(actor_id="environment", actor_type="narrator", display_name="环境")
        if turn_input["mode"] == "scene_event" else default_player_actor(), target_actor_ids=[character.id], text_only=False)
    validate_input_batch([request.message, *[event.content for event in request.context_events]])
    scratch = Path(input["scratchDirectory"])
    session = HarnessSession(input["sessionId"], character, str(uuid5(NAMESPACE_URL, input["sessionId"])),
        output_dir=scratch, history_file=scratch / "history.jsonl", context_builder=builder, initial_history=history)
    session.token_ratio = float(state.get("tokenRatio", .5))
    session.checkpoints = [HistoryCheckpoint.model_validate(value) for value in state.get("checkpoints", [])
        if value["covered_messages"] <= len(history)]
    if state.get("checkpoint"):
        checkpoint = HistoryCheckpoint.model_validate(state["checkpoint"])
        if checkpoint_matches(session, checkpoint):
            session.checkpoint = checkpoint
    runtime = CharacterRuntime(llm_client=client or BridgeClient(), settings=settings)
    compactor = HistoryCompactor(runtime=runtime, settings=settings)
    await compactor.prepare(session, pending_messages(session, request), on_progress=progress)
    messages = memory_messages(session, history=[*session.history, *pending_messages(session, request)])
    with capture_prompt_usage() as usage:
        result = await DialogueService(runtime=runtime, context_builder=builder).execute_turn(
            session=session, message=request.message, speaker=request.speaker,
            event_type=request.event_type, context_events=request.context_events, target_actor_ids=request.target_actor_ids)
    calibrate(session, messages, usage)
    if result.reply.source_format == "parse_error":
        raise ValueError("模型未能生成有效动作/对白 JSON；原历史未改变，可更换模型后重试。")
    return {"version": 1, "baseRevision": state["revision"], "replaceFrom": replace_from,
        "historyAppend": session.history[replace_from:], "regenerate": regenerate,
        "checkpoint": session.checkpoint.model_dump(mode="json") if session.checkpoint else None,
        "checkpoints": [value.model_dump(mode="json") for value in session.checkpoints], "tokenRatio": session.token_ratio,
        "input": {**turn_input, "historyStart": replace_from},
        "reply": {**result.to_api_dict(), "characterName": character.name_zh}}


async def main():
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    data = json.loads(sys.stdin.readline())
    async def progress(value):
        emit({"type": "progress", "data": value})
    try:
        emit({"type": "result", "result": await execute(data, progress=progress)})
    except Exception as exc:
        logging.exception("Harness roleplay turn failed")
        emit({"type": "error", "message": str(exc)})


if __name__ == "__main__":
    asyncio.run(main())
