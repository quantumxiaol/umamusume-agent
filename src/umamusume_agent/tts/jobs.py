"""In-memory asynchronous TTS jobs owned by the project-local MCP server."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .agent import JapaneseDialoguePreparer
from .fish_client import FishSpeechHttpClient
from .models import (
    TERMINAL_TTS_JOB_STATES,
    TTSJobSnapshot,
    TTSSubmitRequest,
)


logger = logging.getLogger(__name__)
_SAFE_PATH_PART = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe_part(value: str) -> str:
    normalized = _SAFE_PATH_PART.sub("_", value.strip())
    return normalized[:100] or "anonymous"


class TTSJobNotFound(LookupError):
    pass


class TTSJobManager:
    """Run a bounded number of deterministic translation/synthesis jobs."""

    def __init__(
        self,
        *,
        preparer: JapaneseDialoguePreparer,
        fish_client: FishSpeechHttpClient,
        outputs_dir: Path,
        max_concurrent_jobs: int,
        audio_format: str,
        speaker_prefix: str,
        fish_generation_options: dict[str, object],
        job_ttl_seconds: int = 3600,
    ):
        self.preparer = preparer
        self.fish_client = fish_client
        self.outputs_dir = outputs_dir
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self.audio_format = audio_format.strip().lower() or "wav"
        self.speaker_prefix = speaker_prefix
        self.fish_generation_options = {
            key: value
            for key, value in fish_generation_options.items()
            if value not in (None, "", 0)
        }
        self.job_ttl_seconds = max(0, job_ttl_seconds)
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent_jobs))
        self._jobs: dict[str, TTSJobSnapshot] = {}
        self._requests: dict[str, TTSSubmitRequest] = {}
        self._idempotency: dict[str, str] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None

    async def submit(self, request: TTSSubmitRequest) -> TTSJobSnapshot:
        self._ensure_cleanup_task()
        subtitle = request.subtitle_zh.strip()
        if not subtitle:
            raise ValueError("subtitle_zh must not be empty")
        if not Path(request.speaker.reference_audio_path).is_file():
            raise FileNotFoundError(
                f"Reference audio not found: "
                f"{request.speaker.reference_audio_path}"
            )

        idempotency_key = (
            f"{request.user_uuid}:{request.source_session_id}:"
            f"{request.utterance_id}:{request.speaker.actor_id}"
        )
        async with self._lock:
            self._cleanup_expired()
            existing_job_id = self._idempotency.get(idempotency_key)
            if existing_job_id:
                existing = self._jobs.get(existing_job_id)
                if existing is not None:
                    return existing

            now = datetime.now()
            job_id = f"tts_{uuid4().hex}"
            snapshot = TTSJobSnapshot(
                job_id=job_id,
                state="queued",
                user_uuid=request.user_uuid,
                source_session_id=request.source_session_id,
                utterance_id=request.utterance_id,
                speaker_id=request.speaker.actor_id,
                speaker_name=request.speaker.name_zh,
                subtitle_zh=subtitle,
                created_at=now,
                updated_at=now,
                audio_format=self.audio_format,
            )
            self._jobs[job_id] = snapshot
            self._requests[job_id] = request
            self._idempotency[idempotency_key] = job_id
            task = asyncio.create_task(
                self._run(job_id),
                name=f"tts-job-{job_id}",
            )
            self._tasks[job_id] = task
            task.add_done_callback(
                lambda _task, current_job_id=job_id: self._tasks.pop(
                    current_job_id,
                    None,
                )
            )
            return snapshot

    async def get(
        self,
        *,
        job_id: str,
        user_uuid: str,
    ) -> TTSJobSnapshot:
        self._ensure_cleanup_task()
        self._cleanup_expired()
        snapshot = self._jobs.get(job_id)
        if snapshot is None or snapshot.user_uuid != user_uuid:
            raise TTSJobNotFound(job_id)
        return snapshot

    def _ensure_cleanup_task(self) -> None:
        if self.job_ttl_seconds <= 0:
            return
        if self._cleanup_task is not None and not self._cleanup_task.done():
            return
        self._cleanup_task = asyncio.create_task(
            self._cleanup_loop(),
            name="tts-job-cleanup",
        )

    async def _cleanup_loop(self) -> None:
        interval = min(
            300.0,
            max(5.0, self.job_ttl_seconds / 2),
        )
        try:
            while True:
                await asyncio.sleep(interval)
                async with self._lock:
                    self._cleanup_expired()
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        cleanup_task = self._cleanup_task
        self._cleanup_task = None
        if cleanup_task is not None and not cleanup_task.done():
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
        active_tasks = [
            task for task in self._tasks.values()
            if not task.done()
        ]
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)

    def _cleanup_expired(self) -> None:
        if self.job_ttl_seconds <= 0:
            return
        now = datetime.now()
        expired = [
            job_id
            for job_id, snapshot in self._jobs.items()
            if (
                snapshot.state in TERMINAL_TTS_JOB_STATES
                and (
                    now - snapshot.updated_at
                ).total_seconds() > self.job_ttl_seconds
            )
        ]
        for job_id in expired:
            snapshot = self._jobs.pop(job_id)
            self._requests.pop(job_id, None)
            self._tasks.pop(job_id, None)
            for key, value in list(self._idempotency.items()):
                if value == job_id:
                    self._idempotency.pop(key, None)
            audio_path = Path(snapshot.audio_path) if snapshot.audio_path else None
            if (
                audio_path is not None
                and audio_path.is_file()
                and audio_path.resolve().is_relative_to(
                    self.outputs_dir.resolve()
                )
            ):
                audio_path.unlink()

    async def cancel(
        self,
        *,
        job_id: str,
        user_uuid: str,
    ) -> TTSJobSnapshot:
        snapshot = await self.get(job_id=job_id, user_uuid=user_uuid)
        if snapshot.state in TERMINAL_TTS_JOB_STATES:
            return snapshot
        task = self._tasks.get(job_id)
        if task is not None:
            task.cancel()
        self._set_state(job_id, "cancelled")
        return self._jobs[job_id]

    def _set_state(self, job_id: str, state: str, **updates: object) -> None:
        snapshot = self._jobs[job_id]
        payload = snapshot.model_dump()
        payload.update(updates)
        payload["state"] = state
        payload["updated_at"] = datetime.now()
        self._jobs[job_id] = TTSJobSnapshot.model_validate(payload)

    async def _run(self, job_id: str) -> None:
        request = self._requests[job_id]
        output_path: Path | None = None
        try:
            async with self._semaphore:
                if self._jobs[job_id].state == "cancelled":
                    return
                self._set_state(job_id, "translating")
                prepared = await self.preparer.prepare(request)

                self._set_state(
                    job_id,
                    "validating",
                    subtitle_ja=prepared.subtitle_ja,
                    spoken_text_ja=prepared.spoken_text_ja,
                )
                if self._jobs[job_id].state == "cancelled":
                    return

                self._set_state(job_id, "synthesizing")
                output_dir = (
                    self.outputs_dir
                    / "tts_jobs"
                    / _safe_part(request.user_uuid)
                )
                output_path = output_dir / f"{job_id}.{self.audio_format}"
                fish_text = prepared.fish_text
                if (
                    self.speaker_prefix
                    and not fish_text.startswith("<|speaker:")
                ):
                    fish_text = f"{self.speaker_prefix}{fish_text}"

                reference_text_path = (
                    Path(request.speaker.reference_text_path)
                    if (
                        request.speaker.reference_text_path
                        and not request.speaker.reference_text_ja
                    )
                    else None
                )
                await self.fish_client.voice_clone(
                    ref_audio_path=Path(
                        request.speaker.reference_audio_path
                    ),
                    text=fish_text,
                    destination=output_path,
                    ref_text=request.speaker.reference_text_ja,
                    ref_text_path=reference_text_path,
                    output_name=output_path.name,
                    audio_format=self.audio_format,
                    generation_options=self.fish_generation_options,
                    on_download_start=lambda: self._set_state(
                        job_id,
                        "downloading",
                    ),
                )
                self._set_state(
                    job_id,
                    "ready",
                    audio_path=str(output_path),
                )
        except asyncio.CancelledError:
            self._remove_partial_audio(output_path)
            if self._jobs[job_id].state != "cancelled":
                self._set_state(job_id, "cancelled")
            raise
        except Exception as exc:
            self._remove_partial_audio(output_path)
            logger.exception("TTS job failed: %s", job_id)
            self._set_state(
                job_id,
                "failed",
                error=str(exc),
                retryable=True,
            )

    def _remove_partial_audio(self, path: Path | None) -> None:
        if path is None or not path.is_file():
            return
        try:
            if path.resolve().is_relative_to(self.outputs_dir.resolve()):
                path.unlink()
        except Exception:
            logger.exception("Failed to remove partial TTS audio: %s", path)
