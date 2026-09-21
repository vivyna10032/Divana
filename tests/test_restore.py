"""从回收站恢复的测试。用临时目录，不碰真实文件。

用法：python -m unittest tests.test_restore -v
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from divana.restore import (
    RestoreError,
    list_trash,
    parse_trash,
    restore,
    restore_note,
    restore_session,
)
from divana.session import MESSAGES_TABLE, SESSIONS_TABLE, session_messages
from divana.trash import trash_file, trash_text

MOMENT = datetime(2026, 9, 20, 19, 49, 47)

SCHEMA = """
CREATE TABLE agent_sessions (
    session_id TEXT PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE agent_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    message_data TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class ParseTrashTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.trash = Path(self._tmp.name) / "trash"

    def test_reads_kind_name_and_body(self) -> None:
        saved = trash_text("session", "default", "[2026-09-13 18:06] 你：你好", now=MOMENT, trash_dir=self.trash)
        item = parse_trash(saved)

        self.assertEqual(item.kind, "session")
        self.assertEqual(item.name, "default")
        self.assertIn("你好", item.body)

    def test_missing_file_raises(self) -> None:
        with self.assertRaises(RestoreError):
            parse_trash(self.trash / "没有这个.md")

    def test_list_is_newest_first(self) -> None:
        trash_text("session", "旧的", "x", now=datetime(2026, 9, 19, 10, 0), trash_dir=self.trash)
        trash_text("session", "新的", "x", now=datetime(2026, 9, 20, 10, 0), trash_dir=self.trash)

        names = [item.name for item in list_trash(self.trash)]
        self.assertEqual(names, ["新的", "旧的"])

    def test_empty_trash(self) -> None:
        self.assertEqual(list_trash(self.trash), [])


class RestoreNoteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.notes = self.root / "notes"
        self.trash = self.root / "trash"
        self.notes.mkdir()

    def test_moves_the_file_back_with_its_original_name(self) -> None:
        original = self.notes / "2026-09-13-注意力机制.md"
        original.write_text("正文", encoding="utf-8")
        saved = trash_file(original, "note", now=MOMENT, trash_dir=self.trash)

        target = restore_note(parse_trash(saved), notes_dir=self.notes)

        self.assertEqual(target.name, "2026-09-13-注意力机制.md")
        self.assertEqual(target.read_text(encoding="utf-8"), "正文")
        self.assertFalse(saved.exists())  # 回收站里那份移走了

    def test_does_not_overwrite_an_existing_note(self) -> None:
        original = self.notes / "笔记.md"
        original.write_text("旧", encoding="utf-8")
        saved = trash_file(original, "note", now=MOMENT, trash_dir=self.trash)
        (self.notes / "笔记.md").write_text("新写的", encoding="utf-8")  # 期间又写了一篇同名的

        target = restore_note(parse_trash(saved), notes_dir=self.notes)

        self.assertEqual(target.name, "笔记-2.md")
        self.assertEqual((self.notes / "笔记.md").read_text(encoding="utf-8"), "新写的")

    def test_wrong_kind_raises(self) -> None:
        saved = trash_text("session", "x", "内容", now=MOMENT, trash_dir=self.trash)
        with self.assertRaises(RestoreError):
            restore_note(parse_trash(saved), notes_dir=self.notes)


class RestoreSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "divana.db"
        self.conn = sqlite3.connect(str(self.db))
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.conn.close()
        self.trash = Path(self._tmp.name) / "trash"

    def exported(self) -> Path:
        body = (
            "[2026-09-13 18:06] 你：你好\n"
            "[2026-09-13 18:07] Divana：你好，我是 Divana\n"
        )
        return trash_text("session", "default", body, now=MOMENT, trash_dir=self.trash)

    def test_writes_the_conversation_back(self) -> None:
        session_id, count = restore_session(parse_trash(self.exported()), db_path=self.db)

        self.assertEqual(session_id, "default")
        self.assertEqual(count, 2)
        messages = session_messages("default", db_path=self.db)
        self.assertEqual([m.text for m in messages], ["你好", "你好，我是 Divana"])
        self.assertEqual([m.role for m in messages], ["user", "assistant"])

    def test_session_row_is_created(self) -> None:
        restore_session(parse_trash(self.exported()), db_path=self.db)
        conn = sqlite3.connect(str(self.db))
        try:
            rows = conn.execute(f"SELECT session_id FROM {SESSIONS_TABLE}").fetchall()
        finally:
            conn.close()
        self.assertEqual(rows, [("default",)])

    def test_can_restore_under_a_new_name(self) -> None:
        session_id, _ = restore_session(
            parse_trash(self.exported()), db_path=self.db, session_id="restored-1"
        )
        self.assertEqual(session_id, "restored-1")
        self.assertEqual(len(session_messages("restored-1", db_path=self.db)), 2)

    def test_empty_backup_raises(self) -> None:
        saved = trash_text("session", "空的", "（这个会话里没有对话内容）", now=MOMENT, trash_dir=self.trash)
        with self.assertRaises(RestoreError):
            restore_session(parse_trash(saved), db_path=self.db)

    def test_wrong_kind_raises(self) -> None:
        saved = trash_text("review", "复盘", "内容", now=MOMENT, trash_dir=self.trash)
        with self.assertRaises(RestoreError):
            restore_session(parse_trash(saved), db_path=self.db)

    def test_backup_moves_out_of_the_way_after_restoring(self) -> None:
        """恢复完再把原件留在待恢复列表里，点两次就变成两份了。"""
        saved = self.exported()
        restore_session(parse_trash(saved), db_path=self.db)

        self.assertEqual(list_trash(self.trash), [])          # 列表里没有了
        self.assertTrue((self.trash / "restored" / saved.name).exists())  # 但也没删


class RestoreDispatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.trash = Path(self._tmp.name) / "trash"

    def test_unsupported_kind_says_what_to_do(self) -> None:
        saved = trash_text("review", "2026-09-20 复盘", "正文", now=MOMENT, trash_dir=self.trash)
        with self.assertRaises(RestoreError) as ctx:
            restore(parse_trash(saved))
        self.assertIn("复制粘贴", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
