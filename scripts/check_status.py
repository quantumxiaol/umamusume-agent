#!/usr/bin/env python3
"""
Check local status for device info and prompt/voice data coverage.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class DeviceInfo:
    os_name: str
    os_version: str
    machine: str
    python_version: str
    cpu_count: int
    torch_version: Optional[str]
    cuda_available: Optional[bool]
    mps_available: Optional[bool]


def _normalize_name(value: str) -> str:
    cleaned = value.strip().replace("_", " ").replace("　", " ")
    cleaned = " ".join(cleaned.split())
    return cleaned.lower()


def _get_device_info() -> DeviceInfo:
    torch_version = None
    cuda_available = None
    mps_available = None

    try:
        import torch  # type: ignore

        torch_version = torch.__version__
        cuda_available = bool(torch.cuda.is_available())
        mps_available = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    except Exception:
        pass

    return DeviceInfo(
        os_name=platform.system(),
        os_version=platform.version(),
        machine=platform.machine(),
        python_version=platform.python_version(),
        cpu_count=os.cpu_count() or 0,
        torch_version=torch_version,
        cuda_available=cuda_available,
        mps_available=mps_available,
    )


def _load_character_map(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return { _normalize_name(en): zh for zh, en in data.items() if isinstance(zh, str) and isinstance(en, str) }


def _list_prompt_files(prompt_dir: Path) -> List[Path]:
    if not prompt_dir.exists():
        return []
    return sorted([p for p in prompt_dir.iterdir() if p.is_file() and p.suffix.lower() in {".md", ".txt"}])


def _list_voice_dirs(voice_dir: Path) -> List[Path]:
    if not voice_dir.exists():
        return []
    return sorted([p for p in voice_dir.iterdir() if p.is_dir()])


def _dir_has_audio_files(path: Path) -> bool:
    for item in path.iterdir():
        if item.is_file() and item.suffix.lower() in {".mp3", ".wav"} and item.stat().st_size > 0:
            return True
    return False


def _build_name_map(items: Iterable[Path]) -> Dict[str, Path]:
    mapped = {}
    for item in items:
        mapped[_normalize_name(item.stem if item.is_file() else item.name)] = item
    return mapped


def _print_device_info(info: DeviceInfo) -> None:
    print("== Device Info ==")
    print(f"OS: {info.os_name} ({info.os_version})")
    print(f"Machine: {info.machine}")
    print(f"Python: {info.python_version}")
    print(f"CPU count: {info.cpu_count}")
    if info.torch_version:
        print(f"Torch: {info.torch_version}")
        print(f"CUDA available: {info.cuda_available}")
        print(f"MPS available: {info.mps_available}")
    else:
        print("Torch: not available")
    print()


def _print_character_lists(
    prompt_map: Dict[str, Path],
    voice_map: Dict[str, Path],
    voice_empty: List[str],
    prompt_empty: List[str],
    en_to_zh: Dict[str, str],
    show_voice_only: bool,
) -> None:
    prompt_names = sorted(prompt_map.keys())
    voice_names = sorted(voice_map.keys())
    prompt_set = set(prompt_names)
    voice_set = set(voice_names)

    prompt_with_voice = sorted(prompt_set & voice_set)
    prompt_missing_voice = sorted(prompt_set - voice_set)
    voice_only = sorted(voice_set - prompt_set)

    print("== Prompt/Voice Coverage ==")
    print(f"Prompts: {len(prompt_names)}")
    print(f"Voice dirs: {len(voice_names)} (empty: {len(voice_empty)})")
    print()

    def _format_name(name: str) -> str:
        zh = en_to_zh.get(name)
        if zh:
            return f"{name} ({zh})"
        return name

    print("Prompts with voice:")
    if prompt_with_voice:
        for name in prompt_with_voice:
            print(f"  - {_format_name(name)}")
    else:
        print("  - none")
    print()

    print("Prompts missing voice:")
    if prompt_missing_voice:
        for name in prompt_missing_voice:
            print(f"  - {_format_name(name)}")
    else:
        print("  - none")
    print()

    print("Empty voice folders:")
    if voice_empty:
        for name in sorted(voice_empty):
            print(f"  - {_format_name(name)}")
    else:
        print("  - none")
    print()

    print("Empty prompt files:")
    if prompt_empty:
        for name in sorted(prompt_empty):
            print(f"  - {_format_name(name)}")
    else:
        print("  - none")
    print()

    if show_voice_only:
        print("Voice-only folders:")
        if voice_only:
            for name in voice_only:
                print(f"  - {_format_name(name)}")
        else:
            print("  - none")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check device info and local prompt/voice data status.")
    parser.add_argument("--prompt-dir", default="result-prompts", help="Prompt directory path")
    parser.add_argument("--voice-dir", default="result-voices", help="Voice directory path")
    parser.add_argument("--character-map", default="umamusume_characters.json", help="Character map JSON path")
    parser.add_argument("--show-voice-only", action="store_true", help="List all voice-only folders")
    args = parser.parse_args()

    prompt_dir = Path(args.prompt_dir)
    voice_dir = Path(args.voice_dir)
    character_map = Path(args.character_map)

    device_info = _get_device_info()
    _print_device_info(device_info)

    prompt_files = _list_prompt_files(prompt_dir)
    voice_dirs = _list_voice_dirs(voice_dir)

    prompt_map = _build_name_map(prompt_files)
    voice_map = _build_name_map(voice_dirs)
    voice_empty = [
        _normalize_name(path.name)
        for path in voice_dirs
        if not _dir_has_audio_files(path)
    ]
    prompt_empty = [
        _normalize_name(path.stem)
        for path in prompt_files
        if path.stat().st_size == 0
    ]

    en_to_zh = _load_character_map(character_map)
    _print_character_lists(prompt_map, voice_map, voice_empty, prompt_empty, en_to_zh, args.show_voice_only)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
