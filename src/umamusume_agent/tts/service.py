"""TTS orchestration independent from FastAPI routes."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

from ..dialogue.history import slugify
from ..dialogue.protocol import strip_stage_directions
from .mcp_client import IndexTTSMCPClient, MCPToolError


logger = logging.getLogger(__name__)


class VoiceService:
    def __init__(
        self,
        *,
        client: IndexTTSMCPClient,
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

