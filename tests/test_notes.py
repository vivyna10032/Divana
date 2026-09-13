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


class NoteStoreListTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.notes_dir = Path(self._tmp.name) / "vault" / "notes"
        self.store = NoteStore(self.notes_dir)
        self.today = date(2026, 9, 13)

    def test_missing_dir_lists_nothing(self) -> None:
        self.assertEqual(self.store.list_all(), [])

    def test_newest_first(self) -> None:
        self.store.save("旧笔记", "旧的。", today=date(2026, 9, 1))
        self.store.save("新笔记", "新的。", today=date(2026, 9, 13))

        titles = [info.title for info in self.store.list_all()]
        self.assertEqual(titles, ["新笔记", "旧笔记"])

    def test_reads_front_matter(self) -> None:
        self.store.save("注意力机制", "正文。", ["transformer"], today=self.today)

        info = self.store.list_all()[0]
        self.assertEqual(info.title, "注意力机制")
        self.assertEqual(info.date, "2026-09-13")
        self.assertEqual(info.tags, ("transformer",))

    def test_handwritten_file_without_front_matter_still_works(self) -> None:
        self.notes_dir.mkdir(parents=True)
        (self.notes_dir / "2026-09-10-手写的.md").write_text(
            "# 手写的笔记\n\n正文在这里。\n", encoding="utf-8"
        )

        info = self.store.list_all()[0]
        self.assertEqual(info.title, "手写的笔记")
        self.assertEqual(info.date, "2026-09-10")
        self.assertEqual(info.tags, ())

    def test_file_without_heading_falls_back_to_file_name(self) -> None:
        self.notes_dir.mkdir(parents=True)
        (self.notes_dir / "2026-09-10-光秃秃.md").write_text("没有标题。\n", encoding="utf-8")

        self.assertEqual(self.store.list_all()[0].title, "2026-09-10-光秃秃")


class NoteStoreSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.notes_dir = Path(self._tmp.name) / "vault" / "notes"
        self.store = NoteStore(self.notes_dir)
        self.store.save(
            "注意力机制",
            "## 结论\n\n让模型自己决定看哪里。\n",
            ["transformer"],
            today=date(2026, 9, 13),
        )
        self.store.save(
            "梯度下降",
            "## 结论\n\n沿着梯度反方向走一步。\n",
            ["optimizer"],
            today=date(2026, 9, 1),
        )

    def test_matches_title(self) -> None:
        hits = self.store.search("注意力")
        self.assertEqual([info.title for info, _ in hits], ["注意力机制"])

    def test_matches_tag(self) -> None:
        hits = self.store.search("optimizer")
        self.assertEqual([info.title for info, _ in hits], ["梯度下降"])

    def test_matches_body(self) -> None:
        hits = self.store.search("反方向")
        self.assertEqual([info.title for info, _ in hits], ["梯度下降"])

    def test_title_beats_body(self) -> None:
        # "梯度"既出现在标题里也出现在另一篇的正文里时，标题命中要排在前面
        self.store.save("反向传播", "梯度是核心。", today=date(2026, 9, 2))
        hits = self.store.search("梯度")
        self.assertEqual([info.title for info, _ in hits], ["梯度下降", "反向传播"])

    def test_star_lists_newest_first(self) -> None:
        hits = self.store.search("*")
        self.assertEqual([info.title for info, _ in hits], ["注意力机制", "梯度下降"])

    def test_no_match_returns_empty(self) -> None:
        self.assertEqual(self.store.search("量子纠缠"), [])

    def test_limit_is_respected(self) -> None:
        self.assertEqual(len(self.store.search("*", limit=1)), 1)

    def test_snippet_prefers_the_matching_line(self) -> None:
        hits = self.store.search("反方向")
        self.assertIn("反方向", hits[0][1])


class NoteStoreReadTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.notes_dir = Path(self._tmp.name) / "vault" / "notes"
        self.store = NoteStore(self.notes_dir)
        self.store.save("注意力机制", "正文甲。", today=date(2026, 9, 13))
        self.store.save("梯度下降", "正文乙。", today=date(2026, 9, 1))

    def test_read_by_file_name(self) -> None:
        info = self.store.read("2026-09-13-注意力机制.md")
        self.assertEqual(info.title, "注意力机制")
        self.assertIn("正文甲。", info.body)

    def test_read_by_title(self) -> None:
        self.assertEqual(self.store.read("梯度下降").title, "梯度下降")

    def test_read_by_unique_partial(self) -> None:
        self.assertEqual(self.store.read("注意力").title, "注意力机制")

    def test_read_is_case_insensitive(self) -> None:
        self.store.save("LoRA 微调", "正文丙。", today=date(2026, 9, 13))
        self.assertEqual(self.store.read("LORA 微调").title, "LoRA 微调")

    def test_empty_name_is_rejected(self) -> None:
        with self.assertRaises(NoteError):
            self.store.read("   ")

    def test_missing_name_is_rejected(self) -> None:
        with self.assertRaises(NoteError):
            self.store.read("不存在的笔记")

    def test_ambiguous_match_is_rejected(self) -> None:
        self.store.save("注意力机制补充", "正文丁。", today=date(2026, 9, 13))
        with self.assertRaises(NoteError) as ctx:
            self.store.read("注意力")
        self.assertIn("多篇", str(ctx.exception))

    def test_cannot_read_outside_the_notes_dir(self) -> None:
        """路径穿越在"只做匹配、不拼路径"的设计下天然无效。"""
        outside = Path(self._tmp.name) / "secret.md"
        outside.write_text("不该被读到的内容", encoding="utf-8")

        for name in ("../secret.md", "..\\secret.md", "../../.env", str(outside)):
            with self.assertRaises(NoteError):
                self.store.read(name)


if __name__ == "__main__":
    unittest.main()
