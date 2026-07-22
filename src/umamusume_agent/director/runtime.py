"""LLM-backed director planning with schema validation and bounded fallback."""

from __future__ import annotations

import logging
from typing import Any

from ..dialogue.protocol import load_json_object_from_text
from ..dialogue.runtime import CharacterRuntime
from .models import DirectorPlan, DirectorSpeakerPlan


logger = logging.getLogger(__name__)


DIRECTOR_REPAIR_PROMPT = """上一条导演输出无法通过 JSON Schema 校验。
请只重新输出一个合法 JSON object，不要写解释、Markdown 或角色台词。"""


class DirectorRuntime:
    def __init__(
        self,
        *,
        json_runtime: CharacterRuntime,
        settings: Any,
        max_speakers: int,
    ):
        self.json_runtime = json_runtime
        self.settings = settings
        self.max_speakers = max(1, max_speakers)

    @staticmethod
    def _parse_plan(raw: str) -> DirectorPlan:
        payload = load_json_object_from_text(raw)
        normalized = dict(payload)
        if not isinstance(normalized.get("scene_patch"), dict):
            normalized["scene_patch"] = {}
        if not isinstance(normalized.get("narration"), str):
            normalized["narration"] = ""
        if not isinstance(normalized.get("speakers"), list):
            normalized["speakers"] = []
        return DirectorPlan.model_validate(normalized)

    def _sanitize_plan(
        self,
        plan: DirectorPlan,
        *,
        allowed_actor_ids: set[str],
        allowed_target_ids: set[str],
    ) -> DirectorPlan:
        speakers: list[DirectorSpeakerPlan] = []
        seen: set[str] = set()
        for item in plan.speakers:
            actor_id = item.actor_id.strip()
            intent = item.intent.strip()
            if (
                not actor_id
                or actor_id not in allowed_actor_ids
                or actor_id in seen
                or not intent
            ):
                continue
            targets = [
                target
                for target in item.target_actor_ids
                if target in allowed_target_ids and target != actor_id
            ]
            speakers.append(
                DirectorSpeakerPlan(
                    actor_id=actor_id,
                    target_actor_ids=list(dict.fromkeys(targets)),
                    intent=intent,
                )
            )
            seen.add(actor_id)
            if len(speakers) >= self.max_speakers:
                break
        return plan.model_copy(update={"speakers": speakers}, deep=True)

    @staticmethod
    def _fallback_plan(
        fallback_actor_ids: list[str],
        allowed_actor_ids: set[str],
    ) -> DirectorPlan:
        actor_id = next(
            (item for item in fallback_actor_ids if item in allowed_actor_ids),
            next(iter(sorted(allowed_actor_ids)), ""),
        )
        if not actor_id:
            return DirectorPlan()
        return DirectorPlan(
            speakers=[
                DirectorSpeakerPlan(
                    actor_id=actor_id,
                    target_actor_ids=["player"],
                    intent="结合最新公开事件，以符合角色性格的方式自然回应。",
                )
            ]
        )

    async def generate_plan(
        self,
        messages: list[dict[str, Any]],
        *,
        allowed_actor_ids: set[str],
        allowed_target_ids: set[str],
        fallback_actor_ids: list[str],
    ) -> DirectorPlan:
        attempts = max(0, int(self.settings.DIRECTOR_JSON_REPAIR_ATTEMPTS)) + 1
        request_messages = list(messages)
        for attempt in range(attempts):
            raw = await self.json_runtime.create_json_completion(
                request_messages,
                temperature=float(self.settings.DIRECTOR_LLM_TEMPERATURE),
                max_tokens=max(64, int(self.settings.DIRECTOR_LLM_MAX_TOKENS)),
            )
            try:
                plan = self._parse_plan(raw)
                sanitized = self._sanitize_plan(
                    plan,
                    allowed_actor_ids=allowed_actor_ids,
                    allowed_target_ids=allowed_target_ids,
                )
                if not sanitized.speakers:
                    fallback = self._fallback_plan(
                        fallback_actor_ids,
                        allowed_actor_ids,
                    )
                    sanitized = sanitized.model_copy(
                        update={"speakers": fallback.speakers},
                        deep=True,
                    )
                return sanitized
            except Exception as exc:
                logger.warning(
                    "Failed to parse director plan, attempt=%s: %s",
                    attempt + 1,
                    exc,
                )
                request_messages = [
                    *messages,
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": DIRECTOR_REPAIR_PROMPT},
                ]

        logger.warning("Director plan fallback activated after parse failures")
        return self._fallback_plan(fallback_actor_ids, allowed_actor_ids)
