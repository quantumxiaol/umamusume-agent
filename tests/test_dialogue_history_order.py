"""History ordering regressions; local JSONL fixtures only, no provider calls."""

import json
import os
import tempfile
import time
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from umamusume_agent.config import config
from umamusume_agent.dialogue.context import LegacyDialogueContextBuilder
from umamusume_agent.dialogue.history import (
    collect_history_messages,
    load_history_memory,
    load_persistent_history,
)
from umamusume_agent.dialogue.history_order import parse_history_timestamp
from umamusume_agent.dialogue.memory import HistoryCheckpoint, prompt_digest, source_digest
from umamusume_agent.dialogue.session import DialogueSession


USER = "00000000-0000-4000-8000-000000000001"


@contextmanager
def local_timezone(name):
    try:
        with patch.dict(os.environ, {"TZ": name}):
            time.tzset()
            yield
    finally:
        time.tzset()


class HistoryOrderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.character = SimpleNamespace(
            id="test", name_en="test_character", name_zh="测试角色",
            get_system_prompt=lambda: "你是测试角色。",
        )
        self.manager = SimpleNamespace(character_exists=lambda _: False)
        self.builder = LegacyDialogueContextBuilder(
            settings=config, prefix_cache_enabled=False, hidden_reinjection_enabled=False,
        )

    def write_events(self, session_id, records):
        path = self.root / USER / f"test_character_20260930_000000_{session_id}" / "history.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        defaults = {"session_id": session_id, "user_uuid": USER, "character_name_en": "test_character"}
        path.write_text("".join(json.dumps({**defaults, **record}, ensure_ascii=False) + "\n"
                                for record in records), encoding="utf-8")
        return path

    @staticmethod
    def message(content, timestamp, index=1):
        return {"event": "message", "role": "user", "content": content,
                "timestamp": timestamp, "message_index": index}

    def assert_message_order(self, expected):
        displayed = collect_history_messages(
            self.root, USER, character_name="test_character", character_manager=self.manager,
        )
        restored = load_persistent_history(self.root, USER, self.character, history_max_messages=0)
        self.assertEqual([record["content"] for record in displayed], expected)
        self.assertEqual([record["content"] for record in restored], expected)
        return displayed

    def memory_session(self):
        return SimpleNamespace(
            user_uuid=USER, session_id="memory", character=self.character,
            context_builder=self.builder, history=[{"role": "user", "content": "约定"}],
        )

    def checkpoint(self, session, revision):
        return HistoryCheckpoint(
            revision=revision, user_uuid=USER, character_id=self.character.id,
            covered_messages=1, summary=f"第 {revision} 版记忆",
            source_digest=source_digest(session.history), prompt_digest=prompt_digest(session),
        )

    def test_offsets_z_and_fractional_seconds_normalize_to_utc(self):
        expected = datetime(2026, 9, 30, 1, 0, 0, 123000, tzinfo=timezone.utc)
        for text in ("2026-09-30T09:00:00.123+08:00", "2026-09-30T01:00:00.123000Z",
                     "2026-09-29T20:00:00.123-05:00", " 2026-09-30T01:00:00.123+00:00 "):
            with self.subTest(timestamp=text):
                self.assertEqual(parse_history_timestamp(text), expected)
        for value in (None, "", "bad-date", "2026-99-30T09:00:00", 123, {}, [],
                      "0001-01-01T00:00:00+14:00"):
            with self.subTest(invalid=value):
                self.assertIsNone(parse_history_timestamp(value))

    @unittest.skipUnless(hasattr(time, "tzset"), "requires process-local timezone control")
    def test_naive_legacy_timestamps_follow_server_local_timezone(self):
        with local_timezone("Asia/Shanghai"):
            self.assertEqual(parse_history_timestamp("2026-09-30T09:00:00"),
                             parse_history_timestamp("2026-09-30T01:00:00Z"))
            self.write_events("aaaaaaaa", [
                self.message("重置前", "2026-09-30T08:59:59"),
                {"event": "history_import", "replace_current": True, "timestamp": "2026-09-30T09:00:00"},
                self.message("重置后", "2026-09-30T01:30:00Z", 2),
            ])
            self.assert_message_order(["重置后"])
        # Use the local offset at the historical date, not today's offset.
        with local_timezone("America/New_York"):
            self.assertEqual(parse_history_timestamp("2026-01-15T12:00:00"),
                             parse_history_timestamp("2026-01-15T17:00:00Z"))
            self.assertEqual(parse_history_timestamp("2026-07-15T12:00:00"),
                             parse_history_timestamp("2026-07-15T16:00:00Z"))

    def test_display_and_recovery_share_cross_session_chronology(self):
        self.write_events("aaaaaaaa", [
            self.message("第二条", "2026-09-30T09:01:00+08:00"),
            self.message("第四条", "2026-09-30T01:03:00Z", 2),
        ])
        self.write_events("bbbbbbbb", [
            self.message("第一条", "2026-09-30T01:00:00Z"),
            self.message("第三条", "2026-09-30T09:02:00+08:00", 2),
        ])
        self.assert_message_order(["第一条", "第二条", "第三条", "第四条"])
        tail = load_persistent_history(self.root, USER, self.character, history_max_messages=2)
        self.assertEqual([item["content"] for item in tail], ["第三条", "第四条"])

    def test_equal_instants_use_numeric_indices_and_stable_fallbacks(self):
        timestamp = "2026-09-30T01:00:00Z"
        self.write_events("aaaaaaaa", [
            self.message("第十条", "2026-09-30T09:00:00+08:00", "10"),
            self.message("第二条", timestamp, 2),
            self.message("第一条", timestamp, "1"),
            self.message("旧索引一", timestamp, "bad"),
            self.message("旧索引二", timestamp, None),
            self.message("旧索引三", timestamp, {}),
        ])
        self.write_events("bbbbbbbb", [self.message("另一会话", timestamp)])
        displayed = self.assert_message_order([
            "旧索引一", "旧索引二", "旧索引三", "第一条", "第二条", "第十条", "另一会话",
        ])
        self.assertEqual(displayed[3]["message_index"], "1")  # Don't rewrite the public/archive value.

    def test_offset_reset_keeps_later_utc_messages(self):
        self.write_events("aaaaaaaa", [self.message("旧内容", "2026-09-30T00:59:59Z")])
        self.write_events("bbbbbbbb", [
            {"event": "history_import", "replace_current": True, "timestamp": "2026-09-30T09:00:00+08:00"},
            self.message("新内容", "2026-09-30T01:30:00Z"),
        ])
        self.assert_message_order(["新内容"])

    def test_latest_reset_uses_real_time_and_remains_character_scoped(self):
        self.write_events("aaaaaaaa", [
            {"event": "history_import", "replace_current": True, "timestamp": "2026-09-30T09:00:00+08:00"},
            self.message("中间版本", "2026-09-30T09:30:00+08:00"),
        ])
        self.write_events("bbbbbbbb", [
            {"event": "history_cleared", "timestamp": "2026-09-30T02:00:00Z"},
            self.message("最终版本", "2026-09-30T02:30:00Z"),
            {"event": "history_cleared", "timestamp": "2026-10-01T00:00:00Z", "character_name_en": "other"},
        ])
        self.assert_message_order(["最终版本"])

    def test_invalid_reset_cannot_hide_messages(self):
        self.write_events("aaaaaaaa", [
            self.message("保留原文", "2026-09-30T00:00:00Z"),
            {"event": "history_import", "replace_current": True, "timestamp": None},
            {"event": "history_cleared", "timestamp": "not-a-date"},
        ])
        with self.assertLogs("umamusume_agent.dialogue.history", level="WARNING"):
            self.assert_message_order(["保留原文"])

    def test_unknown_message_dates_are_retained_with_stable_order(self):
        self.write_events("aaaaaaaa", [
            self.message("无时间", None, 1),
            self.message("坏时间", "bad-date", 2),
            self.message("确定已过期", "2026-09-30T00:00:00Z", 3),
            {"event": "history_cleared", "timestamp": "2026-09-30T01:00:00Z"},
            self.message("新消息", "2026-09-30T02:00:00Z", 4),
        ])
        self.assert_message_order(["无时间", "坏时间", "新消息"])

    def test_checkpoint_recovery_uses_actual_time_across_sessions(self):
        session = self.memory_session()
        first, second = self.checkpoint(session, 1), self.checkpoint(session, 2)
        self.write_events("aaaaaaaa", [{
            "event": "context_checkpoint", "timestamp": "2026-09-30T09:00:00+08:00",
            "checkpoint": first.model_dump(mode="json"),
        }])
        self.write_events("bbbbbbbb", [{
            "event": "context_checkpoint", "timestamp": "2026-09-30T01:30:00Z",
            "checkpoint": second.model_dump(mode="json"),
        }])
        active, snapshots = load_history_memory(self.root, session)
        self.assertEqual(active, second)
        self.assertEqual(snapshots, [first, second])

    def test_later_empty_checkpoint_revision_does_not_resurrect_old_memory(self):
        session = self.memory_session()
        checkpoint = self.checkpoint(session, 1).model_dump(mode="json")
        self.write_events("aaaaaaaa", [
            {"event": "context_checkpoint", "timestamp": "2026-09-30T09:00:00+08:00", "checkpoint": checkpoint},
            {"event": "context_checkpoint", "timestamp": "2026-09-30T01:30:00Z", "checkpoint": None, "checkpoints": []},
            {"event": "context_checkpoint", "timestamp": "bad-date", "checkpoint": checkpoint},
        ])
        self.assertEqual(load_history_memory(self.root, session), (None, []))

    def test_checkpoint_same_instant_preserves_file_event_order(self):
        session = self.memory_session()
        checkpoint = self.checkpoint(session, 1).model_dump(mode="json")
        self.write_events("aaaaaaaa", [
            {"event": "context_checkpoint", "timestamp": "2026-09-30T09:00:00+08:00", "checkpoint": checkpoint},
            {"event": "context_checkpoint", "timestamp": "2026-09-30T01:00:00Z", "checkpoint": None, "checkpoints": []},
        ])
        self.assertEqual(load_history_memory(self.root, session), (None, []))

    def test_new_jsonl_events_use_utc_without_rewriting_imported_timestamp(self):
        path = self.root / USER / "new-session" / "history.jsonl"
        session = DialogueSession(
            "new-session", self.character, USER, output_dir=self.root / "outputs",
            history_file=path, context_builder=self.builder,
        )
        original_timestamp = "2025-01-01T09:00:00+08:00"
        session.import_messages([{"role": "user", "content": "旧原文", "timestamp": original_timestamp}])
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for event in events:
            self.assertEqual(datetime.fromisoformat(event["timestamp"]).tzinfo, timezone.utc)
        message = next(event for event in events if event["event"] == "message")
        self.assertEqual(message["imported_timestamp"], original_timestamp)
        self.assert_message_order(["旧原文"])


if __name__ == "__main__":
    unittest.main()
