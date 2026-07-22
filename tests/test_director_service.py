import json
import tempfile
import unittest
from pathlib import Path

from umamusume_agent.dialogue.models import ActorRef, DialogueInputEvent
from umamusume_agent.dialogue.protocol import StructuredReply
from umamusume_agent.director.context import (
    CharacterSceneContextBuilder,
    DirectorContextBuilder,
)
from umamusume_agent.director.models import (
    CustomSceneDefinition,
    DirectorPlan,
    DirectorSpeakerPlan,
    SceneState,
    SceneTemplate,
)
from umamusume_agent.director.runtime import DirectorRuntime
from umamusume_agent.director.service import DirectorService


class _Settings:
    ROLEPLAY_LLM_MODEL_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ROLEPLAY_LLM_MODEL_NAME = "qwen-test"
    DIALOGUE_PREFIX_CACHE_ENABLED = True
    DIALOGUE_PREFIX_CACHE_MIN_CHARS = 1
    DIRECTOR_ROLE_REINJECTION_INTERVAL_REPLIES = 25
    DIRECTOR_JSON_REPAIR_ATTEMPTS = 1
    DIRECTOR_LLM_TEMPERATURE = 0.2
    DIRECTOR_LLM_MAX_TOKENS = 600


class _Character:
    def __init__(self, actor_id, name):
        self.id = actor_id
        self.name_zh = name
        self.name_en = actor_id
        self.name_jp = name

    def get_system_prompt(self):
        return f"{self.name_zh} 的完整角色提示词"


class _CharacterManager:
    def __init__(self):
        self.characters = {
            "角色A": _Character("uma_a", "角色A"),
            "角色B": _Character("uma_b", "角色B"),
        }

    async def load_character(self, name):
        return self.characters[name]


class _TemplateRepository:
    @staticmethod
    def get(template_id):
        if template_id != "test_scene":
            raise FileNotFoundError(template_id)
        return SceneTemplate(
            template_id="test_scene",
            name="测试场景",
            initial_state=SceneState(location="训练场", time="傍晚"),
            opening_narration="夕阳落在跑道上。",
        )


class _FakeDirectorRuntime:
    def __init__(self):
        self.calls = []

    async def generate_plan(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return DirectorPlan(
            speakers=[
                DirectorSpeakerPlan(
                    actor_id="uma_a",
                    target_actor_ids=["player"],
                    intent="先回应训练员",
                ),
                DirectorSpeakerPlan(
                    actor_id="uma_b",
                    target_actor_ids=["uma_a", "player"],
                    intent="听完角色A后接话",
                ),
            ]
        )


class _FakeCharacterRuntime:
    def __init__(self):
        self.contexts = []

    async def generate_reply(self, context):
        self.contexts.append(context)
        index = len(self.contexts)
        return StructuredReply(
            action=f"动作{index}",
            dialogue=f"角色回复{index}",
            source_format="json_v2",
        )


class _FakeJsonRuntime:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def create_json_completion(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.outputs.pop(0)


class DirectorServiceTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _service(temp_dir, *, director_runtime=None, character_runtime=None):
        return DirectorService(
            character_manager=_CharacterManager(),
            character_runtime=character_runtime or _FakeCharacterRuntime(),
            director_runtime=director_runtime or _FakeDirectorRuntime(),
            template_repository=_TemplateRepository(),
            director_context_builder=DirectorContextBuilder(
                settings=_Settings,
                max_speakers=2,
            ),
            character_context_builder=CharacterSceneContextBuilder(
                settings=_Settings,
            ),
            history_dir=Path(temp_dir),
            max_participants=3,
        )

    async def test_sequential_characters_share_a_public_timeline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            character_runtime = _FakeCharacterRuntime()
            director_runtime = _FakeDirectorRuntime()
            service = self._service(
                temp_dir,
                director_runtime=director_runtime,
                character_runtime=character_runtime,
            )
            session = await service.create_session(
                user_uuid="00000000-0000-4000-8000-000000000001",
                template_id="test_scene",
                character_names=["角色A", "角色B"],
            )

            events = await service.execute_turn(
                session,
                [
                    DialogueInputEvent(
                        content="你们觉得今天的训练怎么样？",
                        speaker=ActorRef(
                            actor_id="player",
                            actor_type="trainer",
                            display_name="训练员",
                        ),
                        event_type="dialogue",
                        target_actor_ids=["uma_a", "uma_b"],
                    )
                ],
            )

            self.assertEqual(len(director_runtime.calls), 1)
            self.assertEqual(len(character_runtime.contexts), 2)
            second_character_packet = json.loads(
                character_runtime.contexts[1].messages[-1]["content"]
            )
            packet_text = json.dumps(second_character_packet, ensure_ascii=False)
            self.assertIn("角色回复1", packet_text)
            self.assertEqual(
                [event.event_type for event in events],
                ["dialogue", "character_reply", "character_reply"],
            )
            self.assertEqual(
                [event.actor.actor_id for event in events[-2:]],
                ["uma_a", "uma_b"],
            )
            self.assertTrue(session.history_file.exists())

    async def test_custom_scene_and_story_outline_are_static_session_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self._service(temp_dir)
            session = await service.create_session(
                user_uuid="00000000-0000-4000-8000-000000000001",
                template_id=None,
                character_names=["角色A", "角色B"],
                custom_scene=CustomSceneDefinition(
                    name="雨后的河边",
                    initial_state=SceneState(
                        location=" 河边 ",
                        time="黄昏",
                        weather="小雨刚停",
                    ),
                    opening_narration="河面还留着细碎的雨点。",
                ),
                story_outline="散步时自然聊到下一场比赛。",
            )

            self.assertTrue(session.template.template_id.startswith("custom_"))
            self.assertEqual(session.template.initial_state.location, "河边")
            self.assertEqual(session.story_outline, "散步时自然聊到下一场比赛。")
            self.assertIn(
                "散步时自然聊到下一场比赛",
                session.director_thread.messages[0]["content"],
            )
            self.assertNotIn(
                "散步时自然聊到下一场比赛",
                session.actor_threads["uma_a"].messages[0]["content"],
            )
            self.assertEqual(session.timeline.events[0].event_type, "narration")

    async def test_director_runtime_repairs_and_sanitizes_speakers(self):
        json_runtime = _FakeJsonRuntime(
            [
                "not json",
                json.dumps(
                    {
                        "scene_patch": {"time": "夜晚"},
                        "narration": "灯亮了。",
                        "speakers": [
                            {
                                "actor_id": "uma_a",
                                "target_actor_ids": ["player", "missing"],
                                "intent": "回应",
                            },
                            {
                                "actor_id": "uma_a",
                                "target_actor_ids": [],
                                "intent": "重复",
                            },
                            {
                                "actor_id": "missing",
                                "target_actor_ids": [],
                                "intent": "非法",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        runtime = DirectorRuntime(
            json_runtime=json_runtime,
            settings=_Settings,
            max_speakers=2,
        )

        plan = await runtime.generate_plan(
            [{"role": "system", "content": "director"}],
            allowed_actor_ids={"uma_a", "uma_b"},
            allowed_target_ids={"player", "uma_a", "uma_b"},
            fallback_actor_ids=["uma_b"],
        )

        self.assertEqual(len(json_runtime.calls), 2)
        self.assertEqual(plan.scene_patch.time, "夜晚")
        self.assertEqual([item.actor_id for item in plan.speakers], ["uma_a"])
        self.assertEqual(plan.speakers[0].target_actor_ids, ["player"])

    async def test_director_runtime_adds_one_fallback_speaker_to_empty_plan(self):
        runtime = DirectorRuntime(
            json_runtime=_FakeJsonRuntime(
                [json.dumps({"narration": "风吹过河面。", "speakers": []})]
            ),
            settings=_Settings,
            max_speakers=2,
        )

        plan = await runtime.generate_plan(
            [{"role": "system", "content": "director"}],
            allowed_actor_ids={"uma_a", "uma_b"},
            allowed_target_ids={"player", "uma_a", "uma_b"},
            fallback_actor_ids=["uma_b", "uma_a"],
        )

        self.assertEqual(plan.narration, "风吹过河面。")
        self.assertEqual([item.actor_id for item in plan.speakers], ["uma_b"])


if __name__ == "__main__":
    unittest.main()
