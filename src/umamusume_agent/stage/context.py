"""Stage-aware Director prompts kept separate from normal Director sessions."""

from __future__ import annotations

import json
from typing import Any

from ..director.context import (
    DIRECTOR_SYSTEM_PROMPT,
    DirectorContextBuilder,
    PromptThread,
    _cast_payload,
    _event_packet,
    _system_message,
    scene_state_payload,
)
from ..director.models import ActorInstance, SceneTemplate
from ..director.timeline import SceneTimeline


STAGE_DIRECTOR_INSTRUCTION = """舞台模式额外输出 stage_actions 数组，例如：
"stage_actions": [
  {"type":"actor.move_to","actor_id":"舞台角色 ID","anchor_id":"真实 Anchor ID","slot_id":"可选 Slot ID"},
  {"type":"actor.travel_to_scene","actor_id":"舞台角色 ID","target_scene_id":"travel_destinations 中的 Scene ID","target_spawn_id":"该目的地 spawn_ids 中的可选 Spawn ID"},
  {"type":"actor.approach","actor_id":"舞台角色 ID","target_actor_id":"目标舞台 Actor ID","distance_tiles":1},
  {"type":"actor.follow","actor_id":"舞台角色 ID","target_actor_id":"目标舞台 Actor ID","distance_tiles":2},
  {"type":"actor.stop_follow","actor_id":"舞台角色 ID"},
  {"type":"actor.face","actor_id":"舞台角色 ID","facing":"left"},
  {"type":"actor.face_actor","actor_id":"舞台角色 ID","target_actor_id":"目标舞台 Actor ID"},
  {"type":"actor.stop","actor_id":"舞台角色 ID"}
]
每个动作的判别字段必须叫 type，绝对不要写成 action。可用动作只有 actor.move_to、actor.travel_to_scene、actor.approach、actor.follow、actor.stop_follow、actor.face、actor.face_actor 和 actor.stop。
stage_actions 的 actor_id 使用 actor_bindings 中的 stage_actor_id。本地角色必须是 live_stage.actors 中 control_owner=agent 的真实角色；remote_actors 是已绑定但身处其他 scene 的缺席角色，没有当前场景 tile，不得伪造坐标，也不得作为当前场景的 target_actor_id。
logical_actor_bindings 显式给出对话身份与实时舞台身份的对应关系；其中 logical_actor_id=player 就是当前说话的训练员。
同一 scene 内明确要去某个地点或物体时使用 actor.move_to，只使用真实 Anchor 与 Slot，不使用坐标。同一 scene 内“过来”“到我身边”“走到训练员身旁”等相对角色移动必须使用 actor.approach，target_actor_id 使用 logical_actor_bindings 对应的当前 live_stage Actor ID；即使目标角色附近有 Anchor，也绝不能用 actor.move_to(附近 Anchor) 代替 actor.approach。
前往另一 scene 的目的地使用 actor.travel_to_scene。target_scene_id 只能来自 travel_destinations，提供 target_spawn_id 时只能来自该 scene 的 spawn_ids；不得从 Anchor 名称、描述或附近地点猜测 Scene ID，也不要用 move_to 代替跨图旅行。本地角色与 remote_actors 都可以执行旅行。
对 remote_actors 中缺席角色的“快过来”“到我身边”用 actor.approach 表达召回，“跟着我”“和我一起走”用 actor.follow 表达召回，target_actor_id 必须是当前 live_stage.actors 中的真实角色；前端会先 travel 再执行本地 approach/follow。不要为缺席角色输出 move_to、face、face_actor、stop、stop_follow 等本地动作。
“跟着我走”“跟随某人”等要求目标继续移动后仍追随的指令使用 actor.follow；同图“停止跟随”“别跟着我”使用 actor.stop_follow。actor.approach 只表示走近一次，不表示持续跟随。live_stage 中 following_actor_id 非空表示角色当前已经处于跟随模式，避免无意义地重复启用。
需要同图角色在原地转向某人、看向某人时使用 actor.face_actor，不要根据坐标自行猜测 actor.face 的绝对方向。actor.approach 到达后会自动面向目标，不需要再追加 face 动作。
角色对白不放进 stage_actions，服务会在角色回复生成后追加 dialogue.say；remote_actors 也可以正常回复。
JSON 顶层格式与普通导演计划相同，但必须额外包含 \"stage_actions\": []。"""


class StageDirectorContextBuilder(DirectorContextBuilder):
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
            f"{DIRECTOR_SYSTEM_PROMPT}\n\n{STAGE_DIRECTOR_INSTRUCTION}\n\n"
            "以下内容在本场景中固定不变：\n"
            f"{json.dumps(static_context, ensure_ascii=False, separators=(',', ':'))}"
        )
        return PromptThread(messages=[_system_message(content, self.settings)])

    def append_stage_turn(
        self,
        thread: PromptThread,
        *,
        timeline: SceneTimeline,
        stage_context: dict[str, Any],
        action_only: bool = False,
    ) -> None:
        interval = max(
            0,
            int(self.settings.DIRECTOR_ROLE_REINJECTION_INTERVAL_REPLIES),
        )
        if thread.reply_count > 0 and interval > 0 and thread.reply_count % interval == 0:
            thread.append(
                "system",
                f"{DIRECTOR_SYSTEM_PROMPT}\n\n{STAGE_DIRECTOR_INSTRUCTION}",
            )
        events = timeline.since(thread.last_seen_sequence)
        instruction = (
            "角色本轮已经完成回复。结合训练员输入、角色的语言动作与对白、"
            "当前环境和 live_stage，只制定需要执行的 stage_actions；"
            "先用 logical_actor_bindings 解析对白中的人物身份，再选择动作；"
            "speakers 必须为空，不要让角色重复回复；narration 必须为空，"
            "scene_patch 必须为空。"
            if action_only
            else "为当前最新事件制定一次导演与舞台计划。"
        )
        packet = {
            "current_scene_state": scene_state_payload(timeline.state),
            "live_stage": stage_context,
            "new_events": _event_packet(events),
            "instruction": instruction,
        }
        thread.append(
            "user",
            json.dumps(packet, ensure_ascii=False, separators=(",", ":")),
        )
        thread.last_seen_sequence = timeline.latest_sequence
