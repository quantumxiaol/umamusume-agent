#!/usr/bin/env python3
"""
Build character folders from result-prompts and result-voices.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from umamusume_agent.builder import build_characters

logger = logging.getLogger("build_character")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="Build character folders from prompt/voice results.")
    parser.add_argument("--prompt-dir", default="result-prompts", help="Prompt directory path")
    parser.add_argument("--voice-dir", default="result-voices", help="Voice directory path")
    parser.add_argument("--output-dir", default="characters", help="Output characters directory path")
    parser.add_argument("--character-map", default="umamusume_characters.json", help="Character map JSON path")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers for audio analysis (1 disables)")
    parser.add_argument("--dry-run", action="store_true", help="Only show what would be built")
    args = parser.parse_args()

    prompt_dir = Path(args.prompt_dir)
    voice_dir = Path(args.voice_dir)
    output_dir = Path(args.output_dir)
    character_map_path = Path(args.character_map)

    if not prompt_dir.exists():
        logger.error("Prompt directory not found: %s", prompt_dir)
        return 1

    if not voice_dir.exists():
        logger.error("Voice directory not found: %s", voice_dir)
        return 1

    built = build_characters(
        prompt_dir=prompt_dir,
        voice_dir=voice_dir,
        output_dir=output_dir,
        character_map_path=character_map_path,
        workers=max(1, args.workers),
        dry_run=args.dry_run,
    )

    if built:
        logger.info("Built %d characters: %s", len(built), ", ".join(built))
    else:
        logger.warning("No characters built.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
