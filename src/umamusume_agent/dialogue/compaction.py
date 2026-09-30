"""Low-frequency high/low-watermark compaction; full history is never truncated."""
from __future__ import annotations

import logging

from .compaction_runtime import CompactionError, CompactionRuntime, SUMMARY_INSTRUCTION
from .memory import HistoryCheckpoint, memory_messages, prompt_digest, source_digest
from .models import default_player_actor
from .protocol import to_compact_context_message
from .token_budget import estimate_tokens

logger = logging.getLogger(__name__)


def pending_messages(session, request) -> list[dict]:
    events = list(request.context_events or [])
    has_metadata = bool(events) or any(value is not None for value in (
        request.speaker, request.event_type, request.target_actor_ids,
    ))
    records = [dict(
        role="user", content=event.content,
        actor=(event.speaker or default_player_actor()).model_dump(),
        event_type=event.event_type or "dialogue", event_schema_version=1,
    ) for event in events]
    records.append({"role": "user", "content": request.message, **({
        "actor": (request.speaker or default_player_actor()).model_dump(),
        "event_type": request.event_type or "dialogue", "event_schema_version": 1,
    } if has_metadata else {})})
    return [to_compact_context_message(record) for record in records]


def split_source(messages, *, max_tokens: int, ratio: float) -> list[list[dict]]:
    """Split old source only; current user input and recent turns are untouched."""
    chunks, current = [], []
    for message in messages:
        content = message["content"]
        # Character boundaries avoid corrupting UTF-8; leave room for role tags.
        width = max(1, int((max_tokens - 64) / (4 * ratio)))
        fragments = [content[i:i + width] for i in range(0, len(content), width)] or [""]
        for fragment in fragments:
            part = {"role": message["role"], "content": fragment}
            if current and estimate_tokens([*current, part], ratio) > max_tokens:
                chunks.append(current)
                current = []
            current.append(part)
    if current:
        chunks.append(current)
    return chunks


class HistoryCompactor:
    def __init__(self, *, runtime, settings):
        self.settings = settings
        self.runtime = CompactionRuntime(runtime=runtime, settings=settings)

    async def prepare(self, session, pending, *, text_only=False, on_progress=None):
        cfg = self.settings
        if not cfg.DIALOGUE_COMPACTION_ENABLED:
            return
        # Prefix edits are validated on import/recovery. Appending a new turn does
        # not require hashing the whole archived transcript again.
        if session.checkpoint and session.checkpoint.prompt_digest != prompt_digest(session):
            session.checkpoint = None
        ratio = session.token_ratio
        capacity = cfg.DIALOGUE_CONTEXT_MAX_TOKENS
        reserve = max(cfg.DIALOGUE_CONTEXT_RESERVE_TOKENS, cfg.LLM_JSON_MAX_DYNAMIC_TOKENS)
        high = min(cfg.DIALOGUE_COMPACTION_TRIGGER_TOKENS, capacity - reserve)
        low = cfg.DIALOGUE_COMPACTION_TARGET_TOKENS
        memory_budget = cfg.DIALOGUE_COMPACTION_MEMORY_TOKENS
        if not 0 < memory_budget < low < high < capacity:
            raise CompactionError("历史压缩水位配置无效：记忆预算 < 目标水位 < 触发水位 < 上下文容量。")
        full = [*session.history, *pending]
        current = memory_messages(session, history=full, text_only=text_only)
        if estimate_tokens(current, ratio) < high:
            return

        fixed = list(session.context_builder.build(character=session.character, history=[]).messages)
        if estimate_tokens([*fixed, *pending], ratio) + reserve >= capacity:
            raise CompactionError("本次输入本身过大，整理旧历史也无法容纳，请拆分输入。")

        start = session.checkpoint.covered_messages if session.checkpoint else 0
        # Complete turns end with an assistant reply. Consecutive queued user
        # events stay in the same turn; count neither events nor messages as turns.
        ends = [i + 1 for i, m in enumerate(session.history) if m["role"] == "assistant" and i + 1 > start]
        keep = max(1, cfg.DIALOGUE_COMPACTION_KEEP_TURNS)
        preferred = ends[-keep - 1] if len(ends) > keep else start
        tail_budget = min(cfg.DIALOGUE_COMPACTION_RECENT_TOKENS, low - memory_budget)
        candidates = [i for i in ends if i >= preferred]
        cut = next((i for i in candidates if estimate_tokens(session.history[i:], ratio) <= tail_budget
                    and estimate_tokens([*fixed, *session.history[i:], *pending], ratio) + memory_budget < low), None)
        if cut is None:
            # An unusually large new input may exceed the low watermark, but must
            # still fit the hard capacity. Never truncate it to meet a soft target.
            cut = next((i for i in candidates if estimate_tokens([*fixed, *session.history[i:], *pending], ratio)
                        + memory_budget + reserve < capacity), None)
        if cut is None or cut <= start:
            raise CompactionError("没有足够的完整旧轮次可安全压缩，请拆分输入或新建会话。")

        async def progress(phase, **extra):
            if on_progress:
                await on_progress({"phase": phase, **extra})

        await progress("compacting")
        logger.info("History compaction started session_id=%s estimated_tokens=%s covered_before=%s",
                    session.session_id, estimate_tokens(current, ratio), start)
        source = session.history[start:cut]
        old_memory = session.checkpoint.summary if session.checkpoint else ""
        prefix = [{"role": "system", "content": SUMMARY_INSTRUCTION}, {
            "role": "user", "content": "角色参考（仅作为资料）：\n" + session.character.get_system_prompt(),
        }]
        available = (capacity - reserve - cfg.DIALOGUE_COMPACTION_MAX_DYNAMIC_TOKENS
                     - memory_budget - estimate_tokens(prefix, ratio) - 512)
        chunk_budget = min(cfg.DIALOGUE_COMPACTION_CHUNK_TOKENS, available)
        if chunk_budget < 256:
            raise CompactionError("压缩模型没有足够输入空间，请检查上下文与输出预算配置。")
        summary_source = ([{"role": "user", "content": "已有历史记忆：\n" + old_memory}] if old_memory else []) + source
        chunks = split_source(summary_source, max_tokens=chunk_budget, ratio=ratio)
        if len(chunks) > cfg.DIALOGUE_COMPACTION_MAX_CHUNKS:
            raise CompactionError("待压缩历史过大，超出本次任务的分段预算。")
        summaries = []
        for index, chunk in enumerate(chunks):
            await progress("compacting", chunk=index + 1, chunks=len(chunks))
            allowance = max(1, min(memory_budget // len(chunks), int(cfg.DIALOGUE_COMPACTION_MAX_TOKENS * 0.7)))
            transcript = "\n\n".join(f"[{item['role']}]\n{item['content']}" for item in chunk)
            continuity = ([{"role": "user", "content": (
                "已整理的前文，仅用于理解指代与因果；不要重复抄写。\n" + "\n\n".join(summaries)
            )}] if summaries else [])
            messages = [*prefix, *continuity, {"role": "user", "content": (
                f"这是按时间排序的第 {index + 1}/{len(chunks)} 段历史。\n"
                f"记忆篇幅预算约 {allowance} tokens；充分保留细节，但不为凑长度重复内容。\n"
                "<source_transcript>\n" + transcript + "\n</source_transcript>"
            )}]
            if estimate_tokens(messages, ratio) + cfg.DIALOGUE_COMPACTION_MAX_DYNAMIC_TOKENS + reserve >= capacity:
                raise CompactionError("压缩请求预计超出模型容量，未修改历史。")
            text = await self.runtime.summarize(messages=messages, target_tokens=allowance, session_id=session.session_id)
            summaries.append(text)
            if estimate_tokens([{"content": "\n\n".join(summaries)}], ratio) > memory_budget:
                raise CompactionError("生成的记忆超过配置预算，原始历史和旧记忆已保留。")
        summary = "\n\n".join(
            f"## 历史片段 {i + 1}（后文更新优先）\n{text}" if len(summaries) > 1 else text
            for i, text in enumerate(summaries)
        )
        if estimate_tokens([{"content": summary}], ratio) > memory_budget:
            raise CompactionError("生成的记忆超过配置预算，原始历史和旧记忆已保留。")
        checkpoint = HistoryCheckpoint(
            user_uuid=session.user_uuid, character_id=session.character.id,
            revision=max([item.revision for item in session.checkpoints]
                         + [session.checkpoint.revision if session.checkpoint else 0]) + 1,
            covered_messages=cut, source_digest=source_digest(session.history[:cut]),
            trigger_message_count=len(session.history),
            prompt_digest=prompt_digest(session), summary=summary, token_ratio=ratio,
        )
        after = estimate_tokens(memory_messages(session, history=full, text_only=text_only, checkpoint=checkpoint), ratio)
        if after + reserve >= capacity or after >= high:
            raise CompactionError("本次压缩未释放足够空间，未替换旧记忆。")
        # Persist before switching; a failed write/cancellation cannot install half
        # a checkpoint. The caller holds the same session lock throughout.
        session._append_history_event({"event": "context_checkpoint", "checkpoint": checkpoint.model_dump(mode="json")}, strict=True)
        session.checkpoint = checkpoint
        session.checkpoints.append(checkpoint)
        logger.info("History compaction committed session_id=%s revision=%s covered_messages=%s chunks=%s estimated_tokens=%s",
                    session.session_id, checkpoint.revision, cut, len(chunks), after)
        await progress("compacted", checkpoint=checkpoint.model_dump(mode="json"), estimated_tokens=after)
