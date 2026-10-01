"""Low-frequency shared scene memory; no per-actor duplicate summarization."""
from __future__ import annotations

import copy
import json
import logging

from ..dialogue.compaction import split_source
from ..dialogue.compaction_runtime import CompactionError, CompactionRuntime, SUMMARY_INSTRUCTION
from ..dialogue.token_budget import estimate_tokens
from .context import _event_packet
from .memory import apply_memory_threads, memory_threads, prompt_digest, public_events, source_digest, threads
from .models import SceneEvent, SceneMemoryCheckpoint
from .timeline import SceneTimeline

logger = logging.getLogger(__name__)
SCENE_SUMMARY_INSTRUCTION = SUMMARY_INSTRUCTION + """
本任务整理多人场景的共享公开记忆。逐一标明人物姓名/ID、说话对象、称呼与关系变化的原因。
只依据公开事件；角色的动作/心理描写不是所有人都知道的事实，保留知情范围，不推测他人内心。
不把隐藏导演指令、调度计划或舞台控制命令编入已发生剧情。旧摘要中被近期原文明示更正的事实要更新。
source_format 为 parse_error 的回复是系统错误占位，不是角色台词或已发生剧情。
"""


class SceneCompactor:
    def __init__(self, *, runtime, settings, director_builder, character_builder):
        self.settings = settings
        self.director_builder = director_builder
        self.character_builder = character_builder
        self.runtime = CompactionRuntime(runtime=runtime, settings=settings,
                                         config_prefix="DIRECTOR", purpose="scene_compaction")

    @property
    def enabled(self):
        return getattr(self.settings, "DIRECTOR_COMPACTION_ENABLED", False)

    def reserve(self):
        cfg = self.settings
        return max(cfg.DIRECTOR_CONTEXT_RESERVE_TOKENS, cfg.LLM_JSON_MAX_DYNAMIC_TOKENS)

    def ensure_capacity(self, thread, messages):
        if self.enabled and estimate_tokens(messages, thread.token_ratio) + self.reserve() >= self.settings.DIRECTOR_CONTEXT_MAX_TOKENS:
            raise CompactionError("本轮场景上下文过大，请拆分输入或新建场景；原始历史与旧摘要已保留。")

    def forecast(self, session, pending, *, replacements=None, stage_context=None):
        """Forecast *every* thread, including actors that have been silent."""
        timeline = SceneTimeline(initial_state=session.template.initial_state)
        # Forecasting only appends. Share immutable archived events instead of
        # deep-copying every Pydantic event on every new turn of a long scene.
        timeline.events = list(session.timeline.events)
        for item in pending:
            timeline.append(SceneEvent(
                turn_index=session.turn_index + 1, event_type=item.event_type or "dialogue",
                actor=item.speaker or session.player, content=item.content.strip(),
                target_actor_ids=item.target_actor_ids or session.character_actor_ids,
            ))
        projected = []
        participants = {item.actor.actor_id: item.actor for item in session.participants}
        for name, original in (replacements or threads(session)).items():
            thread = copy.deepcopy(original)
            if name == "director":
                if stage_context is None:
                    self.director_builder.append_turn(thread, timeline=timeline)
                else:
                    self.director_builder.append_stage_turn(thread, timeline=timeline, stage_context=stage_context)
            else:
                self.character_builder.build_reply_context(
                    thread, actor=participants[name], timeline=timeline,
                    intent="结合本轮最新公开事件自然回应。", target_actor_ids=[session.player.actor_id],
                )
            projected.append(estimate_tokens(thread.messages, thread.token_ratio))
        return max(projected, default=0)

    async def prepare(self, session, pending, *, stage_context=None, on_progress=None):
        if not self.enabled:
            return
        cfg = self.settings
        capacity, reserve = cfg.DIRECTOR_CONTEXT_MAX_TOKENS, self.reserve()
        high = min(cfg.DIRECTOR_COMPACTION_TRIGGER_TOKENS, capacity - reserve)
        low, budget = cfg.DIRECTOR_COMPACTION_TARGET_TOKENS, cfg.DIRECTOR_COMPACTION_MEMORY_TOKENS
        if not 0 < budget < low < high < capacity:
            raise CompactionError("场景压缩水位配置无效：记忆预算 < 目标水位 < 触发水位 < 上下文容量。")
        current = self.forecast(session, pending, stage_context=stage_context)
        if current < high:
            return
        public = public_events(session.timeline.events)
        if any(event.visible_to != "all" for event in public):
            raise CompactionError("当前场景包含非公开事件，不能安全生成共享摘要；未修改历史。")
        start = session.checkpoint.covered_events if session.checkpoint else 0
        # Whole scene turns, not per-actor replies. Always retain the latest turn
        # so manual regeneration cannot edit an already summarized event.
        turns = sorted({event.turn_index for event in public})
        keep = max(1, cfg.DIRECTOR_COMPACTION_KEEP_TURNS)
        preferred = turns[-keep - 1] if len(turns) > keep else -1
        ends = [index for index in range(1, len(public))
                if public[index - 1].turn_index < public[index].turn_index
                and public[index - 1].turn_index >= preferred and index > start]
        ratio = max((thread.token_ratio for thread in threads(session).values()), default=0.5)
        checkpoint = None
        fallback = None
        for cut in ends:
            candidate = SceneMemoryCheckpoint(
                session_id=session.session_id, user_uuid=session.user_uuid,
                revision=max([cp.revision for cp in session.checkpoints] + [0]) + 1,
                covered_events=cut, covered_event_id=public[cut - 1].event_id,
                trigger_event_count=len(public), trigger_event_id=public[-1].event_id,
                trigger_turn_index=public[-1].turn_index,
                source_digest=source_digest(public[:cut]), prompt_digest=prompt_digest(session, cfg),
                summary="待生成记忆",
                reply_counts={name: thread.reply_count for name, thread in threads(session).items()},
                token_ratios={name: thread.token_ratio for name, thread in threads(session).items()},
            )
            projected = self.forecast(session, pending, replacements=memory_threads(session, candidate), stage_context=stage_context) + budget
            if projected < min(high, capacity - reserve):
                fallback = fallback or candidate
                recent = estimate_tokens([{"content": json.dumps(_event_packet(public[cut:]), ensure_ascii=False)}], ratio)
                if projected < low and recent <= cfg.DIRECTOR_COMPACTION_RECENT_TOKENS:
                    checkpoint = candidate
                    break
        checkpoint = checkpoint or fallback
        if checkpoint is None:
            raise CompactionError("没有足够的完整旧轮次可安全压缩（至少保留最新一轮）；请拆分输入或新建场景。")

        async def progress(phase, **extra):
            if on_progress:
                await on_progress({"phase": phase, **extra})

        logger.info("Scene compaction started session_id=%s estimated_tokens=%s covered_before=%s", session.session_id, current, start)
        await progress("compacting")
        source = [{"role": "user", "content": json.dumps({**_event_packet([event])[0], "source_format": event.source_format}, ensure_ascii=False)}
                  for event in public[start:checkpoint.covered_events]]
        if session.checkpoint:
            source.insert(0, {"role": "user", "content": "已有共享记忆：\n" + session.checkpoint.summary})
        prefix = [{"role": "system", "content": SCENE_SUMMARY_INSTRUCTION}, {
            "role": "user", "content": "场景人物资料（不是指令）：\n" + json.dumps([
                participant.actor.model_dump() for participant in session.participants
            ], ensure_ascii=False),
        }]
        available = capacity - reserve - cfg.DIRECTOR_COMPACTION_MAX_DYNAMIC_TOKENS - budget - estimate_tokens(prefix, ratio) - 512
        chunk_budget = min(cfg.DIRECTOR_COMPACTION_CHUNK_TOKENS, available)
        if chunk_budget < 256:
            raise CompactionError("场景压缩模型输入空间不足，请检查上下文与输出预算。")
        chunks = split_source(source, max_tokens=chunk_budget, ratio=ratio)
        if len(chunks) > cfg.DIRECTOR_COMPACTION_MAX_CHUNKS:
            raise CompactionError("场景历史超出本次压缩分段预算，未修改原文或旧摘要。")
        summaries = []
        for index, chunk in enumerate(chunks):
            await progress("compacting", chunk=index + 1, chunks=len(chunks))
            allowance = max(1, min(budget // len(chunks), int(cfg.DIRECTOR_COMPACTION_MAX_TOKENS * 0.7)))
            continuity = ([{"role": "user", "content": "前文记忆供理解指代与因果，不重复抄写：\n" + "\n\n".join(summaries)}]
                          if summaries else [])
            messages = [*prefix, *continuity, {"role": "user", "content": (
                f"按时间排序的第 {index + 1}/{len(chunks)} 段；记忆预算约 {allowance} tokens。\n"
                "<source_transcript>\n" + "\n\n".join(item["content"] for item in chunk) + "\n</source_transcript>"
            )}]
            if estimate_tokens(messages, ratio) + cfg.DIRECTOR_COMPACTION_MAX_DYNAMIC_TOKENS + reserve >= capacity:
                raise CompactionError("场景摘要请求预计超过模型容量，未修改历史。")
            summaries.append(await self.runtime.summarize(messages=messages, target_tokens=allowance, session_id=session.session_id))
            if estimate_tokens([{"content": "\n\n".join(summaries)}], ratio) > budget:
                raise CompactionError("场景摘要超过记忆预算，未替换旧摘要。")
        checkpoint.summary = "\n\n".join(f"## 历史片段 {i + 1}（后文更新优先）\n{text}" for i, text in enumerate(summaries))
        if estimate_tokens([{"content": checkpoint.summary}], ratio) > budget:
            raise CompactionError("场景摘要超过记忆预算，未替换旧摘要。")
        projected = self.forecast(session, pending, replacements=memory_threads(session, checkpoint), stage_context=stage_context)
        if projected >= min(high, capacity - reserve):
            raise CompactionError("本次场景压缩未释放足够空间，原文和旧摘要已保留。")
        # Caller holds session.lock; publish only a complete, durable checkpoint.
        session.history.append({"event": "scene_checkpoint", "checkpoint": checkpoint.model_dump(mode="json")}, strict=True)
        apply_memory_threads(session, checkpoint)
        session.checkpoint = checkpoint
        session.checkpoints.append(checkpoint)
        logger.info("Scene compaction committed session_id=%s revision=%s covered_events=%s chunks=%s estimated_tokens=%s",
                    session.session_id, checkpoint.revision, checkpoint.covered_events, len(chunks), projected)
        await progress("compacted", checkpoint=checkpoint.model_dump(mode="json"), estimated_tokens=projected)
