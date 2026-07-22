import unittest

from umamusume_agent.dialogue.context import LegacyDialogueContextBuilder


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
        self.assertEqual(context.messages[3]["role"], "system")
        self.assertIn("JSON 格式提醒", context.messages[3]["content"])


if __name__ == "__main__":
    unittest.main()
