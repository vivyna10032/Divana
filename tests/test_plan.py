"""学习计划的离线测试。

通用的读写机制已经被 test_profile.py 覆盖了（两个 store 共用同一套实现），
所以这里重点测**计划特有的部分**：章节、模板、错误类型。

用法：python -m unittest tests.test_plan -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from divana.markdown_store import MarkdownStoreError, iter_sections
from divana.plan import (
    DEFAULT_PLAN_PATH,
    MAX_SECTION_CHARS,
    SECTIONS,
    TEMPLATE,
    PlanError,
    PlanStore,
)
from divana.profile import ProfileError


class PlanStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "vault" / "plan.md"

    def store(self) -> PlanStore:
        return PlanStore(self.path)

    def test_default_path_is_in_vault(self) -> None:
        self.assertEqual(DEFAULT_PLAN_PATH.name, "plan.md")
        self.assertEqual(DEFAULT_PLAN_PATH.parent.name, "vault")

    def test_sections_cover_the_four_parts_of_a_plan(self) -> None:
        self.assertEqual(SECTIONS, ("现在的位置", "下一步", "路线图", "复盘记录"))

    def test_read_falls_back_to_template_without_touching_disk(self) -> None:
        self.assertEqual(self.store().read(), TEMPLATE)
        self.assertFalse(self.path.exists())

    def test_ensure_exists_creates_file_with_every_section(self) -> None:
        store = self.store()
        store.ensure_exists()

        self.assertTrue(self.path.exists())
        for section in SECTIONS:
            self.assertIn(f"## {section}", store.read())

    def test_iter_sections_finds_each_heading_once(self) -> None:
        found = [name for name, _, _ in iter_sections(TEMPLATE.splitlines())]
        self.assertEqual(found, list(SECTIONS))

    def test_write_section_replaces_only_that_section(self) -> None:
        store = self.store()
        store.write_section("现在的位置", "在做 Divana，v0.5 阶段。")

        text = store.read()
        self.assertIn("在做 Divana，v0.5 阶段。", text)
        self.assertIn("## 下一步\n\n（还没定。", text)

    def test_can_mark_a_milestone_done(self) -> None:
        store = self.store()
        store.write_section(
            "路线图",
            "### 阶段一：agent 基础\n- [x] 搞懂工具调用循环\n- [ ] 自己写一个最小 agent",
        )

        text = store.read()
        self.assertIn("- [x] 搞懂工具调用循环", text)
        self.assertIn("- [ ] 自己写一个最小 agent", text)

    def test_keeps_handwritten_edits(self) -> None:
        store = self.store()
        store.ensure_exists()
        self.path.write_text(
            store.read() + "\n## 我自己的备忘\n\n手写的东西不许被碰。\n",
            encoding="utf-8",
        )

        store.write_section("下一步", "把 v0.5 收个尾。")

        text = store.read()
        self.assertIn("## 我自己的备忘", text)
        self.assertIn("手写的东西不许被碰。", text)

    def test_unknown_section_lists_the_valid_ones(self) -> None:
        with self.assertRaises(PlanError) as ctx:
            self.store().write_section("减肥计划", "每天跑步")
        self.assertIn("现在的位置", str(ctx.exception))

    def test_plan_error_is_a_markdown_store_error(self) -> None:
        # 工具捕获的是具体类型，但共用基类才能让上层统一处理
        self.assertTrue(issubclass(PlanError, MarkdownStoreError))

    def test_plan_and_profile_have_separate_error_types(self) -> None:
        # error_cls 是类属性覆写，这里确认它没有串台
        self.assertFalse(issubclass(PlanError, ProfileError))

    def test_empty_content_is_rejected(self) -> None:
        with self.assertRaises(PlanError):
            self.store().write_section("下一步", "   \n  ")

    def test_read_section_returns_body_without_heading(self) -> None:
        store = self.store()
        store.ensure_exists()
        text = store.read_section("现在的位置")

        self.assertIn("还没定", text)
        self.assertNotIn("## 现在的位置", text)

    def test_read_section_sees_the_latest_write(self) -> None:
        store = self.store()
        store.write_section("下一步", "- [ ] 读完 run.py")
        self.assertEqual(store.read_section("下一步"), "- [ ] 读完 run.py")

    def test_read_section_rejects_unknown_name(self) -> None:
        with self.assertRaises(PlanError):
            self.store().read_section("不存在的节")

    def test_read_section_returns_empty_when_hand_deleted(self) -> None:
        """文件被手改过也不能炸——读操作要能容错。"""
        store = self.store()
        store.ensure_exists()
        self.path.write_text(
            store.read().replace("## 复盘记录", "## 被改名了"), encoding="utf-8"
        )
        self.assertEqual(store.read_section("复盘记录"), "")

    def test_too_long_content_is_rejected(self) -> None:
        with self.assertRaises(PlanError):
            self.store().write_section("路线图", "长" * (MAX_SECTION_CHARS + 1))

    def test_leaves_no_temp_file_behind(self) -> None:
        store = self.store()
        store.write_section("下一步", "读一遍 agents SDK 的 run.py。")

        leftovers = sorted(p.name for p in self.path.parent.iterdir())
        self.assertEqual(leftovers, ["plan.md"])


if __name__ == "__main__":
    unittest.main()
