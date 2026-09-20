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
    UNGROUPED,
    PlanError,
    PlanStore,
    parse_milestones,
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

    def test_updated_at_is_empty_before_the_file_exists(self) -> None:
        self.assertEqual(self.store().updated_at(), "")

    def test_updated_at_is_a_local_timestamp(self) -> None:
        store = self.store()
        store.ensure_exists()
        self.assertRegex(store.updated_at(), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_progress_counts_milestones(self) -> None:
        store = self.store()
        store.write_section("路线图", "### 阶段一\n- [x] 已做完的\n- [ ] 还没做的")

        progress = store.progress()
        self.assertEqual((progress.done, progress.total), (1, 2))
        self.assertEqual(progress.percent, 50)

    def test_progress_ignores_other_sections(self) -> None:
        """\"下一步\"那节里也可能有 `- [ ]`，混进来进度就虚高了。"""
        store = self.store()
        store.write_section("路线图", "### 阶段一\n- [x] 路线图里的\n- [ ] 也是路线图里的")
        store.write_section("下一步", "- [ ] 这件事不属于路线图")

        progress = store.progress()
        self.assertEqual((progress.done, progress.total), (1, 2))

    def test_append_replaces_the_placeholder(self) -> None:
        store = self.store()
        store.ensure_exists()
        store.append_to_section("复盘记录", "## 第一次复盘\n\n内容")

        text = store.read()
        self.assertIn("## 第一次复盘", text)
        self.assertNotIn("（还没有复盘", text)  # 占位符被顶掉了，不会永远留着
        self.assertIn("## 现在的位置", text)  # 别的节没动

    def test_append_keeps_earlier_entries(self) -> None:
        store = self.store()
        store.append_to_section("复盘记录", "## 第一次\n\n甲")
        store.append_to_section("复盘记录", "## 第二次\n\n乙")

        text = store.read()
        self.assertLess(text.index("第一次"), text.index("第二次"))  # 新的在后面
        self.assertIn("甲", text)
        self.assertIn("乙", text)

    def test_append_demotes_level2_headings_in_the_entry(self) -> None:
        """追加内容自带 `## 标题` 时必须降级。

        不降级的话，那个标题会被当成新章节，下一次追加就会插到它前面
        ——日记顺序反了，而且看起来一切正常。这是测试抓出来的真 bug。
        """
        store = self.store()
        store.append_to_section("复盘记录", "## 第一次复盘\n\n甲")
        text = store.read()

        self.assertIn("### 第一次复盘", text)
        self.assertNotIn("\n## 第一次复盘", text)
        self.assertIn("甲", store.read_section("复盘记录"))  # 内容还在这一节里

    def test_append_does_not_touch_other_sections(self) -> None:
        store = self.store()
        store.write_section("下一步", "- [ ] 别动我")
        before = store.read_section("下一步")

        store.append_to_section("复盘记录", "## 复盘")

        self.assertEqual(store.read_section("下一步"), before)

    def test_append_handles_a_hand_emptied_section(self) -> None:
        """有人把那一节的内容手删了，追加也不该出错。"""
        store = self.store()
        store.ensure_exists()
        self.path.write_text(
            store.read().replace("（还没有复盘。每次回顾的结论会记在这里。）", ""),
            encoding="utf-8",
        )

        store.append_to_section("复盘记录", "## 新复盘")
        self.assertIn("## 新复盘", store.read())

    def test_append_rejects_unknown_section_and_empty_text(self) -> None:
        with self.assertRaises(PlanError):
            self.store().append_to_section("不存在的节", "x")
        with self.assertRaises(PlanError):
            self.store().append_to_section("复盘记录", "   ")


class ParseMilestonesTest(unittest.TestCase):
    """解析器要能容错——markdown 是模型写的，格式不可能百分百稳定。"""

    def test_empty_text(self) -> None:
        progress = parse_milestones("")
        self.assertEqual((progress.done, progress.total), (0, 0))
        self.assertEqual(progress.percent, 0)  # 不能除以零
        self.assertEqual(progress.stages, ())

    def test_counts_done_and_pending(self) -> None:
        progress = parse_milestones("- [x] 搞懂工具调用\n- [ ] 自己写一个 agent")
        self.assertEqual((progress.done, progress.total), (1, 2))
        self.assertEqual(progress.percent, 50)

    def test_percent_is_rounded(self) -> None:
        self.assertEqual(parse_milestones("- [x] a\n- [ ] b\n- [ ] c").percent, 33)

    def test_groups_by_heading(self) -> None:
        text = "### 阶段一：基础\n- [x] a\n\n### 阶段二：进阶\n- [ ] b\n- [ ] c"
        progress = parse_milestones(text)
        self.assertEqual([s.name for s in progress.stages], ["阶段一：基础", "阶段二：进阶"])
        self.assertEqual(len(progress.stages[1].milestones), 2)

    def test_milestones_before_any_heading(self) -> None:
        progress = parse_milestones("- [ ] 先做这个")
        self.assertEqual(progress.stages[0].name, UNGROUPED)

    def test_accepts_common_checkbox_styles(self) -> None:
        text = "* [X] 大写 X\n- [✓] 对勾\n* [ ] 星号开头"
        progress = parse_milestones(text)
        self.assertEqual((progress.done, progress.total), (2, 3))

    def test_spaces_inside_brackets(self) -> None:
        progress = parse_milestones("- [ x ] 括号里有空格")
        self.assertEqual((progress.done, progress.total), (1, 1))

    def test_broken_checkboxes_are_skipped_not_fatal(self) -> None:
        text = "- [] 空的括号\n- [x 少了右括号\n- 普通列表项\n- [ ] 正常的"
        progress = parse_milestones(text)
        self.assertEqual((progress.done, progress.total), (0, 1))
        self.assertEqual(progress.stages[0].milestones[0].text, "正常的")

    def test_prose_between_milestones_is_ignored(self) -> None:
        text = (
            "这一阶段的目标是先跑通最小闭环。\n\n"
            "- [x] a\n\n"
            "> 提示：别急着上框架。\n\n"
            "- [ ] b\n"
        )
        progress = parse_milestones(text)
        self.assertEqual((progress.done, progress.total), (1, 2))

    def test_text_without_checkboxes(self) -> None:
        progress = parse_milestones("（还没有阶段。聊清楚方向之后再说。）")
        self.assertEqual(progress.total, 0)


if __name__ == "__main__":
    unittest.main()
