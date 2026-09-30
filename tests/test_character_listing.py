"""Stable character selection order across local and hosted backends."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from tests.server_support import test_settings
from umamusume_agent.character import CharacterManager
from umamusume_agent.server.app import create_app
from umamusume_agent.server.services import build_services


class CharacterListingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.characters_dir = self.root / "characters"
        self.characters_dir.mkdir()

    def write_character(self, directory, data):
        path = self.characters_dir / directory
        path.mkdir()
        (path / "config.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def test_sorts_by_english_name_not_chinese_or_directory_order(self):
        paths = [
            self.write_character("aaa", {"name_en": "Silence Suzuka", "name_zh": "无声铃鹿"}),
            self.write_character("zzz", {"name_en": " admire Vega ", "name_zh": "爱慕织姬"}),
            self.write_character("mmm", {"name_en": "Agnes Digital", "name_zh": "爱丽数码"}),
        ]
        manager = CharacterManager(str(self.characters_dir))
        for order in (paths, list(reversed(paths))):
            with self.subTest(order=[path.name for path in order]):
                with patch.object(Path, "iterdir", return_value=iter(order)):
                    self.assertEqual(manager.list_characters(), ["爱慕织姬", "爱丽数码", "无声铃鹿"])

    def test_missing_or_invalid_english_names_fall_back_to_directory(self):
        self.write_character("alpha", {"name_zh": "缺少英文名"})
        self.write_character("bravo", {"name_en": None, "name_zh": "空值"})
        self.write_character("charlie", {"name_en": "  ", "name_zh": "空白"})
        self.write_character("delta", {"name_en": 123, "name_zh": "无效类型"})
        self.write_character("echo", {"name_en": "Zulu"})
        manager = CharacterManager(str(self.characters_dir))
        self.assertEqual(manager.list_characters(), ["缺少英文名", "空值", "空白", "无效类型", "echo"])

    def test_equal_english_names_have_a_stable_directory_tie_breaker(self):
        second = self.write_character("b", {"name_en": "Alpha", "name_zh": "角色二"})
        first = self.write_character("a", {"name_en": "ALPHA", "name_zh": "角色一"})
        manager = CharacterManager(str(self.characters_dir))
        with patch.object(Path, "iterdir", return_value=iter([second, first])):
            self.assertEqual(manager.list_characters(), ["角色一", "角色二"])

    def test_invalid_configs_and_non_character_files_are_still_skipped(self):
        broken = self.write_character("broken", {})
        (broken / "config.json").write_text("not-json", encoding="utf-8")
        self.write_character("invalid-shape", [])
        (self.characters_dir / "empty").mkdir()
        (self.characters_dir / "note.txt").write_text("not a character", encoding="utf-8")
        self.write_character("valid", {"name_en": "Valid", "name_zh": "有效角色"})
        self.assertEqual(CharacterManager(str(self.characters_dir)).list_characters(), ["有效角色"])

    async def test_characters_api_keeps_chinese_string_list_in_english_order(self):
        self.write_character("z", {"name_en": "Admire Vega", "name_zh": "爱慕织姬"})
        self.write_character("a", {"name_en": "Silence Suzuka", "name_zh": "无声铃鹿"})
        services = build_services(settings=test_settings(self.root), llm_client=object(), tts_client=object())
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(services=services)), base_url="http://test",
        ) as client:
            response = await client.get("/characters")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"characters": ["爱慕织姬", "无声铃鹿"]})


if __name__ == "__main__":
    unittest.main()
