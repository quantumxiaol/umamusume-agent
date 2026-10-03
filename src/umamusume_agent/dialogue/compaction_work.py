"""Resumable summary drafts, bounded shrinking and content-free diagnostics.

Drafts are disposable sidecars, never active memories or browser restore data.
The caller holds the session lock and alone publishes the final checkpoint.
"""
from __future__ import annotations

import asyncio
from functools import wraps
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
from time import monotonic

from .compaction_runtime import CompactionError, SUMMARY_INSTRUCTION
from .token_budget import estimate_tokens
from ..llm_diagnostics import llm_request_scope

logger = logging.getLogger(__name__)
MAX_SHRINK_ATTEMPTS = 2


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def logged_compaction(method):
    @wraps(method)
    async def wrapped(self, session, *args, **kwargs):
        started = monotonic()
        try:
            return await method(self, session, *args, **kwargs)
        except asyncio.CancelledError:
            logger.warning("Compaction cancelled session_id=%s purpose=%s elapsed_ms=%s; completed drafts retained",
                           session.session_id, self.runtime.purpose, round((monotonic() - started) * 1000))
            raise
        except Exception as exc:
            # Never log upstream exception bodies, prompts or summary contents.
            logger.error("Compaction failed session_id=%s purpose=%s elapsed_ms=%s error_type=%s reason=%s",
                         session.session_id, self.runtime.purpose, round((monotonic() - started) * 1000),
                         type(exc).__name__, str(exc) if isinstance(exc, CompactionError) else "internal_error")
            raise
    return wrapped


class SummaryDraft:
    """One bounded job per session file; invalid fingerprints discard stale work."""
    def __init__(self, history_file, identity):
        self.path = Path(history_file).with_suffix(".compaction.json")
        self.identity = fingerprint(identity)
        self.entries = {}
        try:
            if self.path.stat().st_size <= 32 * 1024 * 1024:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                entries = data.get("entries", {})
                if data.get("identity") == self.identity and isinstance(entries, dict) and len(entries) <= 256:
                    self.entries = {key: value for key, value in entries.items()
                                    if isinstance(key, str) and isinstance(value, str) and value.strip()}
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError) as exc:
            logger.warning("Compaction draft ignored error_type=%s", type(exc).__name__)

    def save(self, key, text):
        self.entries[key] = text
        if len(self.entries) > 256:
            self.entries.pop(next(iter(self.entries)))
        self._write()

    def discard(self, key):
        self.entries.pop(key, None)
        self._write()

    def _write(self):
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".compaction-", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                json.dump({"identity": self.identity, "entries": self.entries}, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            logger.error("Compaction draft write failed error_type=%s", type(exc).__name__)
            raise CompactionError("压缩分段草稿保存失败，已停止后续调用；原始历史与旧记忆未修改。") from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Compaction temporary draft cleanup failed")

    def clear(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Compaction draft cleanup failed error_type=%s", type(exc).__name__)


class CompactionWork:
    def __init__(self, *, runtime, session, chunks, prefix, ratio, budget, capacity, reserve, scene=False, prompt_key=""):
        self.runtime, self.session = runtime, session
        self.chunks, self.prefix = chunks, prefix
        self.ratio, self.budget = ratio, budget
        self.capacity, self.reserve, self.scene = capacity, reserve, scene
        cfg = runtime.settings
        self.draft = SummaryDraft(session.history_file, {
            "version": 1, "user": session.user_uuid, "session": session.session_id,
            "purpose": runtime.purpose, "model": cfg.ROLEPLAY_LLM_MODEL_NAME,
            "base_url": str(getattr(cfg, "ROLEPLAY_LLM_MODEL_BASE_URL", "")),
            "chunks": chunks, "prefix": prefix, "ratio": ratio, "budget": budget,
            "prompt_key": prompt_key, "scene": scene,
            "output": runtime.setting("MAX_TOKENS"), "output_limit": runtime.setting("MAX_DYNAMIC_TOKENS"),
            "capacity": capacity, "reserve": reserve,
        })

    def format(self, summaries):
        return "\n\n".join(
            f"## 历史片段 {index + 1}（后文更新优先）\n{text}" if self.scene or len(summaries) > 1 else text
            for index, text in enumerate(summaries)
        )

    async def request(self, messages, allowance, *, chunk, stage):
        if estimate_tokens(messages, self.ratio) + self.runtime.setting("MAX_DYNAMIC_TOKENS") + self.reserve >= self.capacity:
            raise CompactionError("压缩请求预计超出模型容量，原文与旧摘要已保留。")
        key = fingerprint({"messages": messages, "target": allowance, "stage": stage})
        if key in self.draft.entries:
            logger.info("Compaction draft reused session_id=%s chunk=%s stage=%s", self.session.session_id, chunk, stage)
            return self.draft.entries[key], True, key
        with llm_request_scope(chunk_index=chunk, chunk_count=len(self.chunks), compaction_stage=stage):
            text = await self.runtime.summarize(messages=messages, target_tokens=allowance, session_id=self.session.session_id)
        if not isinstance(text, str) or not text.strip():
            raise CompactionError("压缩分段返回空内容，未修改旧记忆。")
        self.draft.save(key, text)
        return text, False, key

    async def run(self, progress):
        # Reserve all headings/separators before allocating individual pieces.
        overhead = estimate_tokens([{"content": self.format([""] * len(self.chunks))}], self.ratio)
        allowance = min((self.budget - overhead) // max(1, len(self.chunks)),
                        int(self.runtime.setting("MAX_TOKENS") * 0.7))
        if allowance <= 40:
            raise CompactionError("摘要分段的可用记忆预算过小，请调整记忆预算或分段大小。")
        logger.info("Compaction work planned session_id=%s chunks=%s target_tokens=%s memory_budget=%s token_ratio=%s",
                    self.session.session_id, len(self.chunks), allowance, self.budget, self.ratio)
        summaries = []
        for index, chunk in enumerate(self.chunks, 1):
            await progress("compacting", chunk=index, chunks=len(self.chunks), stage="summarizing")
            continuity = ([{"role": "user", "content": "前文记忆供理解指代与因果，不重复抄写：\n" + "\n\n".join(summaries)}]
                          if summaries else [])
            transcript = "\n\n".join(item["content"] if self.scene else f"[{item['role']}]\n{item['content']}" for item in chunk)
            chinese_chars = max(1, int((allowance - 38) / self.ratio / 3 * 0.9))
            messages = [*self.prefix, *continuity, {"role": "user", "content": (
                f"按时间排序的第 {index}/{len(self.chunks)} 段；记忆预算约 {allowance} tokens。\n"
                f"正文控制在约 {chinese_chars} 个中文字以内，不为凑长度重复内容。\n"
                "<source_transcript>\n" + transcript + "\n</source_transcript>"
            )}]
            text, reused, key = await self.request(messages, allowance, chunk=index, stage="summarizing")
            for attempt in range(MAX_SHRINK_ATTEMPTS + 1):
                measured = estimate_tokens([{"content": text}], self.ratio)
                if measured <= allowance:
                    break
                logger.warning("Compaction summary over budget session_id=%s chunk=%s estimated_tokens=%s target_tokens=%s shrink_attempt=%s",
                               self.session.session_id, index, measured, allowance, attempt)
                if attempt == MAX_SHRINK_ATTEMPTS:
                    # Retry only the failed final shrink, not the paid source summaries.
                    self.draft.discard(key)
                    raise CompactionError(f"第 {index}/{len(self.chunks)} 段摘要收缩后仍超过预算，已保留分段草稿；原文与旧记忆未修改。")
                await progress("compacting", chunk=index, chunks=len(self.chunks), stage="shrinking")
                # Separate body size from provider output tokens, which include reasoning.
                byte_limit = max(1, int((allowance - 38) / self.ratio))
                chars = max(1, int(len(text) * byte_limit / max(1, len(text.encode('utf-8'))) * 0.85))
                shrink_messages = [{"role": "system", "content": SUMMARY_INSTRUCTION +
                    "\n现在仅精简已生成的记忆，合并重复表述；保留姓名、称呼、因果、承诺、否定和知情范围，不续写。"},
                    {"role": "user", "content": f"正文最多约 {chars} 字符（UTF-8 {byte_limit} 字节），优先保留重要事实。\n<summary>\n{text}\n</summary>"}]
                text, shrink_reused, key = await self.request(shrink_messages, allowance, chunk=index, stage=f"shrinking_{attempt + 1}")
                reused = reused or shrink_reused
            summaries.append(text)
            logger.info("Compaction chunk ready session_id=%s chunk=%s chunks=%s estimated_tokens=%s reused=%s",
                        self.session.session_id, index, len(self.chunks), measured, reused)
            await progress("compacting", chunk=index, chunks=len(self.chunks), stage="ready", reused=reused)
        summary = self.format(summaries)
        measured = estimate_tokens([{"content": summary}], self.ratio)
        if measured > self.budget:
            raise CompactionError("最终摘要超过记忆预算，分段草稿已保留，未替换旧摘要。")
        return summary
