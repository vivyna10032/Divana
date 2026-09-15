"""instructions 拼装的离线测试。不联网、不碰模型。

用法：python -m unittest tests.test_prompt -v
"""

from __future__ import annotations

import unittest
from datetime import date

from divana.prompt import (
    compose_instructions,
    format_today,
    load_persona,
    load_summarizer,
)


class FormatTodayTest(unittest.TestCase):
    def test_sunday(self) -> None:
        # 2026-09-13 是周日
        self.assertEqual(format_today(date(2026, 9, 13)), "2026-09-13（周日）")

    def test_monday(self) -> None:
        self.assertEqual(format_today(date(2026, 9, 14)), "2026-09-14（周一）")


class ComposeInstructionsTest(unittest.TestCase):
    def compose(self, persona: str = "人格内容", profile: str = "画像内容") -> str:
        return compose_instructions(persona, profile, date(2026, 9, 13))

    def test_contains_persona_date_and_profile(self) -> None:
        text = self.compose()
        self.assertIn("人格内容", text)
        self.assertIn("2026-09-13（周日）", text)
        self.assertIn("画像内容", text)

    def test_tells_the_model_it_has_no_clock(self) -> None:
        # 这是防"模型自己编日期"的关键一句，掉了这个测试就是提醒
        self.assertIn("看不到时钟", self.compose())

    def test_order_is_persona_then_date_then_profile(self) -> None:
        text = self.compose()
        self.assertLess(text.index("人格内容"), text.index("现在的时间"))
        self.assertLess(text.index("现在的时间"), text.index("学习者画像"))

    def test_empty_profile_does_not_break(self) -> None:
        self.assertIn("学习者画像", compose_instructions("人格", "", date(2026, 9, 13)))

    def test_defaults_to_today(self) -> None:
        self.assertIn(date.today().isoformat(), compose_instructions("人格", "画像"))


class PersonaTest(unittest.TestCase):
    def test_persona_file_is_loadable(self) -> None:
        self.assertIn("Divana", load_persona())

    def test_summarizer_prompt_is_loadable(self) -> None:
        text = load_summarizer()
        self.assertIn("标题", text)
        self.assertIn("只写记录里真实出现过的内容", text)
        # 这条是防"模型自己加一级标题"的约定，掉了要有人发现
        self.assertIn("不要再写一级标题", text)


if __name__ == "__main__":
    unittest.main()
