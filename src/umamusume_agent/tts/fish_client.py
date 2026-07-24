"""Async client for the fish-tts-server API used by umamusume-anime."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx


FISH_API_PREFIX = "/fishspeech"


class FishSpeechError(RuntimeError):
    """Raised when fish-tts-server returns an unusable response."""


def _audio_mime_type(path: Path) -> str:
    known = {
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
    }
    return known.get(
        path.suffix.lower(),
        mimetypes.guess_type(path.name)[0] or "application/octet-stream",
    )


def _form_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class FishSpeechHttpClient:
    """Multipart Fish Speech client with streamed result downloads."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        api_key: str = "",
    ):
        normalized = base_url.rstrip("/")
        if normalized.endswith(FISH_API_PREFIX):
            normalized = normalized[: -len(FISH_API_PREFIX)]
        headers = {}
        if api_key.strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"
        timeout = httpx.Timeout(
            max(1.0, timeout_seconds),
            connect=min(30.0, max(1.0, timeout_seconds)),
        )
        self._client = httpx.AsyncClient(
            base_url=normalized,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict[str, Any]:
        response = await self._client.get(f"{FISH_API_PREFIX}/health")
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {"status": "ok"}

    async def voice_clone(
        self,
        *,
        ref_audio_path: Path,
        text: str,
        destination: Path,
        ref_text: str = "",
        ref_text_path: Path | None = None,
        output_name: str | None = None,
        audio_format: str = "wav",
        generation_options: dict[str, Any] | None = None,
        on_download_start: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if not ref_audio_path.is_file():
            raise FileNotFoundError(f"Reference audio not found: {ref_audio_path}")
        if ref_text_path is not None and not ref_text_path.is_file():
            raise FileNotFoundError(
                f"Reference transcript not found: {ref_text_path}"
            )

        data: dict[str, str] = {
            "format": audio_format,
            "text": text,
        }
        if ref_text:
            data["ref_text"] = ref_text
        if output_name:
            data["output_name"] = output_name
        for key, value in (generation_options or {}).items():
            if value is not None:
                data[key] = _form_value(value)

        files: dict[str, tuple[str, bytes, str]] = {
            "ref_audio": (
                ref_audio_path.name,
                ref_audio_path.read_bytes(),
                _audio_mime_type(ref_audio_path),
            )
        }
        if ref_text_path is not None:
            files["ref_text_file"] = (
                ref_text_path.name,
                ref_text_path.read_bytes(),
                "text/plain",
            )

        response = await self._client.post(
            f"{FISH_API_PREFIX}/tts/voice_clone",
            data=data,
            files=files,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise FishSpeechError("Fish Speech response must be a JSON object")

        stored = payload.get("audio")
        if not isinstance(stored, (dict, str)):
            raise FishSpeechError(
                "Fish Speech response did not include an audio object"
            )
        audio_url = self._stored_audio_url(stored)
        if on_download_start is not None:
            on_download_start()
        await self._download(audio_url, destination)
        return {
            "audio_path": str(destination),
            "audio": stored,
            "raw": payload,
        }

    @staticmethod
    def _stored_audio_url(stored: dict[str, Any] | str) -> str:
        if isinstance(stored, str) and stored.strip():
            return stored.strip()
        if isinstance(stored, dict):
            for key in (
                "url",
                "download_url",
                "file_url",
                "href",
                "path",
            ):
                value = stored.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        raise FishSpeechError(
            "Fish Speech audio object did not include a downloadable URL"
        )

    async def _download(self, url: str, destination: Path) -> None:
        parsed = urlparse(url)
        if not parsed.scheme and not url.startswith("/"):
            url = f"/{url}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        async with self._client.stream("GET", url) as response:
            response.raise_for_status()
            with destination.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    handle.write(chunk)
        if not destination.is_file() or destination.stat().st_size <= 0:
            raise FishSpeechError("Downloaded Fish Speech audio is empty")
