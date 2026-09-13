"""笔记落盘的离线测试：不联网、不碰模型。

用法：python -m unittest tests.test_notes -v
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from divana.notes import (
    MAX_TAGS,
    NoteError,
    NoteStore,
    normalize_tags,
    slugify,
)


class SlugifyTest(unittest.TestCase):
    def test_keeps_chinese(self) -> None:
        self.assertEqual(slugify("注意力机制"), "注意力机制")

    def test_replaces_forbidden_characters(self) -> None:
        self.assertNotIn("/", slugify("a/b:c*d?e"))
        self.assertNotIn(":", slugify("a/b:c*d?e"))

    def test_collapses_whitespace(self) -> None:
        self.assertEqual(slugify("LoRA   微调   入门"), "LoRA-微调-入门")

    def test_strips_leading_and_trailing_punctuation(self) -> None:
        self.assertEqual(slugify("  ..注意力机制.  "), "注意力机制")

    def test_truncates_long_titles(self) -> None:
        self.assertLessEqual(len(slugify("概" * 200)), 40)

    def test_falls_back_when_nothing_is_left(self) -> None:
        self.assertEqual(slugify("   "), "note")
        self.assertEqual(slugify("///"), "note")

    def test_avoids_windows_reserved_names(self) -> None:
        self.assertEqual(slugify("CON"), "note")
        self.assertEqual(slugify("nul"), "note")
        self.assertEqual(slugify("COM1"), "note")


class NormalizeTagsTest(unittest.TestCase):
    def test_strips_hashes_and_duplicates(self) -> None:
        self.assertEqual(
            normalize_tags([" #attention ", "attention", "transformer"]),
            ("attention", "transformer"),
        )

    def test_limits_count(self) -> None:
        self.assertEqual(len(normalize_tags([f"t{i}" for i in range(20)])), MAX_TAGS)

    def test_handles_none(self) -> None:
        self.assertEqual(normalize_tags(None), ())


class NoteStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.notes_dir = Path(self._tmp.name) / "vault" / "notes"
        self.today = date(2026, 9, 13)

    def store(self) -> NoteStore:
        return NoteStore(self.notes_dir)

    def test_save_writes_a_file_named_after_date_and_title(self) -> None:
        note = self.store().save("注意力机制", "一句话：让模型自己决定看哪里。", today=self.today)

        self.assertEqual(note.path.name, "2026-09-13-注意力机制.md")
        self.assertTrue(note.path.exists())

    def test_saved_file_has_front_matter_and_body(self) -> None:
        note = self.store().save(
            "注意力机制",
            "正文内容。",
            ["transformer", "attention"],
            today=self.today,
        )
        text = note.path.read_text(encoding="utf-8")

        self.assertTrue(text.startswith("---\n"))
        self.assertIn("title: 注意力机制", text)
        self.assertIn("date: 2026-09-13", text)
        self.assertIn("tags: [transformer, attention]", text)
        self.assertIn("# 注意力机制", text)
        self.assertIn("正文内容。", text)

    def test_empty_tags_render_as_empty_list(self) -> None:
        note = self.store().save("概念", "正文。", [], today=self.today)
        self.assertIn("tags: []", note.path.read_text(encoding="utf-8"))

    def test_second_note_with_same_title_does_not_overwrite(self) -> None:
        store = self.store()
        first = store.save("梯度下降", "第一版。", today=self.today)
        second = store.save("梯度下降", "第二版。", today=self.today)

        self.assertNotEqual(first.path, second.path)
        self.assertEqual(second.path.name, "2026-09-13-梯度下降-2.md")
        self.assertIn("第一版。", first.path.read_text(encoding="utf-8"))

    def test_creates_missing_directories(self) -> None:
        self.assertFalse(self.notes_dir.exists())
        self.store().save("概念", "正文。", today=self.today)
        self.assertTrue(self.notes_dir.is_dir())

    def test_rejects_empty_title_or_body(self) -> None:
        with self.assertRaises(NoteError):
            self.store().save("   ", "正文。", today=self.today)
        with self.assertRaises(NoteError):
            self.store().save("标题", "   ", today=self.today)

    def test_rejects_overlong_body(self) -> None:
        with self.assertRaises(NoteError):
            self.store().save("标题", "字" * 9000, today=self.today)

    def test_leaves_no_temp_file_behind(self) -> None:
        store = self.store()
        store.save("概念", "正文。", today=self.today)

        leftovers = sorted(p.name for p in self.notes_dir.iterdir())
        self.assertEqual(leftovers, ["2026-09-13-概念.md"])


if __name__ == "__main__":
    unittest.main()
