import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from umamusume_agent.config import config
from umamusume_agent.dialogue.context import LegacyDialogueContextBuilder
from umamusume_agent.dialogue.history import (
    create_history_file_path,
    load_persistent_history,
    parse_history_file,
)
from umamusume_agent.dialogue.session import DialogueSession


class _Character:
    name_zh = "测试角色"
    name_en = "test_character"
    name_jp = "テストキャラクター"

    @staticmethod
    def get_system_prompt():
        return "你是测试角色。"


class DialogueHistoryAndSessionTests(unittest.TestCase):
    def test_session_jsonl_round_trip_preserves_structured_reply(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history_dir = root / "dialogues"
            output_dir = root / "outputs"
            output_dir.mkdir()
            created_at = datetime(2026, 7, 22, 12, 0, 0)
            session_id = "12345678-1234-4234-8234-123456789abc"
            user_uuid = "00000000-0000-4000-8000-000000000001"
            character = _Character()
            history_file = create_history_file_path(
                history_dir,
                user_uuid,
                character,
                created_at,
                session_id,
            )
            context_builder = LegacyDialogueContextBuilder(
                settings=config,
                prefix_cache_enabled=False,
                hidden_reinjection_enabled=False,
            )
            session = DialogueSession(
                session_id,
                character,
                user_uuid,
                output_dir=output_dir,
                history_file=history_file,
                context_builder=context_builder,
                created_at=created_at,
            )

            session.add_message("user", "今天训练什么？")
            session.add_message(
                "assistant",
                "先从热身开始吧。",
                action="她整理了一下训练计划。",
                dialogue="先从热身开始吧。",
                source_format="json_v2",
                schema_version=2,
            )

            messages, character_names = parse_history_file(history_file)
            self.assertEqual(len(messages), 2)
            self.assertEqual(messages[0]["content"], "今天训练什么？")
            self.assertEqual(messages[1]["action"], "她整理了一下训练计划。")
            self.assertEqual(messages[1]["dialogue"], "先从热身开始吧。")
            self.assertIn("test_character", character_names)

            restored = load_persistent_history(
                history_dir,
                user_uuid,
                character,
                history_max_messages=0,
            )
            self.assertEqual(
                restored,
                [
                    {"role": "user", "content": "今天训练什么？"},
                    {
                        "role": "assistant",
                        "content": (
                            "角色动作：她整理了一下训练计划。\n"
                            "角色对白：先从热身开始吧。"
                        ),
                    },
                ],
            )

            records = [
                json.loads(line)
                for line in history_file.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(records[0]["event"], "session_start")
            self.assertEqual(records[-1]["schema_version"], 2)


if __name__ == "__main__":
    unittest.main()
