"""TTS orchestration independent from FastAPI routes."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import quote

from ..dialogue.history import slugify
from ..dialogue.protocol import strip_stage_directions
from .mcp_client import MCPToolError, TTSMCPClient
from .models import (
    TTSCastMember,
    TTSCharacterProfile,
    TTSContextEvent,
    TTSSubmitRequest,
)


logger = logging.getLogger(__name__)


class VoiceService:
    def __init__(
        self,
        *,
        client: TTSMCPClient,
        outputs_dir: Path,
        characters_dir: Path,
    ):
        self.client = client
        self.outputs_dir = outputs_dir
        self.characters_dir = characters_dir

    def create_output_dir(self, character: Any, created_at: datetime) -> Path:
        safe_name = slugify(character.name_en or character.name_zh)
        timestamp = created_at.strftime("%Y%m%d_%H%M%S")
        base_name = f"{safe_name}_{timestamp}"
        output_dir = self.outputs_dir / base_name
        if output_dir.exists():
            counter = 1
            while (self.outputs_dir / f"{base_name}_{counter}").exists():
                counter += 1
            output_dir = self.outputs_dir / f"{base_name}_{counter}"
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    @staticmethod
    def build_audio_url(path: Path) -> str:
        return f"/audio?path={quote(str(path))}"

    def is_allowed_audio_path(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
            return (
                resolved.is_relative_to(self.outputs_dir)
                or resolved.is_relative_to(self.characters_dir)
            )
        except Exception:
            return False

    def reserve_output(self, session: Any) -> Dict[str, Any]:
        session.voice_index += 1
        output_name = f"reply_{session.voice_index:03d}.wav"
        target_path = session.output_dir / output_name
        return {
            "audio_path": str(target_path),
            "audio_url": self.build_audio_url(target_path),
            "index": session.voice_index,
            "output_dir": str(session.output_dir),
            "output_name": output_name,
        }

    @staticmethod
    def build_job_status_url(job_id: str, user_uuid: str) -> str:
        return (
            f"/tts/jobs/{quote(job_id)}"
            f"?user_uuid={quote(user_uuid)}"
        )

    @staticmethod
    def build_job_audio_url(job_id: str, user_uuid: str) -> str:
        return (
            f"/tts/jobs/{quote(job_id)}/audio"
            f"?user_uuid={quote(user_uuid)}"
        )

    @staticmethod
    def _character_profile(
        character: Any,
        *,
        actor_id: str | None = None,
    ) -> TTSCharacterProfile:
        voice_config = character.get_voice_config()
        personality = getattr(character, "personality", None)
        pronouns = getattr(personality, "pronouns", None)
        get_reference_text = getattr(
            character,
            "get_ref_audio_text",
            lambda: None,
        )
        return TTSCharacterProfile(
            actor_id=actor_id or character.id,
            name_zh=character.name_zh,
            name_jp=character.name_jp,
            system_prompt=character.get_system_prompt(),
            speaking_style=getattr(personality, "speaking_style", "") or "",
            first_person=getattr(pronouns, "self", "") or "",
            user_address=getattr(pronouns, "user", "") or "",
            catchphrases=list(
                getattr(personality, "catchphrases", []) or []
            ),
            reference_audio_path=voice_config.get("ref_audio_path") or "",
            reference_text_path=voice_config.get("ref_text_path") or "",
            reference_text_ja=get_reference_text() or "",
        )

    @staticmethod
    def _cast_members(items: Iterable[dict[str, Any]]) -> list[TTSCastMember]:
        return [
            TTSCastMember.model_validate(item)
            for item in items
        ]

    async def submit_dialogue(
        self,
        *,
        user_uuid: str,
        source_session_id: str,
        utterance_id: str,
        character: Any,
        dialogue_text: str,
        actor_id: str | None = None,
        target_actor_ids: list[str] | None = None,
        cast: Iterable[dict[str, Any]] = (),
        context_events: Iterable[dict[str, Any]] = (),
    ) -> Optional[Dict[str, Any]]:
        voice_config = character.get_voice_config()
        if (
            voice_config.get("no_voice")
            or not voice_config.get("ref_audio_path")
        ):
            return None

        tts_text = (dialogue_text or "").strip()
        if not tts_text:
            tts_text = strip_stage_directions(dialogue_text or "")
        if not tts_text:
            return None

        request = TTSSubmitRequest(
            user_uuid=user_uuid,
            source_session_id=source_session_id,
            utterance_id=utterance_id,
            subtitle_zh=tts_text,
            speaker=self._character_profile(
                character,
                actor_id=actor_id,
            ),
            target_actor_ids=list(target_actor_ids or []),
            cast=self._cast_members(cast),
            context_events=[
                TTSContextEvent.model_validate(item)
                for item in context_events
            ],
        )
        try:
            snapshot = await self.client.submit(
                request.model_dump(mode="json")
            )
        except Exception:
            logger.exception(
                "Failed to submit TTS job for utterance %s",
                utterance_id,
            )
            return None
        return self._public_job(snapshot, user_uuid=user_uuid)

    def _public_job(
        self,
        snapshot: Dict[str, Any],
        *,
        user_uuid: str,
    ) -> Dict[str, Any]:
        job_id = str(snapshot.get("job_id") or "")
        payload = {
            key: value
            for key, value in snapshot.items()
            if key not in {"audio_path", "user_uuid"}
        }
        payload.update(
            {
                "requested": True,
                "job_id": job_id,
                "status_url": self.build_job_status_url(
                    job_id,
                    user_uuid,
                ),
                "audio_url": (
                    self.build_job_audio_url(job_id, user_uuid)
                    if snapshot.get("state") == "ready"
                    else ""
                ),
            }
        )
        return payload

    async def get_job(
        self,
        *,
        job_id: str,
        user_uuid: str,
    ) -> Dict[str, Any]:
        snapshot = await self.client.get_job(job_id, user_uuid)
        return self._public_job(snapshot, user_uuid=user_uuid)

    async def cancel_job(
        self,
        *,
        job_id: str,
        user_uuid: str,
    ) -> Dict[str, Any]:
        snapshot = await self.client.cancel(job_id, user_uuid)
        return self._public_job(snapshot, user_uuid=user_uuid)

    async def resolve_job_audio(
        self,
        *,
        job_id: str,
        user_uuid: str,
    ) -> Path:
        snapshot = await self.client.get_job(job_id, user_uuid)
        if snapshot.get("state") != "ready":
            raise FileNotFoundError("TTS audio is not ready")
        path = Path(str(snapshot.get("audio_path") or ""))
        if (
            not path.is_file()
            or not self.is_allowed_audio_path(path)
            or not path.resolve().is_relative_to(self.outputs_dir)
        ):
            raise FileNotFoundError("TTS audio is unavailable")
        return path

    async def generate_for_reply(
        self,
        session: Any,
        dialogue_text: str,
        voice_plan: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        try:
            voice_config = session.character.get_voice_config()
            if (
                voice_config.get("no_voice")
                or not voice_config.get("ref_audio_path")
            ):
                logger.info(
                    "Skipping TTS generation for session %s: no_voice is True or ref_audio_path is missing",
                    session.session_id,
                )
                return None

            prompt_audio_path = voice_config["ref_audio_path"]
            output_name = voice_plan["output_name"]
            target_path = Path(voice_plan["audio_path"])
            raw_dialogue = dialogue_text or ""
            tts_text = raw_dialogue.strip()
            if not tts_text:
                tts_text = strip_stage_directions(raw_dialogue)
            if not tts_text:
                logger.warning(
                    "Skipping TTS generation for session %s: empty dialogue text",
                    session.session_id,
                )
                return None

            result = await self.client.synthesize(
                text=tts_text,
                prompt_wav_path=prompt_audio_path,
                output_name=output_name,
            )

            source_path = Path(result.get("audio_path", ""))
            if not source_path.exists():
                raise FileNotFoundError(
                    f"TTS output not found: {source_path}"
                )

            if source_path.resolve() != target_path.resolve():
                if target_path.exists():
                    target_path.unlink()
                shutil.move(str(source_path), str(target_path))

            voice_info = {
                "audio_path": str(target_path),
                "audio_url": self.build_audio_url(target_path),
                "prompt_audio_path": result.get("prompt_audio_path"),
                "sample_rate": result.get("sample_rate"),
                "index": voice_plan["index"],
                "output_dir": voice_plan["output_dir"],
                "tts_text": tts_text,
            }
            session.audio_history.append(voice_info)
            return voice_info

        except (MCPToolError, FileNotFoundError) as exc:
            logger.error("TTS MCP error: %s", exc)
            return None
        except Exception as exc:
            logger.error("Failed to generate voice: %s", exc)
            return None
