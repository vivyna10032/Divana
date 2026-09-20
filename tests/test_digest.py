"""早报的纯函数测试：拼简报、存文件、列列表、读一期。不联网、不碰模型。

用法：python -m unittest tests.test_digest -v
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from divana.digest import (
    DigestError,
    compose_brief,
    list_digests,
    mark_digest_done,
    read_digest,
    save_digest,
)
from divana.state import DIGEST_ERROR, LAST_DIGEST, read_state


class ComposeBriefTest(unittest.TestCase):
    def brief(self, **overrides: str) -> str:
        base = {
            "today_text": "2026-09-21（周一）",
            "goal": "找 agent 实习",
            "level": "大三",
            "weak": "分布式系统",
            "plan_now": "在做 Divana",
            "plan_next": "搞懂 agent loop",
            "recent_titles": "- 2026-09-20　早报：框架对比",
        }
        return compose_brief(**(base | overrides))

    def test_carries_the_direction(self) -> None:
        text = self.brief()
        for piece in ("2026-09-21", "找 agent 实习", "大三", "分布式系统", "在做 Divana", "搞懂 agent loop"):
            self.assertIn(piece, text)

    def test_empty_pieces_are_marked(self) -> None:
        text = self.brief(weak="", plan_next="")
        self.assertIn("薄弱点：（空）", text)
        self.assertIn("下一步：（空）", text)

    def test_first_issue_says_so(self) -> None:
        self.assertIn("这是第一期", self.brief(recent_titles=""))

    def test_lists_recent_titles_to_avoid_repeats(self) -> None:
        self.assertIn("框架对比", self.brief())


class DigestStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "digest"

    def test_missing_folder_lists_nothing(self) -> None:
        self.assertEqual(list_digests(self.dir), [])

    def test_save_uses_the_date_as_filename(self) -> None:
        path = save_digest("## 2026-09-21 早报\n\n内容", today=date(2026, 9, 21), digest_dir=self.dir)
        self.assertEqual(path.name, "2026-09-21.md")
        self.assertIn("内容", path.read_text(encoding="utf-8"))

    def test_saving_twice_the_same_day_overwrites(self) -> None:
        save_digest("## 第一次\n", today=date(2026, 9, 21), digest_dir=self.dir)
        save_digest("## 第二次\n", today=date(2026, 9, 21), digest_dir=self.dir)
        self.assertEqual(len(list_digests(self.dir)), 1)
        self.assertIn("第二次", (self.dir / "2026-09-21.md").read_text(encoding="utf-8"))

    def test_list_is_newest_first_and_reads_the_title(self) -> None:
        save_digest("## 2026-09-20 早报\n\n甲", today=date(2026, 9, 20), digest_dir=self.dir)
        save_digest("## 2026-09-21 早报\n\n乙", today=date(2026, 9, 21), digest_dir=self.dir)

        infos = list_digests(self.dir)
        self.assertEqual([info.date for info in infos], ["2026-09-21", "2026-09-20"])
        self.assertEqual(infos[0].title, "2026-09-21 早报")

    def test_title_falls_back_to_the_date(self) -> None:
        save_digest("今天没什么可报的", today=date(2026, 9, 21), digest_dir=self.dir)
        self.assertEqual(list_digests(self.dir)[0].title, "2026-09-21")

    def test_read_by_date_or_file_name(self) -> None:
        save_digest("## 2026-09-21 早报\n\n正文", today=date(2026, 9, 21), digest_dir=self.dir)

        info, text = read_digest("2026-09-21", self.dir)
        self.assertEqual(info.date, "2026-09-21")
        self.assertIn("正文", text)
        self.assertEqual(read_digest("2026-09-21.md", self.dir)[1], text)

    def test_read_unknown_raises(self) -> None:
        save_digest("x", today=date(2026, 9, 21), digest_dir=self.dir)
        with self.assertRaises(DigestError):
            read_digest("2020-01-01", self.dir)

    def test_empty_name_raises(self) -> None:
        with self.assertRaises(DigestError):
            read_digest("   ", self.dir)

    def test_cannot_read_outside_the_folder(self) -> None:
        outside = Path(self._tmp.name) / "secret.md"
        outside.write_text("不该读到", encoding="utf-8")
        save_digest("x", today=date(2026, 9, 21), digest_dir=self.dir)

        for bad in ("../secret.md", "..\\secret.md", str(outside)):
            with self.assertRaises(DigestError):
                read_digest(bad, self.dir)


class MarkDigestDoneTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name) / "state.json"

    def test_records_the_date(self) -> None:
        mark_digest_done(today=date(2026, 9, 21), state_path=self.state)
        self.assertEqual(read_state(self.state)[LAST_DIGEST], "2026-09-21")

    def test_clears_an_earlier_error(self) -> None:
        from divana.state import update_state

        update_state(self.state, **{DIGEST_ERROR: "上次失败了"})
        mark_digest_done(today=date(2026, 9, 21), state_path=self.state)
        self.assertEqual(read_state(self.state)[DIGEST_ERROR], "")


if __name__ == "__main__":
    unittest.main()
