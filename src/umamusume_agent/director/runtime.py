"""LLM-backed director planning with schema validation and bounded fallback."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..dialogue.protocol import load_json_object_from_text
from ..dialogue.runtime import CharacterRuntime
from .models import (
    DirectorApproachAction,
    DirectorFaceAction,
    DirectorFaceActorAction,
    DirectorFollowAction,
    DirectorMoveAction,
    DirectorPlan,
    DirectorSpeakerPlan,
    DirectorStageAction,
    DirectorStopFollowAction,
    DirectorStopAction,
)


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
        max_stage_actions: int = 4,
        thinking_mode: str = "auto",
    ):
        self.json_runtime = json_runtime
        self.settings = settings
        self.max_speakers = max(1, max_speakers)
        self.max_stage_actions = max(0, max_stage_actions)
        self.thinking_mode = thinking_mode.strip().lower()

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
        if not isinstance(normalized.get("stage_actions"), list):
            normalized["stage_actions"] = []
        return DirectorPlan.model_validate(normalized)

    def _sanitize_plan(
        self,
        plan: DirectorPlan,
        *,
        allowed_actor_ids: set[str],
        allowed_target_ids: set[str],
        allowed_anchor_ids: set[str] | None,
        allowed_stage_actor_ids: set[str] | None,
        allowed_stage_target_actor_ids: set[str] | None,
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
        stage_actions: list[DirectorStageAction] = []
        if (
            allowed_anchor_ids is not None
            and allowed_stage_actor_ids is not None
            and self.max_stage_actions > 0
        ):
            for action in plan.stage_actions:
                actor_id = action.actor_id.strip()
                if (
                    not actor_id
                    or actor_id not in allowed_stage_actor_ids
                ):
                    continue
                if isinstance(action, DirectorMoveAction):
                    anchor_id = action.anchor_id.strip()
                    if not anchor_id or anchor_id not in allowed_anchor_ids:
                        continue
                    stage_actions.append(
                        DirectorMoveAction(
                            type="actor.move_to",
                            actor_id=actor_id,
                            anchor_id=anchor_id,
                            slot_id=(action.slot_id or "").strip() or None,
                        )
                    )
                elif isinstance(action, DirectorFaceAction):
                    stage_actions.append(
                        DirectorFaceAction(
                            type="actor.face",
                            actor_id=actor_id,
                            facing=action.facing,
                        )
                    )
                elif isinstance(action, DirectorFaceActorAction):
                    target_actor_id = action.target_actor_id.strip()
                    if (
                        allowed_stage_target_actor_ids is None
                        or target_actor_id not in allowed_stage_target_actor_ids
                        or target_actor_id == actor_id
                    ):
                        continue
                    stage_actions.append(
                        DirectorFaceActorAction(
                            type="actor.face_actor",
                            actor_id=actor_id,
                            target_actor_id=target_actor_id,
                        )
                    )
                elif isinstance(action, DirectorApproachAction):
                    target_actor_id = action.target_actor_id.strip()
                    if (
                        allowed_stage_target_actor_ids is None
                        or target_actor_id not in allowed_stage_target_actor_ids
                        or target_actor_id == actor_id
                    ):
                        continue
                    stage_actions.append(
                        DirectorApproachAction(
                            type="actor.approach",
                            actor_id=actor_id,
                            target_actor_id=target_actor_id,
                            distance_tiles=action.distance_tiles,
                        )
                    )
                elif isinstance(action, DirectorFollowAction):
                    target_actor_id = action.target_actor_id.strip()
                    if (
                        allowed_stage_target_actor_ids is None
                        or target_actor_id not in allowed_stage_target_actor_ids
                        or target_actor_id == actor_id
                    ):
                        continue
                    stage_actions.append(
                        DirectorFollowAction(
                            type="actor.follow",
                            actor_id=actor_id,
                            target_actor_id=target_actor_id,
                            distance_tiles=action.distance_tiles,
                        )
                    )
                elif isinstance(action, DirectorStopFollowAction):
                    stage_actions.append(
                        DirectorStopFollowAction(
                            type="actor.stop_follow",
                            actor_id=actor_id,
                        )
                    )
                elif isinstance(action, DirectorStopAction):
                    stage_actions.append(
                        DirectorStopAction(type="actor.stop", actor_id=actor_id)
                    )
                if len(stage_actions) >= self.max_stage_actions:
                    break
        return plan.model_copy(
            update={"speakers": speakers, "stage_actions": stage_actions},
            deep=True,
        )

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
        allowed_anchor_ids: set[str] | None = None,
        allowed_stage_actor_ids: set[str] | None = None,
        allowed_stage_target_actor_ids: set[str] | None = None,
        require_speaker: bool = True,
    ) -> DirectorPlan:
        attempts = max(0, int(self.settings.DIRECTOR_JSON_REPAIR_ATTEMPTS)) + 1
        request_messages = list(messages)
        prompt_only = False
        retry_reason = "initial"
        for attempt in range(attempts):
            completion = await self.json_runtime.create_json_completion_result(
                request_messages,
                temperature=float(self.settings.DIRECTOR_LLM_TEMPERATURE),
                max_tokens=max(64, int(self.settings.DIRECTOR_LLM_MAX_TOKENS)),
                force_prompt_only=prompt_only,
                thinking=self._thinking_enabled(),
                attempt=attempt + 1,
                retry_reason=retry_reason,
            )
            if not completion.can_parse:
                logger.warning(
                    "Director plan discarded because finish_reason=%s after "
                    "%s length retries; partial output will not enter repair context",
                    completion.finish_reason,
                    completion.length_retries,
                )
                return (
                    self._fallback_plan(fallback_actor_ids, allowed_actor_ids)
                    if require_speaker
                    else DirectorPlan()
                )
            raw = completion.content
            try:
                plan = self._parse_plan(raw)
                sanitized = self._sanitize_plan(
                    plan,
                    allowed_actor_ids=allowed_actor_ids,
                    allowed_target_ids=allowed_target_ids,
                    allowed_anchor_ids=allowed_anchor_ids,
                    allowed_stage_actor_ids=allowed_stage_actor_ids,
                    allowed_stage_target_actor_ids=allowed_stage_target_actor_ids,
                )
                if require_speaker and not sanitized.speakers:
                    fallback = self._fallback_plan(
                        fallback_actor_ids,
                        allowed_actor_ids,
                    )
                    sanitized = sanitized.model_copy(
                        update={"speakers": fallback.speakers},
                        deep=True,
                    )
                # Do not replay an unsanitized plan or unrecognized fields.
                # Whitespace/key order may be kept only if accepted semantics
                # and the complete JSON payload were left unchanged.
                try:
                    original = DirectorPlan.model_validate_json(raw)
                    if (
                        json.loads(raw) == original.model_dump(mode="json", exclude_unset=True)
                        and original.model_dump() == sanitized.model_dump()
                    ):
                        sanitized.model_content = raw
                except (ValueError, TypeError):
                    pass
                return sanitized
            except Exception as exc:
                logger.warning(
                    "Failed to parse director plan, attempt=%s: %s",
                    attempt + 1,
                    exc,
                )
                empty_output = not raw.strip()
                prompt_only = not empty_output
                retry_reason = "empty_output" if empty_output else "json_repair"
                request_messages = list(messages) if empty_output else [
                    *messages,
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": DIRECTOR_REPAIR_PROMPT},
                ]

        logger.warning("Director plan fallback activated after parse failures")
        return (
            self._fallback_plan(fallback_actor_ids, allowed_actor_ids)
            if require_speaker
            else DirectorPlan()
        )

    def _thinking_enabled(self) -> bool | None:
        if self.thinking_mode == "enabled":
            return True
        if self.thinking_mode == "disabled":
            return False
        return None
