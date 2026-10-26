import json
import tempfile
import unittest
from pathlib import Path

from umamusume_agent.dialogue.models import DialogueInputEvent
from umamusume_agent.director.context import CharacterSceneContextBuilder
from pydantic import TypeAdapter

from umamusume_agent.director.models import (
    DirectorApproachAction,
    DirectorFollowAction,
    DirectorMoveAction,
    DirectorPlan,
    DirectorSpeakerPlan,
    DirectorTravelAction,
)
from umamusume_agent.director.runtime import DirectorRuntime
from umamusume_agent.director.service import DirectorService
from umamusume_agent.server.stage_routes import create_stage_router
from umamusume_agent.stage.context import StageDirectorContextBuilder
from umamusume_agent.stage.models import (
    AgentStageAction,
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


def _cross_scene_live_stage(
    *,
    include_local_agent=True,
    remote_scene_id="library",
):
    payload = _live_stage().model_dump(mode="json")
    if not include_local_agent:
        payload["actors"] = [
            actor
            for actor in payload["actors"]
            if actor["actor_id"] != "uma_a"
        ]
    payload["remote_actors"] = [
        {"actor_id": "remote_uma", "scene_id": remote_scene_id}
    ]
    payload["travel_destinations"] = [
        {
            "scene_id": "library",
            "display_name": "图书馆",
            "spawn_ids": ["east_gate", "west_gate"],
        },
        {"scene_id": "annexe", "spawn_ids": []},
    ]
    payload["portals"] = [
        {
            "id": "library_portal",
            "target_scene_id": "library",
            "target_spawn_id": "east_gate",
        }
    ]
    return LiveStageContext.model_validate(payload)


def _make_director_service(temp_dir, director_runtime):
    return DirectorService(
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

    async def test_stage_service_allows_unbound_character_in_another_scene(self):
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
                character_names=["角色A", "角色B"],
            )

            result = await StageSceneService(director_service).execute_turn(
                session,
                [
                    DialogueInputEvent(
                        content="你在这里跑步吧",
                        target_actor_ids=["uma_a"],
                    )
                ],
                live_stage=_live_stage(),
                actor_bindings=[
                    StageActorBinding(agent_actor_id="uma_a", stage_actor_id="uma_a")
                ],
            )

            self.assertEqual(
                [action.type for action in result.actions],
                ["actor.move_to", "dialogue.say"],
            )
            _, kwargs = director_runtime.calls[0]
            self.assertEqual(kwargs["allowed_actor_ids"], {"uma_a"})
            self.assertEqual(kwargs["allowed_target_ids"], {"player", "uma_a"})


    async def test_runtime_sanitizes_travel_and_remote_actor_actions(self):
        runtime = DirectorRuntime(
            json_runtime=_FakeJsonRuntime(
                [
                    json.dumps(
                        {
                            "stage_actions": [
                                {
                                    "type": "actor.travel_to_scene",
                                    "actor_id": "uma_a",
                                    "target_scene_id": "library",
                                    "target_spawn_id": "east_gate",
                                },
                                {
                                    "type": "actor.travel_to_scene",
                                    "actor_id": "remote_uma",
                                    "target_scene_id": "annexe",
                                },
                                {
                                    "type": "actor.travel_to_scene",
                                    "actor_id": "uma_a",
                                    "target_scene_id": "unknown_scene",
                                },
                                {
                                    "type": "actor.travel_to_scene",
                                    "actor_id": "uma_a",
                                    "target_scene_id": "library",
                                    "target_spawn_id": "wrong_spawn",
                                },
                                {
                                    "type": "actor.travel_to_scene",
                                    "actor_id": "ghost",
                                    "target_scene_id": "library",
                                },
                                {
                                    "type": "actor.approach",
                                    "actor_id": "remote_uma",
                                    "target_actor_id": "trainer",
                                },
                                {
                                    "type": "actor.follow",
                                    "actor_id": "remote_uma",
                                    "target_actor_id": "trainer",
                                },
                                {
                                    "type": "actor.approach",
                                    "actor_id": "remote_uma",
                                    "target_actor_id": "other_remote",
                                },
                                {
                                    "type": "actor.move_to",
                                    "actor_id": "remote_uma",
                                    "anchor_id": "garden_bench",
                                },
                                {
                                    "type": "actor.face",
                                    "actor_id": "remote_uma",
                                    "facing": "down",
                                },
                                {
                                    "type": "actor.face_actor",
                                    "actor_id": "remote_uma",
                                    "target_actor_id": "trainer",
                                },
                                {
                                    "type": "actor.stop",
                                    "actor_id": "remote_uma",
                                },
                                {
                                    "type": "actor.stop_follow",
                                    "actor_id": "remote_uma",
                                },
                                {
                                    "type": "actor.move_to",
                                    "actor_id": "uma_a",
                                    "anchor_id": "garden_bench",
                                },
                                {
                                    "type": "actor.travel_to_scene",
                                    "actor_id": "uma_a",
                                    "target_scene_id": "library",
                                },
                            ],
                            "speakers": [],
                        }
                    )
                ]
            ),
            settings=_Settings,
            max_speakers=2,
            max_stage_actions=20,
        )

        plan = await runtime.generate_plan(
            [{"role": "system", "content": "stage director"}],
            allowed_actor_ids={"uma_a", "uma_b"},
            allowed_target_ids={"player", "uma_a", "uma_b"},
            fallback_actor_ids=[],
            allowed_anchor_ids={"garden_bench"},
            allowed_stage_actor_ids={"uma_a", "remote_uma"},
            allowed_stage_target_actor_ids={"trainer", "uma_a"},
            remote_stage_actor_ids={"remote_uma"},
            allowed_travel_destinations={
                "library": {"east_gate"},
                "annexe": set(),
            },
            require_speaker=False,
        )

        self.assertEqual(
            [action.type for action in plan.stage_actions],
            [
                "actor.travel_to_scene",
                "actor.travel_to_scene",
                "actor.approach",
                "actor.follow",
                "actor.move_to",
                "actor.travel_to_scene",
            ],
        )
        self.assertEqual(
            [action.actor_id for action in plan.stage_actions],
            [
                "uma_a",
                "remote_uma",
                "remote_uma",
                "remote_uma",
                "uma_a",
                "uma_a",
            ],
        )
        self.assertEqual(plan.stage_actions[0].target_spawn_id, "east_gate")
        self.assertIsNone(plan.stage_actions[1].target_spawn_id)
        self.assertEqual(plan.stage_actions[-1].target_scene_id, "library")
        self.assertIsNone(plan.stage_actions[-1].target_spawn_id)

    async def test_live_stage_additive_contract_and_capabilities(self):
        old_stage = _live_stage()
        self.assertEqual(old_stage.schema_version, "live_stage.v1")
        self.assertEqual(old_stage.remote_actors, [])
        self.assertEqual(old_stage.travel_destinations, [])
        self.assertIsNone(
            StageActorBinding(
                agent_actor_id="uma_a",
                stage_actor_id="uma_a",
            ).scene_id
        )

        action = TypeAdapter(AgentStageAction).validate_python(
            {
                "type": "actor.travel_to_scene",
                "actor_id": "uma_a",
                "target_scene_id": "library",
            }
        )
        self.assertEqual(action.type, "actor.travel_to_scene")
        self.assertIsNone(action.target_spawn_id)
        cross_scene_stage = _cross_scene_live_stage()
        self.assertEqual(cross_scene_stage.portals[0].id, "library_portal")
        self.assertEqual(
            cross_scene_stage.portals[0].target_scene_id,
            "library",
        )
        self.assertEqual(
            cross_scene_stage.portals[0].target_spawn_id,
            "east_gate",
        )

        router = create_stage_router(
            director_service=None,
            stage_service=None,
            sessions={},
            session_ttl_seconds=0,
            max_stage_actions=4,
        )
        route = next(
            item for item in router.routes if item.path == "/stage/capabilities"
        )
        capabilities = await route.endpoint()
        self.assertIn("actor.travel_to_scene", capabilities["action_types"])

    async def test_single_character_remote_binding_travels_and_replies(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            director_runtime = _StageAwareDirectorRuntime(
                DirectorPlan(
                    stage_actions=[
                        DirectorTravelAction(
                            type="actor.travel_to_scene",
                            actor_id="remote_uma",
                            target_scene_id="library",
                            target_spawn_id="east_gate",
                        )
                    ]
                )
            )
            director_service = _make_director_service(temp_dir, director_runtime)
            session = await director_service.create_session(
                user_uuid="00000000-0000-4000-8000-000000000001",
                template_id="test_scene",
                character_names=["角色A"],
            )

            result = await StageSceneService(director_service).execute_turn(
                session,
                [DialogueInputEvent(content="去图书馆等我。")],
                live_stage=_cross_scene_live_stage(include_local_agent=False),
                actor_bindings=[
                    StageActorBinding(
                        agent_actor_id="uma_a",
                        stage_actor_id="remote_uma",
                        scene_id="library",
                    )
                ],
            )

            self.assertEqual(
                [action.type for action in result.actions],
                ["actor.travel_to_scene", "dialogue.say"],
            )
            self.assertEqual(result.actions[0].actor_id, "remote_uma")
            self.assertEqual(result.actions[-1].actor_id, "remote_uma")
            messages, kwargs = director_runtime.calls[0]
            self.assertEqual(kwargs["allowed_stage_actor_ids"], {"remote_uma"})
            self.assertEqual(kwargs["remote_stage_actor_ids"], {"remote_uma"})
            self.assertEqual(kwargs["allowed_stage_target_actor_ids"], {"trainer"})
            self.assertEqual(
                kwargs["allowed_travel_destinations"],
                {
                    "library": {"east_gate", "west_gate"},
                    "annexe": set(),
                },
            )
            packet = json.loads(messages[-1]["content"])
            remote_actors = packet["live_stage"]["remote_actors"]
            self.assertEqual(
                remote_actors,
                [{"actor_id": "remote_uma", "scene_id": "library"}],
            )
            self.assertNotIn("tile", remote_actors[0])
            self.assertEqual(
                packet["live_stage"]["actor_bindings"][0]["scene_id"],
                "library",
            )

    async def test_multi_character_local_and_remote_travel_chain(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            director_runtime = _StageAwareDirectorRuntime(
                DirectorPlan(
                    stage_actions=[
                        DirectorTravelAction(
                            type="actor.travel_to_scene",
                            actor_id="uma_a",
                            target_scene_id="library",
                            target_spawn_id="west_gate",
                        ),
                        DirectorApproachAction(
                            type="actor.approach",
                            actor_id="remote_uma",
                            target_actor_id="trainer",
                        ),
                    ],
                    speakers=[
                        DirectorSpeakerPlan(
                            actor_id="uma_a",
                            target_actor_ids=["player"],
                            intent="回应并前往图书馆",
                        ),
                        DirectorSpeakerPlan(
                            actor_id="uma_b",
                            target_actor_ids=["player"],
                            intent="从其他场景回应并回来",
                        ),
                    ],
                )
            )
            director_service = _make_director_service(temp_dir, director_runtime)
            session = await director_service.create_session(
                user_uuid="00000000-0000-4000-8000-000000000001",
                template_id="test_scene",
                character_names=["角色A", "角色B"],
            )

            result = await StageSceneService(director_service).execute_turn(
                session,
                [DialogueInputEvent(content="我们去图书馆，你也快过来。")],
                live_stage=_cross_scene_live_stage(),
                actor_bindings=[
                    StageActorBinding(
                        agent_actor_id="uma_a",
                        stage_actor_id="uma_a",
                    ),
                    StageActorBinding(
                        agent_actor_id="uma_b",
                        stage_actor_id="remote_uma",
                        scene_id="library",
                    ),
                ],
            )

            self.assertEqual(
                [action.type for action in result.actions],
                [
                    "actor.travel_to_scene",
                    "actor.approach",
                    "dialogue.say",
                    "dialogue.say",
                ],
            )
            self.assertEqual(
                [action.actor_id for action in result.actions[-2:]],
                ["uma_a", "remote_uma"],
            )
            _, kwargs = director_runtime.calls[0]
            self.assertEqual(
                kwargs["allowed_stage_actor_ids"],
                {"uma_a", "remote_uma"},
            )
            self.assertEqual(kwargs["remote_stage_actor_ids"], {"remote_uma"})
            self.assertEqual(
                kwargs["allowed_stage_target_actor_ids"],
                {"trainer", "uma_a"},
            )
            self.assertEqual(kwargs["allowed_actor_ids"], {"uma_a", "uma_b"})
            self.assertEqual(
                kwargs["allowed_travel_destinations"],
                {
                    "library": {"east_gate", "west_gate"},
                    "annexe": set(),
                },
            )

    async def test_stage_service_rejects_invalid_local_and_remote_bindings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            director_runtime = _StageAwareDirectorRuntime(DirectorPlan())
            director_service = _make_director_service(temp_dir, director_runtime)
            stage_service = StageSceneService(director_service)
            session = await director_service.create_session(
                user_uuid="00000000-0000-4000-8000-000000000001",
                template_id="test_scene",
                character_names=["角色A"],
            )

            async def execute(
                binding,
                *,
                live_stage=None,
                target_actor_ids=None,
            ):
                await stage_service.execute_turn(
                    session,
                    [
                        DialogueInputEvent(
                            content="你好",
                            target_actor_ids=target_actor_ids,
                        )
                    ],
                    live_stage=live_stage or _cross_scene_live_stage(),
                    actor_bindings=[binding],
                )

            with self.assertRaisesRegex(ValueError, "remote_actors"):
                await execute(
                    StageActorBinding(
                        agent_actor_id="uma_a",
                        stage_actor_id="remote_uma",
                        scene_id="library",
                    ),
                    live_stage=_live_stage(),
                )
            with self.assertRaisesRegex(ValueError, "remote_actors"):
                await execute(
                    StageActorBinding(
                        agent_actor_id="uma_a",
                        stage_actor_id="remote_uma",
                        scene_id="annexe",
                    )
                )
            with self.assertRaisesRegex(ValueError, "remote_actors"):
                await execute(
                    StageActorBinding(
                        agent_actor_id="uma_a",
                        stage_actor_id="remote_uma",
                    )
                )
            with self.assertRaisesRegex(ValueError, "remote_actors"):
                await execute(
                    StageActorBinding(
                        agent_actor_id="uma_a",
                        stage_actor_id="remote_uma",
                        scene_id="test_scene",
                    ),
                    live_stage=_cross_scene_live_stage(
                        remote_scene_id="test_scene"
                    ),
                )
            with self.assertRaisesRegex(ValueError, "不受 agent 控制"):
                await execute(
                    StageActorBinding(
                        agent_actor_id="uma_a",
                        stage_actor_id="trainer",
                    ),
                    live_stage=_live_stage(),
                )
            with self.assertRaisesRegex(ValueError, "不属于当前会话"):
                await execute(
                    StageActorBinding(
                        agent_actor_id="ghost",
                        stage_actor_id="remote_uma",
                        scene_id="library",
                    )
                )
            with self.assertRaisesRegex(ValueError, "本轮目标角色不属于当前会话"):
                await execute(
                    StageActorBinding(
                        agent_actor_id="uma_a",
                        stage_actor_id="uma_a",
                    ),
                    target_actor_ids=["ghost"],
                )
            self.assertEqual(director_runtime.calls, [])


if __name__ == "__main__":
    unittest.main()
