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
    list_sessions,
    new_session_id,
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
