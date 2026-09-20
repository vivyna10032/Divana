"""回收站的读写测试。用临时目录，不碰 data/trash。

用法：python -m unittest tests.test_trash -v
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from divana.trash import trash_file, trash_text

MOMENT = datetime(2026, 9, 20, 15, 30, 12)


class TrashTextTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.trash = Path(self._tmp.name) / "trash"

    def test_writes_the_text_with_a_source_header(self) -> None:
        saved = trash_text("review", "2026-09-20 复盘", "## 内容\n\n正文", now=MOMENT, trash_dir=self.trash)

        text = saved.read_text(encoding="utf-8")
        self.assertIn("回收站：2026-09-20 复盘", text)
        self.assertIn("类型：review", text)
        self.assertIn("2026-09-20 15:30:12", text)
        self.assertIn("正文", text)

    def test_filename_carries_the_kind_and_time(self) -> None:
        saved = trash_text("stage", "阶段一：基础", "x", now=MOMENT, trash_dir=self.trash)
        self.assertTrue(saved.name.startswith("20260920-153012-stage-"))
        self.assertTrue(saved.name.endswith(".md"))

    def test_same_second_does_not_overwrite(self) -> None:
        first = trash_text("review", "同名", "第一次", now=MOMENT, trash_dir=self.trash)
        second = trash_text("review", "同名", "第二次", now=MOMENT, trash_dir=self.trash)

        self.assertNotEqual(first, second)
        self.assertIn("第一次", first.read_text(encoding="utf-8"))
        self.assertIn("第二次", second.read_text(encoding="utf-8"))

    def test_name_is_slugified_for_the_filename(self) -> None:
        saved = trash_text("note", "a/b:c*d", "x", now=MOMENT, trash_dir=self.trash)
        self.assertNotIn("/", saved.name)
        self.assertNotIn(":", saved.name)


class TrashFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.trash = self.root / "trash"

    def test_moves_instead_of_deleting(self) -> None:
        source = self.root / "2026-09-13-注意力机制.md"
        source.write_text("正文", encoding="utf-8")

        saved = trash_file(source, "note", now=MOMENT, trash_dir=self.trash)

        self.assertFalse(source.exists())  # 原位置没了
        self.assertTrue(saved.exists())  # 但东西还在（这就是回收站的意义）
        self.assertEqual(saved.read_text(encoding="utf-8"), "正文")
        self.assertIn("注意力机制", saved.name)


if __name__ == "__main__":
    unittest.main()
