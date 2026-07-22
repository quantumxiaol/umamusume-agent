import json
import tempfile
import unittest
from pathlib import Path

from umamusume_agent.dialogue.models import ActorRef
from umamusume_agent.dialogue.protocol import StructuredReply
from umamusume_agent.director.context import (
    CharacterSceneContextBuilder,
    DirectorContextBuilder,
)
from umamusume_agent.director.models import (
    ActorInstance,
    DirectorPlan,
    SceneEvent,
    SceneState,
    SceneStatePatch,
    SceneTemplate,
)
from umamusume_agent.director.templates import SceneTemplateRepository
from umamusume_agent.director.timeline import SceneTimeline, reduce_scene_state


class _Settings:
    ROLEPLAY_LLM_MODEL_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ROLEPLAY_LLM_MODEL_NAME = "qwen-test"
    DIALOGUE_PREFIX_CACHE_ENABLED = True
    DIALOGUE_PREFIX_CACHE_MIN_CHARS = 1
    DIRECTOR_ROLE_REINJECTION_INTERVAL_REPLIES = 25


class _Character:
    id = "uma_a"

    @staticmethod
    def get_system_prompt():
        return "角色 A 的完整且固定的人格提示词"


class _ReinjectionSettings(_Settings):
    DIRECTOR_ROLE_REINJECTION_INTERVAL_REPLIES = 1


def _actor(actor_id: str, display_name: str) -> ActorRef:
    return ActorRef(
        actor_id=actor_id,
        actor_type="umamusume",
        display_name=display_name,
        character_id=actor_id,
    )


def _template() -> SceneTemplate:
    return SceneTemplate(
        template_id="test_scene",
        name="测试场景",
        initial_state=SceneState(
            location="训练场",
            time="傍晚",
            weather="晴朗",
        ),
    )


class DirectorFoundationTests(unittest.TestCase):
    def test_timeline_is_append_only_and_reduces_scene_patches(self):
        timeline = SceneTimeline(initial_state=_template().initial_state)
        first = timeline.append(
            SceneEvent(
                event_type="scene_change",
                content="夜幕降临。",
                scene_patch=SceneStatePatch(time="夜晚", lighting="场灯"),
            )
        )
        second = timeline.append(
            SceneEvent(
                event_type="scene_change",
                content="开始下雨。",
                scene_patch=SceneStatePatch(weather="小雨"),
            )
        )

        self.assertEqual((first.sequence, second.sequence), (1, 2))
        self.assertEqual(timeline.state.time, "夜晚")
        self.assertEqual(timeline.state.weather, "小雨")
        self.assertEqual(timeline.state.lighting, "场灯")
        replayed = reduce_scene_state(timeline.initial_state, timeline.events)
        self.assertEqual(replayed, timeline.state)

    def test_visibility_filters_hidden_and_actor_scoped_events(self):
        timeline = SceneTimeline(initial_state=_template().initial_state)
        timeline.append(SceneEvent(event_type="narration", content="公开事件"))
        timeline.append(
            SceneEvent(
                event_type="actor_directive",
                content="只给 A",
                visible_to=["uma_a"],
                hidden=True,
            )
        )

        self.assertEqual(
            [event.content for event in timeline.since(0, actor_id="uma_b")],
            ["公开事件"],
        )
        self.assertEqual(
            [event.content for event in timeline.since(
                0,
                actor_id="uma_a",
                include_hidden=True,
            )],
            ["公开事件", "只给 A"],
        )

    def test_director_thread_only_appends_so_previous_request_is_a_prefix(self):
        participant = ActorInstance(actor=_actor("uma_a", "角色A"))
        timeline = SceneTimeline(initial_state=_template().initial_state)
        builder = DirectorContextBuilder(settings=_Settings, max_speakers=2)
        thread = builder.create_thread(
            template=_template(),
            participants=[participant],
            story_outline="训练结束后自然聊到下一场比赛。",
        )
        self.assertEqual(
            thread.messages[0]["cache_control"],
            {"type": "ephemeral"},
        )
        self.assertIn("训练结束后自然聊到下一场比赛", thread.messages[0]["content"])

        timeline.append(
            SceneEvent(
                event_type="dialogue",
                actor=ActorRef(
                    actor_id="player",
                    actor_type="trainer",
                    display_name="训练员",
                ),
                content="今天练到这里吧。",
            )
        )
        builder.append_turn(thread, timeline=timeline)
        builder.record_plan(thread, DirectorPlan())
        previous_messages = thread.snapshot()

        timeline.append(SceneEvent(event_type="scene_event", content="天黑了。"))
        builder.append_turn(thread, timeline=timeline)

        self.assertEqual(thread.messages[:len(previous_messages)], previous_messages)

    def test_character_b_sees_a_reply_but_not_hidden_director_plan(self):
        actor_a = _actor("uma_a", "角色A")
        actor_b = _actor("uma_b", "角色B")
        participants = [ActorInstance(actor=actor_a), ActorInstance(actor=actor_b)]
        timeline = SceneTimeline(initial_state=_template().initial_state)
        timeline.append(
            SceneEvent(
                event_type="character_reply",
                actor=actor_a,
                action="抬头看向天空。",
                dialogue="已经晚上了。",
            )
        )
        timeline.append(
            SceneEvent(
                event_type="director_plan",
                content="内部计划",
                hidden=True,
            )
        )

        builder = CharacterSceneContextBuilder(settings=_Settings)
        thread = builder.create_thread(
            character=_Character(),
            template=_template(),
            participants=participants,
        )
        context = builder.build_reply_context(
            thread,
            actor=actor_b,
            timeline=timeline,
            intent="回应角色A",
            target_actor_ids=["uma_a"],
        )
        packet = json.loads(context.messages[-1]["content"])
        packet_text = json.dumps(packet, ensure_ascii=False)

        self.assertIn("已经晚上了", packet_text)
        self.assertNotIn("内部计划", packet_text)
        self.assertTrue(context.messages[0]["content"].startswith("角色 A"))

    def test_character_constraints_reinject_by_its_own_reply_count(self):
        actor = _actor("uma_a", "角色A")
        timeline = SceneTimeline(initial_state=_template().initial_state)
        timeline.append(SceneEvent(event_type="scene_event", content="第一轮"))
        builder = CharacterSceneContextBuilder(settings=_ReinjectionSettings)
        thread = builder.create_thread(
            character=_Character(),
            template=_template(),
            participants=[ActorInstance(actor=actor)],
        )
        builder.build_reply_context(
            thread,
            actor=actor,
            timeline=timeline,
            intent="观察环境",
            target_actor_ids=[],
        )
        builder.record_reply(
            thread,
            StructuredReply(action="抬头。", dialogue="天色变了。"),
        )

        timeline.append(SceneEvent(event_type="scene_event", content="第二轮"))
        builder.build_reply_context(
            thread,
            actor=actor,
            timeline=timeline,
            intent="继续回应",
            target_actor_ids=[],
        )

        self.assertEqual(thread.reply_count, 1)
        self.assertEqual(thread.messages[-2]["role"], "system")
        self.assertIn("多人场景 JSON", thread.messages[-2]["content"])
        self.assertEqual(thread.messages[-1]["role"], "user")

    def test_scene_template_repository_loads_json_templates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "scene.json"
            path.write_text(_template().model_dump_json(), encoding="utf-8")
            repository = SceneTemplateRepository(temp_dir)

            self.assertEqual(repository.get("test_scene").name, "测试场景")
            self.assertEqual(len(repository.list()), 1)


if __name__ == "__main__":
    unittest.main()
