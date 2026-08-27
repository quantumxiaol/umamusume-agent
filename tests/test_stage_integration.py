import json
import tempfile
import unittest
from pathlib import Path

from umamusume_agent.dialogue.models import DialogueInputEvent
from umamusume_agent.director.context import CharacterSceneContextBuilder
from umamusume_agent.director.models import (
    DirectorApproachAction,
    DirectorFollowAction,
    DirectorMoveAction,
    DirectorPlan,
    DirectorSpeakerPlan,
)
from umamusume_agent.director.runtime import DirectorRuntime
from umamusume_agent.director.service import DirectorService
from umamusume_agent.stage.context import StageDirectorContextBuilder
from umamusume_agent.stage.models import (
    LiveStageContext,
    StageActorBinding,
)
from umamusume_agent.stage.service import StageSceneService
from tests.test_director_service import (
    _CharacterManager,
    _FakeCharacterRuntime,
    _FakeJsonRuntime,
    _Settings,
    _TemplateRepository,
)


class _StageAwareDirectorRuntime:
    def __init__(self, plan=None):
        self.calls = []
        self.plan = plan or DirectorPlan(
            stage_actions=[
                DirectorMoveAction(
                    type="actor.move_to",
                    actor_id="uma_a",
                    anchor_id="garden_bench",
                )
            ],
            speakers=[
                DirectorSpeakerPlan(
                    actor_id="uma_a",
                    target_actor_ids=["player"],
                    intent="走到长椅旁回应",
                )
            ],
        )

    async def generate_plan(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.plan


def _live_stage():
    return LiveStageContext.model_validate(
        {
            "schema_version": "live_stage.v1",
            "stage_id": "test_stage",
            "scene_id": "test_scene",
            "actors": [
                {
                    "actor_id": "trainer",
                    "tile": {"x": 6, "y": 3},
                    "facing": "left",
                    "locomotion": "stand",
                    "control_owner": "player",
                    "nearby_anchors": [],
                },
                {
                    "actor_id": "uma_a",
                    "tile": {"x": 2, "y": 3},
                    "facing": "down",
                    "locomotion": "stand",
                    "control_owner": "agent",
                    "nearby_anchors": [],
                }
            ],
            "anchors": [
                {
                    "id": "garden_bench",
                    "display_name": "花园长椅",
                    "description": "花园旁的长椅",
                    "tags": ["bench"],
                    "available_slots": ["bench_left"],
                }
            ],
            "activity_paths": [],
            "portals": [],
        }
    )


class StageIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_accepts_bound_stage_id_distinct_from_agent_id(self):
        runtime = DirectorRuntime(
            json_runtime=_FakeJsonRuntime(
                [
                    json.dumps(
                        {
                            "stage_actions": [
                                {
                                    "type": "actor.move_to",
                                    "actor_id": "uma_a",
                                    "anchor_id": "garden_bench",
                                },
                                {
                                    "type": "actor.move_to",
                                    "actor_id": "uma_a",
                                    "anchor_id": "hallucinated",
                                },
                                {
                                    "type": "actor.approach",
                                    "actor_id": "uma_a",
                                    "target_actor_id": "trainer",
                                },
                                {
                                    "type": "actor.approach",
                                    "actor_id": "uma_a",
                                    "target_actor_id": "ghost",
                                },
                                {
                                    "type": "actor.follow",
                                    "actor_id": "uma_a",
                                    "target_actor_id": "trainer",
                                    "distance_tiles": 2,
                                },
                                {
                                    "type": "actor.face_actor",
                                    "actor_id": "uma_a",
                                    "target_actor_id": "trainer",
                                },
                                {
                                    "type": "actor.stop_follow",
                                    "actor_id": "uma_a",
                                },
                            ],
                            "speakers": [{"actor_id": "a", "intent": "回应"}],
                        }
                    )
                ]
            ),
            settings=_Settings,
            max_speakers=2,
            max_stage_actions=5,
        )
        plan = await runtime.generate_plan(
            [{"role": "system", "content": "director"}],
            allowed_actor_ids={"a"},
            allowed_target_ids={"player", "a"},
            fallback_actor_ids=["a"],
            allowed_anchor_ids={"garden_bench"},
            allowed_stage_actor_ids={"uma_a"},
            allowed_stage_target_actor_ids={"trainer", "uma_a"},
        )
        self.assertEqual(len(plan.stage_actions), 5)
        self.assertEqual(plan.stage_actions[0].actor_id, "uma_a")
        self.assertEqual(plan.stage_actions[1].target_actor_id, "trainer")
        self.assertEqual(plan.stage_actions[2].type, "actor.follow")
        self.assertEqual(plan.stage_actions[2].target_actor_id, "trainer")
        self.assertEqual(plan.stage_actions[3].type, "actor.face_actor")
        self.assertEqual(plan.stage_actions[3].target_actor_id, "trainer")
        self.assertEqual(plan.stage_actions[4].type, "actor.stop_follow")

    async def test_stage_service_returns_commands_without_calling_browser(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            director_runtime = _StageAwareDirectorRuntime()
            director_service = DirectorService(
                character_manager=_CharacterManager(),
                character_runtime=_FakeCharacterRuntime(),
                director_runtime=director_runtime,
                template_repository=_TemplateRepository(),
                director_context_builder=StageDirectorContextBuilder(
                    settings=_Settings,
                    max_speakers=2,
                ),
                character_context_builder=CharacterSceneContextBuilder(
                    settings=_Settings
                ),
                history_dir=Path(temp_dir),
                max_participants=3,
            )
            session = await director_service.create_session(
                user_uuid="00000000-0000-4000-8000-000000000001",
                template_id="test_scene",
                character_names=["角色A"],
            )
            result = await StageSceneService(director_service).execute_turn(
                session,
                [DialogueInputEvent(content="去花园长椅说吧。")],
                live_stage=_live_stage(),
                actor_bindings=[
                    StageActorBinding(agent_actor_id="uma_a", stage_actor_id="uma_a")
                ],
            )
            self.assertEqual(len(director_runtime.calls), 1)
            messages, kwargs = director_runtime.calls[0]
            packet = json.loads(messages[-1]["content"])
            rendered_events = json.dumps(
                packet["new_events"],
                ensure_ascii=False,
            )
            self.assertIn("角色回复1", rendered_events)
            self.assertIn("动作1", rendered_events)
            self.assertIn("角色本轮已经完成回复", packet["instruction"])
            self.assertIn(
                {"logical_actor_id": "player", "stage_actor_id": "trainer"},
                packet["live_stage"]["logical_actor_bindings"],
            )
            self.assertFalse(kwargs["require_speaker"])
            self.assertEqual(
                [action.type for action in result.actions],
                ["actor.move_to", "dialogue.say"],
            )
            self.assertEqual(result.actions[-1].text, "角色回复1")

    async def test_single_character_can_approach_live_player_actor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            director_runtime = _StageAwareDirectorRuntime(
                DirectorPlan(
                    stage_actions=[
                        DirectorApproachAction(
                            type="actor.approach",
                            actor_id="uma_a",
                            target_actor_id="trainer",
                        )
                    ]
                )
            )
            director_service = DirectorService(
                character_manager=_CharacterManager(),
                character_runtime=_FakeCharacterRuntime(),
                director_runtime=director_runtime,
                template_repository=_TemplateRepository(),
                director_context_builder=StageDirectorContextBuilder(
                    settings=_Settings,
                    max_speakers=2,
                ),
                character_context_builder=CharacterSceneContextBuilder(
                    settings=_Settings
                ),
                history_dir=Path(temp_dir),
                max_participants=3,
            )
            session = await director_service.create_session(
                user_uuid="00000000-0000-4000-8000-000000000001",
                template_id="test_scene",
                character_names=["角色A"],
            )
            result = await StageSceneService(director_service).execute_turn(
                session,
                [DialogueInputEvent(content="到我这来")],
                live_stage=_live_stage(),
                actor_bindings=[
                    StageActorBinding(agent_actor_id="uma_a", stage_actor_id="uma_a")
                ],
            )

            self.assertEqual(len(director_runtime.calls), 1)
            self.assertEqual(
                [action.type for action in result.actions],
                ["actor.approach", "dialogue.say"],
            )
            self.assertEqual(result.actions[0].target_actor_id, "trainer")

    async def test_single_character_can_follow_live_player_actor(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            director_runtime = _StageAwareDirectorRuntime(
                DirectorPlan(
                    stage_actions=[
                        DirectorFollowAction(
                            type="actor.follow",
                            actor_id="uma_a",
                            target_actor_id="trainer",
                            distance_tiles=2,
                        )
                    ]
                )
            )
            director_service = DirectorService(
                character_manager=_CharacterManager(),
                character_runtime=_FakeCharacterRuntime(),
                director_runtime=director_runtime,
                template_repository=_TemplateRepository(),
                director_context_builder=StageDirectorContextBuilder(
                    settings=_Settings,
                    max_speakers=2,
                ),
                character_context_builder=CharacterSceneContextBuilder(
                    settings=_Settings
                ),
                history_dir=Path(temp_dir),
                max_participants=3,
            )
            session = await director_service.create_session(
                user_uuid="00000000-0000-4000-8000-000000000001",
                template_id="test_scene",
                character_names=["角色A"],
            )
            result = await StageSceneService(director_service).execute_turn(
                session,
                [DialogueInputEvent(content="跟着我走")],
                live_stage=_live_stage(),
                actor_bindings=[
                    StageActorBinding(agent_actor_id="uma_a", stage_actor_id="uma_a")
                ],
            )

            self.assertEqual(
                [action.type for action in result.actions],
                ["actor.follow", "dialogue.say"],
            )
            self.assertEqual(result.actions[0].target_actor_id, "trainer")

    async def test_stage_service_rejects_unbound_session_character(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            director_service = DirectorService(
                character_manager=_CharacterManager(),
                character_runtime=_FakeCharacterRuntime(),
                director_runtime=_StageAwareDirectorRuntime(),
                template_repository=_TemplateRepository(),
                director_context_builder=StageDirectorContextBuilder(
                    settings=_Settings,
                    max_speakers=2,
                ),
                character_context_builder=CharacterSceneContextBuilder(
                    settings=_Settings
                ),
                history_dir=Path(temp_dir),
                max_participants=3,
            )
            session = await director_service.create_session(
                user_uuid="00000000-0000-4000-8000-000000000001",
                template_id="test_scene",
                character_names=["角色A"],
            )
            with self.assertRaisesRegex(ValueError, "缺少 Stage 角色绑定"):
                await StageSceneService(director_service).execute_turn(
                    session,
                    [DialogueInputEvent(content="你好")],
                    live_stage=_live_stage(),
                    actor_bindings=[],
                )


if __name__ == "__main__":
    unittest.main()
