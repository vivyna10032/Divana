"""复盘流程里的纯函数测试（拼素材、排文本）。不联网、不碰模型。

用法：python -m unittest tests.test_review -v
"""

from __future__ import annotations

import unittest

from divana.review import MAX_MATERIAL_CHARS, compose_material, render_recent
from divana.session import RecentMessage


def msg(at: str, role: str, text: str, session: str = "default") -> RecentMessage:
    return RecentMessage(session_id=session, at=at, role=role, text=text)


class RenderRecentTest(unittest.TestCase):
    def test_empty_says_so(self) -> None:
        self.assertIn("没有新的对话记录", render_recent([]))

    def test_labels_speakers_and_keeps_order(self) -> None:
        text = render_recent(
            [
                msg("2026-09-20 10:00", "user", "问一句"),
                msg("2026-09-20 10:01", "assistant", "答一句"),
            ]
        )
        self.assertIn("[2026-09-20 10:00] 你：问一句", text)
        self.assertIn("[2026-09-20 10:01] Divana：答一句", text)
        self.assertLess(text.index("问一句"), text.index("答一句"))

    def test_too_long_keeps_the_newest_and_says_what_was_dropped(self) -> None:
        messages = [msg(f"2026-09-{day:02d}", "user", f"第{day}天" + "字" * 200)
                    for day in range(1, 21)]
        text = render_recent(messages, max_chars=600)

        self.assertTrue(text.startswith("（更早的"))
        self.assertIn("第20天", text)
        self.assertNotIn("第1天", text)

    def test_short_conversation_has_no_notice(self) -> None:
        self.assertNotIn("省略", render_recent([msg("2026-09-20", "user", "短")]))

    def test_paragraph_headings_in_content_are_demoted(self) -> None:
        """她回答里的 `## 标题` 要降成三级，免得和素材自己的分节混在一起。"""
        text = render_recent([msg("2026-09-20", "assistant", "## 我的结论\n\n正文")])
        self.assertIn("### 我的结论", text)
        self.assertNotIn("\n## 我的结论", text)


class ComposeMaterialTest(unittest.TestCase):
    def material(self, **overrides: str) -> str:
        base = {
            "days": 7,
            "today": "2026-09-20（周日）",
            "plan_now": "在做 Divana",
            "plan_next": "搞定定时任务",
            "roadmap": "### 阶段一\n- [x] a",
            "profile_goal": "找 agent 实习",
            "profile_level": "大三",
            "profile_weak": "分布式系统",
            "notes": "- 2026-09-18　注意力机制",
            "recent": "[2026-09-20] 你：你好",
        }
        return compose_material(**(base | overrides))

    def test_includes_every_piece(self) -> None:
        text = self.material()
        for piece in ("在做 Divana", "搞定定时任务", "找 agent 实习", "大三",
                      "分布式系统", "注意力机制", "你好"):
            self.assertIn(piece, text)

    def test_carries_the_date_and_window(self) -> None:
        text = self.material()
        self.assertIn("2026-09-20", text)
        self.assertIn("最近 7 天", text)

    def test_empty_pieces_are_marked(self) -> None:
        text = self.material(plan_now="", profile_weak="", notes="")
        self.assertIn("现在的位置：（空）", text)
        self.assertIn("薄弱点：（空）", text)
        self.assertIn("（还没有笔记）", text)

    def test_history_comes_last(self) -> None:
        # 对话摘录最长，放最后；前面的计划/画像才是"框架"
        text = self.material()
        self.assertLess(text.index("学习者画像"), text.index("最近的对话"))


if __name__ == "__main__":
    unittest.main()
