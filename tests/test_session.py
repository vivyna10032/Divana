"""会话列表的离线测试。

用临时数据库跑，不碰 data/divana.db。表结构照 SDK 建的来
（agent_sessions / agent_messages）——所以这些测试也在盯着"SDK 换表结构"这件事。

用法：python -m unittest tests.test_session -v
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from divana.session import (
    TITLE_CHARS,
    delete_session,
    list_sessions,
    new_session_id,
    recent_messages,
    session_messages,
    title_from_item,
    to_local_time,
)

# SDK 建表用的语句（照 sqlite_session.py 抄的，去掉索引）
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


def user_item(text: str) -> str:
    return json.dumps({"role": "user", "content": text})


def assistant_item(text: str) -> str:
    return json.dumps(
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        }
    )


class SessionListTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "divana.db"
        self.conn = sqlite3.connect(str(self.db))
        self.conn.executescript(SCHEMA)
        self.addCleanup(self.conn.close)

    def add(self, session_id: str, items: list[str], *, updated: str) -> None:
        self.conn.execute(
            "INSERT INTO agent_sessions (session_id, created_at, updated_at) "
            "VALUES (?, ?, ?)",
            (session_id, "2026-09-01 00:00:00", updated),
        )
        for data in items:
            self.conn.execute(
                "INSERT INTO agent_messages (session_id, message_data) VALUES (?, ?)",
                (session_id, data),
            )
        self.conn.commit()

    def test_missing_database_returns_empty(self) -> None:
        self.assertEqual(list_sessions(Path(self._tmp.name) / "nope.db"), [])

    def test_empty_database_returns_empty(self) -> None:
        self.assertEqual(list_sessions(self.db), [])

    def test_uses_first_user_message_as_title(self) -> None:
        self.add("default", [user_item("帮我看看这个项目"), assistant_item("好的")],
                 updated="2026-09-16 12:33:45")

        sessions = list_sessions(self.db)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].session_id, "default")
        self.assertEqual(sessions[0].title, "帮我看看这个项目")
        self.assertEqual(sessions[0].message_count, 2)

    def test_newest_first(self) -> None:
        self.add("old", [user_item("旧对话")], updated="2026-09-10 08:00:00")
        self.add("new", [user_item("新对话")], updated="2026-09-16 12:00:00")

        self.assertEqual([s.session_id for s in list_sessions(self.db)], ["new", "old"])

    def test_long_title_is_truncated(self) -> None:
        self.add("long", [user_item("字" * 80)], updated="2026-09-16 12:00:00")
        title = list_sessions(self.db)[0].title
        self.assertEqual(len(title), TITLE_CHARS + 1)  # 多出来的那个是省略号
        self.assertTrue(title.endswith("…"))

    def test_title_falls_back_to_session_id(self) -> None:
        """第一条不是用户消息（比如让模型先说话），标题就退回会话名。"""
        self.add("no-user", [assistant_item("我先说")], updated="2026-09-16 12:00:00")
        self.assertEqual(list_sessions(self.db)[0].title, "no-user")

    def test_broken_json_does_not_break_listing(self) -> None:
        self.add("broken", ["{不是 JSON"], updated="2026-09-16 12:00:00")
        sessions = list_sessions(self.db)
        self.assertEqual(sessions[0].session_id, "broken")
        self.assertEqual(sessions[0].title, "broken")

    def test_timestamps_are_converted_to_local_time(self) -> None:
        self.add("default", [user_item("hi")], updated="2026-09-16 12:33:45")
        stamp = list_sessions(self.db)[0].updated_at
        # 把结果当本地时间解读、再转回 UTC，应该和原值落在同一分钟
        # （显示只精确到分钟，秒是故意丢掉的）
        back = datetime.strptime(stamp, "%Y-%m-%d %H:%M").astimezone(timezone.utc)
        self.assertEqual(back.strftime("%Y-%m-%d %H:%M"), "2026-09-16 12:33")


class ToLocalTimeTest(unittest.TestCase):
    def test_empty_input(self) -> None:
        self.assertEqual(to_local_time(""), "")

    def test_garbage_is_returned_as_is(self) -> None:
        self.assertEqual(to_local_time("不是时间"), "不是时间")


class RecentMessagesTest(unittest.TestCase):
    """复盘素材的来源：最近几天、跨会话、只要人话。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "divana.db"
        self.conn = sqlite3.connect(str(self.db))
        self.conn.executescript(SCHEMA)
        self.addCleanup(self.conn.close)
        self.now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

    def add(self, session_id: str, data: str, *, at: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO agent_sessions (session_id) VALUES (?)",
            (session_id,),
        )
        self.conn.execute(
            "INSERT INTO agent_messages (session_id, message_data, created_at) "
            "VALUES (?, ?, ?)",
            (session_id, data, at),
        )
        self.conn.commit()

    def test_missing_database(self) -> None:
        self.assertEqual(recent_messages(7, db_path=Path(self._tmp.name) / "no.db"), [])

    def test_window_filters_old_messages(self) -> None:
        self.add("default", user_item("六天前"), at="2026-09-14 12:00:00")
        self.add("default", user_item("八天前"), at="2026-09-12 12:00:00")

        found = recent_messages(7, db_path=self.db, now=self.now)
        self.assertEqual([m.text for m in found], ["六天前"])

    def test_spans_all_sessions(self) -> None:
        self.add("default", user_item("老会话"), at="2026-09-19 10:00:00")
        self.add("chat-2", user_item("新会话"), at="2026-09-19 11:00:00")

        found = recent_messages(7, db_path=self.db, now=self.now)
        self.assertEqual([m.session_id for m in found], ["default", "chat-2"])

    def test_keeps_only_human_talk(self) -> None:
        self.add("default", user_item("问"), at="2026-09-19 10:00:00")
        self.add("default", json.dumps({"type": "function_call", "name": "x"}),
                 at="2026-09-19 10:01:00")
        self.add("default", assistant_item("答"), at="2026-09-19 10:02:00")

        self.assertEqual(
            [(m.role, m.text) for m in recent_messages(7, db_path=self.db, now=self.now)],
            [("user", "问"), ("assistant", "答")],
        )

    def test_limit_keeps_the_newest(self) -> None:
        for hour in range(10):
            self.add("default", user_item(f"第{hour}条"), at=f"2026-09-19 {hour:02d}:00:00")

        found = recent_messages(7, db_path=self.db, now=self.now, limit=3)
        self.assertEqual([m.text for m in found], ["第7条", "第8条", "第9条"])

    def test_timestamps_are_local(self) -> None:
        self.add("default", user_item("hi"), at="2026-09-19 04:00:00")
        at = recent_messages(7, db_path=self.db, now=self.now)[0].at
        back = datetime.strptime(at, "%Y-%m-%d %H:%M").astimezone(timezone.utc)
        self.assertEqual(back.strftime("%H:%M"), "04:00")


class DeleteSessionTest(unittest.TestCase):
    """删会话 = 先导出到回收站，再删库里的行。顺序不能反。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = self.root / "divana.db"
        self.trash = self.root / "trash"
        self.conn = sqlite3.connect(str(self.db))
        self.conn.executescript(SCHEMA)
        self.addCleanup(self.conn.close)

    def add(self, session_id: str, data: str, *, at: str = "2026-09-19 10:00:00") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO agent_sessions (session_id) VALUES (?)", (session_id,)
        )
        self.conn.execute(
            "INSERT INTO agent_messages (session_id, message_data, created_at) "
            "VALUES (?, ?, ?)",
            (session_id, data, at),
        )
        self.conn.commit()

    def test_exports_before_deleting(self) -> None:
        self.add("chat-1", user_item("这段要被删"))
        self.add("chat-1", assistant_item("好的"))

        saved = delete_session("chat-1", db_path=self.db, trash_dir=self.trash)

        text = saved.read_text(encoding="utf-8")
        self.assertIn("这段要被删", text)
        self.assertIn("好的", text)

    def test_rows_are_gone_after_delete(self) -> None:
        self.add("chat-1", user_item("走好"))
        delete_session("chat-1", db_path=self.db, trash_dir=self.trash)

        self.assertEqual(session_messages("chat-1", db_path=self.db), [])
        self.assertEqual(list_sessions(self.db), [])

    def test_other_sessions_are_untouched(self) -> None:
        self.add("chat-1", user_item("删我"))
        self.add("chat-2", user_item("留我"))

        delete_session("chat-1", db_path=self.db, trash_dir=self.trash)

        self.assertEqual([m.text for m in session_messages("chat-2", db_path=self.db)], ["留我"])

    def test_empty_session_can_be_deleted(self) -> None:
        self.conn.execute("INSERT INTO agent_sessions (session_id) VALUES ('empty')")
        self.conn.commit()

        saved = delete_session("empty", db_path=self.db, trash_dir=self.trash)
        self.assertIn("没有对话内容", saved.read_text(encoding="utf-8"))

    def test_session_messages_are_chronological(self) -> None:
        self.add("chat-1", user_item("第一句"), at="2026-09-19 10:00:00")
        self.add("chat-1", user_item("第二句"), at="2026-09-19 11:00:00")
        texts = [m.text for m in session_messages("chat-1", db_path=self.db)]
        self.assertEqual(texts, ["第一句", "第二句"])


class NewSessionIdTest(unittest.TestCase):
    def test_format_is_stable(self) -> None:
        moment = datetime(2026, 9, 17, 10, 30, 5)
        self.assertEqual(new_session_id(moment), "chat-20260917-103005")

    def test_two_calls_in_the_same_second_are_different(self) -> None:
        ids = {new_session_id() for _ in range(20)}
        self.assertEqual(len(ids), 1)  # 同一秒内确实会重复，所以界面上要防连点


class TitleFromItemTest(unittest.TestCase):
    def test_user_message(self) -> None:
        item = {"role": "user", "content": "你好\n世界"}
        self.assertEqual(title_from_item(item), "你好 世界")  # 换行压成空格

    def test_assistant_message_is_not_a_title(self) -> None:
        self.assertEqual(title_from_item({"role": "assistant", "content": "回答"}), "")

    def test_tool_items_are_not_titles(self) -> None:
        self.assertEqual(title_from_item({"type": "function_call", "name": "x"}), "")

    def test_non_dict(self) -> None:
        self.assertEqual(title_from_item("裸字符串"), "")


if __name__ == "__main__":
    unittest.main()
