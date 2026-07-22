"""Cache-friendly prompt threads and scene context projections."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ..character import CharacterConfig
from ..dialogue.models import ActorRef, CharacterReplyContext
from ..dialogue.protocol import StructuredReply
from .models import ActorInstance, DirectorPlan, SceneEvent, SceneState, SceneTemplate
from .timeline import SceneTimeline


DIRECTOR_SYSTEM_PROMPT = """你是多人角色扮演场景的导演。
你只负责更新场景并决定哪些在场角色应该依次回应；绝对不要替角色写动作或台词。
每次收到新事件后，至少安排一位最相关的角色回应。通常只安排一位角色；只有接话、争论或多人互动确实能让情节更自然时才安排第二位。不要为了用满名额而让所有角色轮流说话。
剧情大纲如果存在，只是发展方向而不是必须逐项执行的固定剧本；始终以已经发生的事件和角色自然反应为准。
结合最新事件、当前场景和角色关系，输出一个 JSON object，格式必须是：
{
  "schema_version": 1,
  "scene_patch": {
    "location": null,
    "sub_location": null,
    "time": null,
    "weather": null,
    "lighting": null,
    "atmosphere": null,
    "ambient_sound": null,
    "props": null
  },
  "narration": "可选的简短环境或过渡旁白",
  "speakers": [
    {
      "actor_id": "必须来自给定在场角色",
      "target_actor_ids": ["回应对象"],
      "intent": "只描述本轮表演意图，不写具体动作和台词"
    }
  ]
}
每轮最多安排给定上限数量的角色，每个角色最多一次。没有变化的 scene_patch 字段使用 null。"""


CHARACTER_SCENE_INSTRUCTION = """你正在多人共享场景中扮演当前角色。
事件按真实发生顺序提供。你能听到其他角色和训练员的公开发言；不要把其他角色的发言误认为训练员发言。
导演提示只规定本轮意图，不替你写台词。你的性格、措辞和最终行为仍必须遵循角色设定。
只回应到当前时刻，不要替其他角色行动或发言，不要自行开始无限连续对话。"""

CHARACTER_SCENE_RESPONSE_FORMAT = """【多人场景 JSON 回复格式硬性规范】
你必须只输出一个合法 JSON object，不要输出 Markdown、解释或其他文字。
格式必须是：
{
  "action": "当前角色自己的动作、神态或心理描写；没有则写无",
  "dialogue": "当前角色对目标对象自然说出的台词"
}
action 和 dialogue 都必须是 string，dialogue 不能为空。不要替训练员或其他角色说话、行动或思考。"""


@dataclass
class PromptThread:
    """An append-only message thread suitable for provider prefix caching."""

    messages: list[dict[str, Any]]
    last_seen_sequence: int = 0
    reply_count: int = 0

    def append(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(message) for message in self.messages]


def _supports_prefix_cache(settings: Any) -> bool:
    base_url = str(settings.ROLEPLAY_LLM_MODEL_BASE_URL or "").lower()
    model = str(settings.ROLEPLAY_LLM_MODEL_NAME or "").lower()
    return (
        "dashscope.aliyuncs.com" in base_url
        or "bailian" in base_url
        or model.startswith("qwen")
    )


def _system_message(content: str, settings: Any) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "system", "content": content}
    if (
        settings.DIALOGUE_PREFIX_CACHE_ENABLED
        and _supports_prefix_cache(settings)
        and len(content) >= settings.DIALOGUE_PREFIX_CACHE_MIN_CHARS
    ):
        message["cache_control"] = {"type": "ephemeral"}
    return message


def scene_state_payload(state: SceneState) -> dict[str, Any]:
    return state.model_dump(exclude_none=True)


def render_scene_event(event: SceneEvent) -> str:
    actor_name = event.actor.display_name if event.actor else "环境"
    if event.event_type == "character_reply":
        parts = [f"{actor_name}回应"]
        if event.action:
            parts.append(f"动作：{event.action}")
        if event.dialogue:
            parts.append(f"对白：{event.dialogue}")
        return "｜".join(parts)
    if event.event_type == "action":
        return f"{actor_name}动作：{event.content}"
    if event.event_type == "dialogue":
        return f"{actor_name}对白：{event.content}"
    if event.event_type in {"narration", "scene_event", "scene_change"}:
        return f"环境：{event.content}"
    if event.event_type == "actor_enter":
        return f"{actor_name}进入场景：{event.content}"
    if event.event_type == "actor_leave":
        return f"{actor_name}离开场景：{event.content}"
    return f"{actor_name}：{event.content}"


def _event_packet(events: list[SceneEvent]) -> list[dict[str, Any]]:
    return [
        {
            "sequence": event.sequence,
            "event_type": event.event_type,
            "actor_id": event.actor.actor_id if event.actor else None,
            "text": render_scene_event(event),
            "target_actor_ids": event.target_actor_ids,
        }
        for event in events
    ]


def _cast_payload(participants: list[ActorInstance]) -> list[dict[str, Any]]:
    return [
        {
            "actor_id": item.actor.actor_id,
            "display_name": item.actor.display_name,
            "actor_type": item.actor.actor_type,
            "position": item.position,
        }
        for item in participants
        if item.present
    ]


class DirectorContextBuilder:
    def __init__(self, *, settings: Any, max_speakers: int = 2):
        self.settings = settings
        self.max_speakers = max(1, max_speakers)

    def create_thread(
        self,
        *,
        template: SceneTemplate,
        participants: list[ActorInstance],
        story_outline: str = "",
    ) -> PromptThread:
        static_context = {
            "scene_template": template.model_dump(mode="json"),
            "cast": _cast_payload(participants),
            "schedulable_actor_ids": [
                item.actor.actor_id
                for item in participants
                if item.present and item.actor.actor_type in {"umamusume", "npc"}
            ],
            "max_speakers_per_turn": self.max_speakers,
            "optional_story_outline": story_outline.strip() or None,
        }
        content = (
            f"{DIRECTOR_SYSTEM_PROMPT}\n\n"
            "以下内容在本场景中固定不变：\n"
            f"{json.dumps(static_context, ensure_ascii=False, separators=(',', ':'))}"
        )
        return PromptThread(messages=[_system_message(content, self.settings)])

    def append_turn(
        self,
        thread: PromptThread,
        *,
        timeline: SceneTimeline,
    ) -> None:
        interval = max(
            0,
            int(self.settings.DIRECTOR_ROLE_REINJECTION_INTERVAL_REPLIES),
        )
        if thread.reply_count > 0 and interval > 0 and thread.reply_count % interval == 0:
            thread.append("system", DIRECTOR_SYSTEM_PROMPT)
        events = timeline.since(thread.last_seen_sequence)
        packet = {
            "current_scene_state": scene_state_payload(timeline.state),
            "new_events": _event_packet(events),
            "instruction": "为当前最新事件制定一次导演计划。",
        }
        thread.append(
            "user",
            json.dumps(packet, ensure_ascii=False, separators=(",", ":")),
        )
        thread.last_seen_sequence = timeline.latest_sequence

    @staticmethod
    def record_plan(thread: PromptThread, plan: DirectorPlan) -> None:
        thread.append(
            "assistant",
            plan.model_dump_json(exclude_none=True),
        )
        thread.reply_count += 1


class CharacterSceneContextBuilder:
    def __init__(self, *, settings: Any):
        self.settings = settings

    def create_thread(
        self,
        *,
        character: CharacterConfig,
        template: SceneTemplate,
        participants: list[ActorInstance],
    ) -> PromptThread:
        static_scene = {
            "scene_template": template.model_dump(mode="json"),
            "cast": _cast_payload(participants),
            "current_actor_id": character.id,
        }
        content = (
            f"{character.get_system_prompt()}\n\n"
            f"{CHARACTER_SCENE_INSTRUCTION}\n\n"
            f"{CHARACTER_SCENE_RESPONSE_FORMAT}\n\n"
            "以下内容在本场景中固定不变：\n"
            f"{json.dumps(static_scene, ensure_ascii=False, separators=(',', ':'))}"
        )
        return PromptThread(messages=[_system_message(content, self.settings)])

    def build_reply_context(
        self,
        thread: PromptThread,
        *,
        actor: ActorRef,
        timeline: SceneTimeline,
        intent: str,
        target_actor_ids: list[str],
    ) -> CharacterReplyContext:
        interval = max(
            0,
            int(self.settings.DIRECTOR_ROLE_REINJECTION_INTERVAL_REPLIES),
        )
        if thread.reply_count > 0 and interval > 0 and thread.reply_count % interval == 0:
            thread.append(
                "system",
                f"{CHARACTER_SCENE_INSTRUCTION}\n\n{CHARACTER_SCENE_RESPONSE_FORMAT}",
            )
        events = timeline.since(
            thread.last_seen_sequence,
            actor_id=actor.actor_id,
        )
        packet = {
            "current_scene_state": scene_state_payload(timeline.state),
            "new_visible_events": _event_packet(events),
            "director_instruction_for_this_reply": {
                "intent": intent,
                "target_actor_ids": target_actor_ids,
            },
        }
        thread.append(
            "user",
            json.dumps(packet, ensure_ascii=False, separators=(",", ":")),
        )
        thread.last_seen_sequence = timeline.latest_sequence
        return CharacterReplyContext(messages=thread.snapshot())

    @staticmethod
    def record_reply(thread: PromptThread, reply: StructuredReply) -> None:
        thread.append(
            "assistant",
            json.dumps(
                {"action": reply.action, "dialogue": reply.dialogue},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        thread.reply_count += 1
