"""
Character Builder - local build using prompt/voice results.
"""

from __future__ import annotations

import json
import logging
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from ..character.model import CharacterConfig, Metadata, Personality, VoiceConfig
from .quality_filter import AudioFilterConfig, AudioQuality, analyze_audio, is_valid_japanese_text, score_audio

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioSelection:
    audio_path: Path
    text_jp: Optional[Path]
    text_zh: Optional[Path]
    quality: AudioQuality
    size_bytes: int
    text_score: float


def _normalize_name(value: str) -> str:
    cleaned = value.strip().replace("_", " ").replace("\u3000", " ")
    cleaned = " ".join(cleaned.split())
    return cleaned.lower()


def _slugify(value: str) -> str:
    return _normalize_name(value).replace(" ", "_")


def _load_character_map(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return { _normalize_name(en): zh for zh, en in data.items() if isinstance(zh, str) and isinstance(en, str) }


def _iter_audio_files(voice_dir: Path) -> List[Path]:
    return sorted(
        [p for p in voice_dir.iterdir() if p.is_file() and p.suffix.lower() in {".mp3", ".wav"}],
        key=lambda p: p.stat().st_size,
        reverse=True,
    )


def _find_text_files(audio_path: Path) -> Tuple[Optional[Path], Optional[Path]]:
    stem = audio_path.stem
    jp = audio_path.with_name(f"{stem}_jp.txt")
    zh = audio_path.with_name(f"{stem}_zh.txt")
    return (jp if jp.exists() else None, zh if zh.exists() else None)


def _load_text(text_path: Path) -> str:
    return text_path.read_text(encoding="utf-8").strip()

def _evaluate_candidate(
    audio_path_str: str,
    text_jp_str: Optional[str],
    text_zh_str: Optional[str],
) -> Tuple[str, Optional[str], Optional[str], AudioQuality, int, float, List[Tuple[str, str]]]:
    audio_path = Path(audio_path_str)
    issues: List[Tuple[str, str]] = []
    text_score = 0.0

    penalties = {
        "humming_or_singing": 6.0,
        "repetitive_kana": 4.0,
        "too_many_ellipsis": 4.0,
        "too_many_symbols": 3.0,
        "too_short": 2.0,
        "too_long": 2.0,
        "no_kanji": 2.0,
    }

    if text_jp_str:
        try:
            jp_text = Path(text_jp_str).read_text(encoding="utf-8").strip()
        except Exception:
            jp_text = ""
            issues.append(("jp", "read_error"))
        if jp_text:
            valid, reason = is_valid_japanese_text(jp_text)
            if valid:
                text_score += 0.6
            else:
                text_score -= penalties.get(reason, 1.5)
                issues.append(("jp", reason))
        else:
            text_score -= 0.5
            issues.append(("jp", "empty"))

    if text_zh_str:
        try:
            zh_text = Path(text_zh_str).read_text(encoding="utf-8").strip()
        except Exception:
            zh_text = ""
            issues.append(("zh", "read_error"))
        if zh_text:
            text_score += 0.2
        else:
            text_score -= 0.5
            issues.append(("zh", "empty"))

    quality, reason = analyze_audio(audio_path_str)
    if reason:
        issues.append(("audio", reason))

    size_bytes = audio_path.stat().st_size
    return (audio_path_str, text_jp_str, text_zh_str, quality, size_bytes, text_score, issues)


def _iter_candidates(
    audio_paths: Iterable[Path],
) -> Iterable[Tuple[str, Optional[str], Optional[str]]]:
    for audio_path in audio_paths:
        text_jp, text_zh = _find_text_files(audio_path)
        yield (
            audio_path.as_posix(),
            text_jp.as_posix() if text_jp else None,
            text_zh.as_posix() if text_zh else None,
        )


def _select_best_audio(
    voice_dir: Path,
    max_candidates: Optional[int] = None,
    workers: int = 1,
) -> Optional[AudioSelection]:
    audio_files = _iter_audio_files(voice_dir)
    if not audio_files:
        return None

    config = AudioFilterConfig()
    candidates = audio_files if max_candidates is None else audio_files[:max_candidates]
    selections: List[AudioSelection] = []
    worker_count = max(1, int(workers or 1))
    candidate_args = list(_iter_candidates(candidates))

    def _handle_result(
        result: Tuple[str, Optional[str], Optional[str], AudioQuality, int, float, List[Tuple[str, str]]],
    ) -> None:
        audio_path_str, text_jp_str, text_zh_str, quality, size_bytes, text_score, issues = result
        display_path = audio_path_str
        for issue_type, reason in issues:
            if issue_type == "jp":
                logger.info('Flag "%s": jp text issue (%s)', display_path, reason)
            elif issue_type == "zh":
                logger.info('Flag "%s": zh text issue (%s)', display_path, reason)
            elif issue_type == "audio":
                logger.info('Flag "%s": audio issue (%s)', display_path, reason)
            else:
                logger.info('Flag "%s": issue (%s)', display_path, reason)

        selections.append(
            AudioSelection(
                audio_path=Path(audio_path_str),
                text_jp=Path(text_jp_str) if text_jp_str else None,
                text_zh=Path(text_zh_str) if text_zh_str else None,
                quality=quality,
                size_bytes=size_bytes,
                text_score=text_score,
            )
        )

    if worker_count > 1 and len(candidate_args) > 1:
        with ProcessPoolExecutor(max_workers=worker_count) as executor:
            future_map = {
                executor.submit(_evaluate_candidate, *args): args[0] for args in candidate_args
            }
            for future in as_completed(future_map):
                try:
                    _handle_result(future.result())
                except Exception as exc:
                    logger.warning('Flag "%s": analysis exception (%s)', future_map[future], exc)
    else:
        for args in candidate_args:
            _handle_result(_evaluate_candidate(*args))

    def _score(selection: AudioSelection) -> float:
        size_bonus = min(selection.size_bytes / 1_000_000.0, 5.0) * 0.1
        return score_audio(selection.quality, config) + selection.text_score + size_bonus

    selections.sort(key=_score, reverse=True)
    return selections[0] if selections else None


def _load_prompt(prompt_path: Path) -> str:
    return prompt_path.read_text(encoding="utf-8").strip()


def _copy_prompt(prompt_path: Path, character_dir: Path) -> Path:
    target = character_dir / "prompt.md"
    shutil.copy2(prompt_path, target)
    return target


def _copy_audio(selection: AudioSelection, character_dir: Path) -> Tuple[Path, Optional[Path], Optional[Path]]:
    audio_suffix = selection.audio_path.suffix.lower()
    audio_target = character_dir / f"reference{audio_suffix}"
    shutil.copy2(selection.audio_path, audio_target)

    text_jp_target = None
    if selection.text_jp:
        text_jp_target = character_dir / "reference_jp.txt"
        shutil.copy2(selection.text_jp, text_jp_target)

    text_zh_target = None
    if selection.text_zh:
        text_zh_target = character_dir / "reference_zh.txt"
        shutil.copy2(selection.text_zh, text_zh_target)

    return audio_target, text_jp_target, text_zh_target


def _build_character_config(
    character_dir: Path,
    prompt_text: str,
    name_info: Dict[str, str],
    audio_target: Path,
    text_jp_target: Optional[Path],
    text_zh_target: Optional[Path],
    quality: AudioQuality,
) -> CharacterConfig:
    ref_text_path = None
    if text_jp_target:
        ref_text_path = text_jp_target.name
    elif text_zh_target:
        ref_text_path = text_zh_target.name

    sample_rate = quality.sample_rate or 22050

    return CharacterConfig(
        id=f"uma_{_slugify(name_info['name_en'])}",
        name_zh=name_info.get("name_zh") or name_info["name_en"],
        name_en=name_info["name_en"],
        name_jp=name_info.get("name_jp") or name_info["name_en"],
        version="1.0",
        created_at=datetime.now(),
        last_updated=datetime.now(),
        system_prompt=prompt_text,
        personality=Personality(),
        voice_config=VoiceConfig(
            model="IndexTTS2",
            ref_audio_path=audio_target.name,
            ref_text_path=ref_text_path,
            language_code="ja-JP",
            sample_rate=sample_rate,
        ),
        metadata=Metadata(
            cv=name_info.get("cv"),
            birthday=name_info.get("birthday"),
            height=name_info.get("height"),
            source_wiki=name_info.get("source_wiki"),
            retrieved_at=datetime.now(),
        ),
        character_dir=character_dir,
    )


def _save_character_config(character: CharacterConfig) -> None:
    config_path = character.character_dir / "config.json"
    with config_path.open("w", encoding="utf-8") as handle:
        json.dump(
            character.model_dump(mode="json", exclude={"character_dir"}),
            handle,
            ensure_ascii=False,
            indent=2,
        )


def _resolve_name_info(prompt_en: str, en_to_zh: Dict[str, str]) -> Dict[str, str]:
    normalized = _normalize_name(prompt_en)
    name_zh = en_to_zh.get(normalized, prompt_en)
    return {
        "name_en": prompt_en,
        "name_zh": name_zh,
        "name_jp": prompt_en,
        "source_wiki": f"https://wiki.biligame.com/umamusume/{name_zh}",
    }


def build_characters(
    prompt_dir: Path,
    voice_dir: Path,
    output_dir: Path,
    character_map_path: Path,
    workers: int = 1,
    dry_run: bool = False,
) -> List[str]:
    prompt_files = sorted([p for p in prompt_dir.iterdir() if p.is_file() and p.suffix.lower() in {".md", ".txt"}])
    voice_dirs = { _normalize_name(p.name): p for p in voice_dir.iterdir() if p.is_dir() }

    en_to_zh = _load_character_map(character_map_path)

    built: List[str] = []

    for prompt_path in prompt_files:
        prompt_en = prompt_path.stem.replace("_", " ").strip()
        normalized = _normalize_name(prompt_en)
        voice_path = voice_dirs.get(normalized)

        if not voice_path:
            logger.warning("Skip %s: voice directory not found", prompt_en)
            continue

        prompt_text = _load_prompt(prompt_path)
        if not prompt_text:
            logger.warning("Skip %s: prompt file is empty", prompt_en)
            continue

        selection = _select_best_audio(voice_path, workers=workers)
        if not selection:
            logger.warning("Skip %s: no valid audio after filtering in %s", prompt_en, voice_path)
            continue

        name_info = _resolve_name_info(prompt_en, en_to_zh)
        character_slug = _slugify(name_info["name_en"])
        character_dir = output_dir / character_slug

        logger.info("Building character %s -> %s", prompt_en, character_dir)

        if dry_run:
            built.append(character_slug)
            continue

        character_dir.mkdir(parents=True, exist_ok=True)

        _copy_prompt(prompt_path, character_dir)
        audio_target, text_jp_target, text_zh_target = _copy_audio(selection, character_dir)

        character_config = _build_character_config(
            character_dir=character_dir,
            prompt_text=prompt_text,
            name_info=name_info,
            audio_target=audio_target,
            text_jp_target=text_jp_target,
            text_zh_target=text_zh_target,
            quality=selection.quality,
        )
        _save_character_config(character_config)

        built.append(character_slug)

    return built


class CharacterBuilder:
    """构建角色配置（基于本地 prompt + voice 结果）"""

    def __init__(
        self,
        prompt_dir: str = "result-prompts",
        voice_dir: str = "result-voices",
        characters_dir: str = "characters",
        character_map_path: str = "umamusume_characters.json",
    ) -> None:
        self.prompt_dir = Path(prompt_dir)
        self.voice_dir = Path(voice_dir)
        self.characters_dir = Path(characters_dir)
        self.character_map_path = Path(character_map_path)

    def build_all(self, dry_run: bool = False, workers: int = 1) -> List[str]:
        return build_characters(
            prompt_dir=self.prompt_dir,
            voice_dir=self.voice_dir,
            output_dir=self.characters_dir,
            character_map_path=self.character_map_path,
            workers=workers,
            dry_run=dry_run,
        )

    async def build_character(self, character_name: str) -> CharacterConfig:
        self.build_all(dry_run=False)
        config_path = self.characters_dir / _slugify(character_name) / "config.json"
        if not config_path.exists():
            raise FileNotFoundError(f"Character config not found: {config_path}")
        data = json.loads(config_path.read_text(encoding="utf-8"))
        character = CharacterConfig(**data)
        character.character_dir = config_path.parent
        return character
