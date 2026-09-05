import unittest

from umamusume_agent.dialogue.context import LegacyDialogueContextBuilder
from umamusume_agent.dialogue.protocol import to_compact_context_message


class _Settings:
    LLM_JSON_ENABLED = True
    LLM_JSON_OUTPUT_MODE = "auto"
    ROLEPLAY_LLM_MODEL_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ROLEPLAY_LLM_MODEL_NAME = "qwen-test"
    DIALOGUE_PREFIX_CACHE_ENABLED = True
    DIALOGUE_PREFIX_CACHE_MIN_CHARS = 1
    DIALOGUE_HIDDEN_FORMAT_REINJECTION_ENABLED = True
    DIALOGUE_HIDDEN_FORMAT_REINJECTION_INTERVAL_MESSAGES = 2


class _Character:
    @staticmethod
    def get_system_prompt():
        return "角色提示词"


class LegacyDialogueContextBuilderTests(unittest.TestCase):
    def test_keeps_stable_system_prefix_and_reinjects_hidden_constraint(self):
        builder = LegacyDialogueContextBuilder(settings=_Settings)
        history = [
            {"role": "user", "content": "第一句"},
            {"role": "assistant", "content": "角色对白：第二句"},
        ]

        context = builder.build(
            character=_Character(),
            history=history,
            text_only=False,
        )

        self.assertEqual(context.messages[0]["role"], "system")
        self.assertTrue(context.messages[0]["content"].startswith("角色提示词\n\n"))
        self.assertEqual(
            context.messages[0]["cache_control"],
            {"type": "ephemeral"},
        )
        self.assertEqual(context.messages[1:3], history)
        self.assertEqual(len(context.messages), 3)

        next_history = [*history, {"role": "user", "content": "第三句"}]
        next_context = builder.build(character=_Character(), history=next_history)
        self.assertEqual(next_context.messages[:3], context.messages)
        self.assertEqual(next_context.messages[3]["role"], "user")
        self.assertIn("JSON 格式提醒", next_context.messages[3]["content"])
        self.assertEqual(next_history[-1]["content"], "第三句")

    def test_reminders_preserve_every_previous_request_across_boundaries(self):
        builder = LegacyDialogueContextBuilder(settings=_Settings)
        history = []
        previous = []
        for turn in range(1, 55):
            history.append({"role": "user", "content": f"第{turn}轮"})
            messages = builder.build(character=_Character(), history=history).messages
            self.assertEqual(messages[:len(previous)], previous)
            self.assertEqual(sum(m["role"] == "system" for m in messages), 1)
            previous = list(messages)
            history.append({"role": "assistant", "content": f"回答{turn}"})

    def test_story_event_types_render_without_changing_legacy_messages(self):
        legacy = to_compact_context_message(
            {"role": "user", "content": "你好"}
        )
        action = to_compact_context_message(
            {
                "role": "user",
                "content": "把毛巾递了过去。",
                "actor": {
                    "actor_id": "player",
                    "actor_type": "trainer",
                    "display_name": "训练员",
                },
                "event_type": "action",
                "event_schema_version": 1,
            }
        )
        scene_event = to_compact_context_message(
            {
                "role": "user",
                "content": "开始下雨了。",
                "actor": {
                    "actor_id": "narrator",
                    "actor_type": "narrator",
                    "display_name": "环境",
                },
                "event_type": "scene_event",
                "event_schema_version": 1,
            }
        )

        self.assertEqual(legacy, {"role": "user", "content": "你好"})
        self.assertEqual(
            action,
            {"role": "user", "content": "【训练员动作】把毛巾递了过去。"},
        )
        self.assertEqual(
            scene_event,
            {"role": "user", "content": "【环境变化】开始下雨了。"},
        )


if __name__ == "__main__":
    unittest.main()
