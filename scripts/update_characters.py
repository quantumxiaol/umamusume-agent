#!/usr/bin/env python3
"""Preview/apply prompt updates or merge a tested reference-voice library.

No API calls. Existing prompts and non-voice config fields win during voice merges.
Every apply creates a backup manifest; unchanged runs do not write anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Change:
    path: Path
    before: bytes | None
    after: bytes
    reason: str


def local_file(directory: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError(f"Expected a relative file path: {name!r}")
    relative = Path(name)
    if ".." in relative.parts:
        raise ValueError(f"Parent traversal is not allowed: {name}")
    file = directory / relative
    file.resolve().relative_to(directory.resolve())
    cursor = file
    while cursor != directory:
        if cursor.is_symlink():
            raise ValueError(f"Symlink is not allowed: {cursor}")
        cursor = cursor.parent
    return file


def read_config(directory: Path) -> dict:
    data = json.loads(local_file(directory, "config.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Config must be an object: {directory}")
    for key in ("id", "name_en", "system_prompt"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"Missing/non-string {key}: {directory}")
    if not isinstance(data.get("voice_config"), dict):
        raise ValueError(f"Missing voice_config: {directory}")
    return data


def read_prompt(directory: Path) -> str:
    value = local_file(directory, "prompt.md").read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError(f"Empty prompt: {directory}")
    return value


def catalog(root: Path) -> dict[str, tuple[Path, dict]]:
    if not root.is_dir():
        raise ValueError(f"Character directory not found: {root}")
    result = {}
    ids = set()
    for directory in sorted(root.iterdir()):
        if directory.name.startswith(".") or not directory.is_dir():
            continue
        if not (directory / "config.json").exists():
            continue
        if directory.is_symlink():
            raise ValueError(f"Character directory is a symlink: {directory}")
        data = read_config(directory)
        if data["id"] in ids:
            raise ValueError(f"Duplicate character id: {data['id']}")
        ids.add(data["id"])
        result[directory.name] = (directory, data)
    return result


def normalized(name: str) -> str:
    return "_".join(name.casefold().split())


def anime_catalog(root: Path, target: dict) -> tuple[dict, list[str]]:
    """Audio-only folders may update an existing card, never create one implicitly."""
    records = catalog(root)
    excluded = []
    for directory in sorted(root.iterdir()):
        if directory.name.startswith('.') or not directory.is_dir() or directory.name in records:
            continue
        if not any((directory / name).exists() for name in ('reference.mp3', 'reference.wav')):
            continue
        if directory.is_symlink():
            raise ValueError(f"Character directory is a symlink: {directory}")
        if directory.name not in target:
            excluded.append(f"{directory.name}: audio-only source, no existing target card; not imported")
            continue
        # Exact directory match is intentional. Do not infer identities from an
        # audio filename, nor synthesize a persona for generic NPC/Trainer voices.
        records[directory.name] = (directory, target[directory.name][1])
    return records, excluded


def anime_voice_config(directory: Path, base: dict) -> dict:
    """Match anime/scripts/synthesize_script.py, not its optional config.json."""
    audio = next((name for name in ('reference.mp3', 'reference.wav')
                  if local_file(directory, name).is_file()), None)
    if audio is None:
        raise ValueError(f"Anime reference audio not found: {directory}")
    return {**base['voice_config'], 'no_voice': False, 'ref_audio_path': audio,
            'ref_text_path': 'reference_jp.txt', 'language_code': 'ja-JP'}


def select(records: dict, names: list[str] | None, all_characters: bool) -> list[str]:
    if all_characters:
        return sorted(records)
    chosen = set()
    for name in names or []:
        matches = [key for key, (_, data) in records.items() if normalized(name) in {
            normalized(str(value)) for value in
            (key, data["id"], data.get("name_en"), data.get("name_zh"), data.get("name_jp"))
            if value
        }]
        if len(matches) != 1:
            raise ValueError(f"Character must match exactly once: {name!r}; matches={matches}")
        chosen.add(matches[0])
    if not chosen:
        raise ValueError("Select --character NAME or --all")
    return sorted(chosen)


def encoded(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def append_change(changes: list[Change], path: Path, content: bytes, reason: str) -> None:
    if path.is_symlink():
        raise ValueError(f"Destination is a symlink: {path}")
    before = path.read_bytes() if path.exists() else None
    if before != content:
        changes.append(Change(path, before, content, reason))


def plan_prompts(records: dict, selected: list[str]) -> list[Change]:
    changes = []
    for name in selected:
        directory, config = records[name]
        prompt = read_prompt(directory)
        if config["system_prompt"] == prompt:
            continue
        updated = {**config, "system_prompt": prompt}
        append_change(changes, directory / "config.json", encoded(updated), "prompt.md → system_prompt")
    return changes


def disabled(voice: dict) -> bool:
    value = voice.get("no_voice", False)
    if value in (True, "true", "True"):
        return True
    if value in (False, "false", "False"):
        return False
    raise ValueError(f"Invalid no_voice value: {value!r}")


def voice_bundle(directory: Path, config: dict) -> dict[str, bytes]:
    voice = config["voice_config"]
    if disabled(voice):
        return {}
    audio = voice.get("ref_audio_path")
    transcript = voice.get("ref_text_path")
    files = {}
    for name in (audio, transcript):
        file = local_file(directory, name)
        content = file.read_bytes()
        if not content.strip() or content.startswith(b"version https://git-lfs.github.com/spec/v1"):
            raise ValueError(f"Empty file or unresolved Git LFS pointer: {file}")
        if name == transcript:
            content.decode("utf-8")
        files[name] = content
    # The paired Chinese text is useful for human inspection; unused alternate
    # audio files are deliberately not selected as active references.
    for file in sorted(directory.glob("reference_*.txt")):
        safe_file = local_file(directory, file.name)
        files[file.name] = safe_file.read_bytes()
    return files


def plan_voices(root: Path, target: dict, source: dict, selected: list[str],
                add_missing: bool = False, source_layout: str = 'config') -> tuple[list[Change], list[str]]:
    if source_layout not in ('config', 'anime'):
        raise ValueError(f"Unknown source layout: {source_layout}")
    changes, skipped = [], []
    target_ids = {data["id"]: key for key, (_, data) in target.items()}
    for name in selected:
        source_dir, source_config = source[name]
        if source_layout == 'anime':
            base = target[name][1] if name in target else source_config
            voice = anime_voice_config(source_dir, base)
            # Avoid changing a valid legacy string boolean solely for formatting.
            if not disabled(base['voice_config']):
                voice['no_voice'] = base['voice_config'].get('no_voice', False)
            source_config = {**source_config, 'voice_config': voice}
        bundle = voice_bundle(source_dir, source_config)
        if name not in target:
            if not add_missing:
                skipped.append(f"{name}: missing target (use --add-missing)")
                continue
            if source_config["id"] in target_ids:
                raise ValueError(f"Same id under another destination: {name}")
            target_dir = root / name
            if target_dir.exists():
                raise ValueError(f"Unmanaged destination already exists: {target_dir}")
            imported = {**source_config, "system_prompt": read_prompt(source_dir)}
            for file in sorted(source_dir.iterdir()):
                if file.is_symlink() or file.is_dir():
                    raise ValueError(f"Unexpected nested or linked source asset: {file}")
                if file.name.startswith("."):
                    continue
                content = encoded(imported) if file.name == "config.json" else file.read_bytes()
                append_change(changes, target_dir / file.name, content, "add character")
            continue
        target_dir, target_config = target[name]
        if (target_config["id"] != source_config["id"] or
                normalized(target_config["name_en"]) != normalized(source_config["name_en"])):
            raise ValueError(f"Identity mismatch: {name}")
        if not bundle:
            skipped.append(f"{name}: source has no active voice; destination preserved")
            continue
        for filename, content in bundle.items():
            append_change(changes, local_file(target_dir, filename), content, "tested voice/reference text")
        if source_config["voice_config"] != target_config["voice_config"]:
            updated = {**target_config, "voice_config": source_config["voice_config"]}
            append_change(changes, target_dir / "config.json", encoded(updated), "voice_config only")
    return changes, skipped


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".character-update-", delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
            os.chmod(temporary, old_mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def apply_plan(root: Path, changes: list[Change]) -> Path | None:
    if not changes:
        return None
    root = root.resolve()
    seen = set()
    for change in changes:
        relative = change.path.relative_to(root)
        local_file(root, str(relative))
        if change.path in seen:
            raise ValueError(f"Duplicate planned destination: {change.path}")
        seen.add(change.path)
        current = change.path.read_bytes() if change.path.exists() else None
        if current != change.before:
            raise ValueError(f"File changed after planning: {change.path}")
    backup_parent = root.parent / ".character-backups"
    backup_parent.mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-"), dir=backup_parent))
    manifest = {"root": str(root), "status": "prepared", "files": []}
    for change in changes:
        relative = change.path.relative_to(root)
        if change.before is not None:
            saved = backup / "before" / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(change.before)
        manifest["files"].append({"path": str(relative), "existed": change.before is not None,
                                  "before_sha256": hashlib.sha256(change.before).hexdigest() if change.before is not None else None,
                                  "after_sha256": hashlib.sha256(change.after).hexdigest(), "reason": change.reason})
    atomic_write(backup / "manifest.json", encoded(manifest))
    completed = []
    try:
        for change in changes:
            current = change.path.read_bytes() if change.path.exists() else None
            if current != change.before:
                raise ValueError(f"File changed during update: {change.path}")
            atomic_write(change.path, change.after)
            completed.append(change)
    except Exception:
        for change in reversed(completed):
            if change.before is None:
                change.path.unlink(missing_ok=True)
                parent = change.path.parent
                while parent != root and parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
            else:
                atomic_write(change.path, change.before)
        manifest["status"] = "rolled_back"
        atomic_write(backup / "manifest.json", encoded(manifest))
        raise
    manifest["status"] = "applied"
    atomic_write(backup / "manifest.json", encoded(manifest))
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prompts", "voices"))
    parser.add_argument("--characters-dir", type=Path, default=Path(__file__).resolve().parents[1] / "characters")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--character", action="append", help="directory, id, Chinese/English/Japanese name; repeatable")
    selector.add_argument("--all", action="store_true")
    parser.add_argument("--source", type=Path, help="source characters directory for voices mode")
    parser.add_argument("--source-layout", choices=('config', 'anime'), default='config',
                        help="anime: actual reference.mp3/wav + reference_jp.txt, including existing audio-only cards")
    parser.add_argument("--add-missing", action="store_true", help="also import source-only characters")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true", help="write changes, keeping backups")
    action.add_argument("--check", action="store_true", help="read-only; exit 1 if updates are needed")
    args = parser.parse_args()
    try:
        root = args.characters_dir.resolve()
        target = catalog(root)
        if args.mode == "prompts":
            if args.source or args.add_missing or args.source_layout != 'config':
                parser.error("--source/--add-missing/--source-layout only apply to voices mode")
            selected = select(target, args.character, args.all)
            changes, skipped = plan_prompts(target, selected), []
        else:
            if not args.source:
                parser.error("voices mode requires --source")
            if args.source.resolve() == root:
                raise ValueError("Source and destination must differ")
            excluded = []
            if args.source_layout == 'anime':
                source, excluded = anime_catalog(args.source.resolve(), target)
            else:
                source = catalog(args.source.resolve())
            selected = select(source, args.character, args.all)
            changes, skipped = plan_voices(root, target, source, selected, args.add_missing, args.source_layout)
            skipped = excluded + skipped
        report = {"mode": args.mode, "selected": len(selected), "changed_files": len(changes),
                  "changes": [{"path": str(c.path.relative_to(root)), "reason": c.reason} for c in changes],
                  "skipped": skipped, "applied": False}
        if args.mode == 'voices':
            report['source_layout'] = args.source_layout
        if args.apply:
            backup = apply_plan(root, changes)
            report.update(applied=bool(changes), backup=str(backup) if backup else None)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if args.check and changes else 0
    except (ValueError, OSError, UnicodeError) as error:
        parser.exit(2, f"Update aborted: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
