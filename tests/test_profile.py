"""学习者画像的离线测试：不联网、不碰模型，跑起来很快。

用法：python -m unittest tests.test_profile -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from divana.profile import (
    DEFAULT_PROFILE_PATH,
    MAX_SECTION_CHARS,
    SECTIONS,
    TEMPLATE,
    ProfileError,
    ProfileStore,
)
from divana.markdown_store import iter_sections


class ProfileStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "vault" / "profile.md"

    def store(self) -> ProfileStore:
        return ProfileStore(self.path)

    def test_default_path_is_in_vault(self) -> None:
        self.assertEqual(DEFAULT_PROFILE_PATH.name, "profile.md")
        self.assertEqual(DEFAULT_PROFILE_PATH.parent.name, "vault")

    def test_read_falls_back_to_template_without_touching_disk(self) -> None:
        self.assertEqual(self.store().read(), TEMPLATE)
        self.assertFalse(self.path.exists())

    def test_ensure_exists_creates_file_with_every_section(self) -> None:
        store = self.store()
        store.ensure_exists()

        self.assertTrue(self.path.exists())
        text = store.read()
        for section in SECTIONS:
            self.assertIn(f"## {section}", text)

    def test_iter_sections_finds_each_heading_once(self) -> None:
        lines = TEMPLATE.splitlines()
        found = [name for name, _, _ in iter_sections(lines)]
        self.assertEqual(found, list(SECTIONS))

    def test_write_section_replaces_only_that_section(self) -> None:
        store = self.store()
        store.ensure_exists()
        store.write_section("目标", "学会用 PyTorch 训练一个自己的小模型。")

        text = store.read()
        self.assertIn("学会用 PyTorch 训练一个自己的小模型。", text)
        # 其它章节的占位内容原样还在
        self.assertIn("## 薄弱点\n\n（还没记录。）", text)

    def test_write_section_can_be_called_twice(self) -> None:
        store = self.store()
        store.write_section("目标", "第一版")
        store.write_section("目标", "第二版")

        text = store.read()
        self.assertIn("第二版", text)
        self.assertNotIn("第一版", text)
        # 同一节标题不该出现两次
        self.assertEqual(text.count("## 目标"), 1)

    def test_write_section_keeps_handwritten_edits(self) -> None:
        """用户自己加的笔记和章节不能被程序弄丢。"""
        store = self.store()
        store.ensure_exists()
        self.path.write_text(
            store.read() + "\n## 我自己的备注\n\n这一节是手写的，程序不该碰。\n",
            encoding="utf-8",
        )

        store.write_section("当前水平", "能读懂简单的前向传播代码。")

        text = store.read()
        self.assertIn("## 我自己的备注", text)
        self.assertIn("这一节是手写的，程序不该碰。", text)

    def test_unknown_section_is_rejected(self) -> None:
        with self.assertRaises(ProfileError):
            self.store().write_section("最喜欢的颜色", "蓝色")

    def test_empty_content_is_rejected(self) -> None:
        with self.assertRaises(ProfileError):
            self.store().write_section("目标", "   \n  ")

    def test_too_long_content_is_rejected(self) -> None:
        with self.assertRaises(ProfileError):
            self.store().write_section("目标", "长" * (MAX_SECTION_CHARS + 1))

    def test_write_leaves_no_temp_file_behind(self) -> None:
        store = self.store()
        store.write_section("已掌握", "列表推导式、字典、函数。")

        leftovers = sorted(p.name for p in self.path.parent.iterdir())
        self.assertEqual(leftovers, ["profile.md"])

    def test_written_file_ends_with_newline(self) -> None:
        store = self.store()
        store.write_section("学习习惯", "喜欢先看例子再看定义。")
        self.assertTrue(store.read().endswith("\n"))


if __name__ == "__main__":
    unittest.main()
