"""跨运行状态的读写测试。用临时文件，不碰 data/state.json。

用法：python -m unittest tests.test_state -v
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from divana.state import (
    LAST_ERROR,
    LAST_REVIEW,
    last_review_date,
    read_state,
    update_state,
    write_state,
)


class StateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "state.json"

    def test_missing_file_is_empty_state(self) -> None:
        self.assertEqual(read_state(self.path), {})

    def test_broken_json_is_empty_state(self) -> None:
        self.path.write_text("{不是 json", encoding="utf-8")
        self.assertEqual(read_state(self.path), {})

    def test_json_that_is_not_an_object_is_empty_state(self) -> None:
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(read_state(self.path), {})

    def test_roundtrip(self) -> None:
        write_state({"a": 1, "中文": "值"}, self.path)
        self.assertEqual(read_state(self.path), {"a": 1, "中文": "值"})

    def test_update_merges_instead_of_overwriting(self) -> None:
        write_state({"keep": "我"}, self.path)
        update_state(self.path, added="新")
        self.assertEqual(read_state(self.path), {"keep": "我", "added": "新"})

    def test_update_state_creates_the_parent_directory(self) -> None:
        nested = Path(self._tmp.name) / "data" / "state.json"
        update_state(nested, x=1)
        self.assertTrue(nested.exists())

    def test_leaves_no_temp_file_behind(self) -> None:
        update_state(self.path, x=1)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["state.json"])

    def test_last_review_date_parses(self) -> None:
        write_state({LAST_REVIEW: "2026-09-20"}, self.path)
        self.assertEqual(last_review_date(self.path), date(2026, 9, 20))

    def test_last_review_date_handles_garbage(self) -> None:
        write_state({LAST_REVIEW: "上周三"}, self.path)
        self.assertIsNone(last_review_date(self.path))

    def test_last_review_date_when_never(self) -> None:
        self.assertIsNone(last_review_date(self.path))

    def test_error_key_roundtrip(self) -> None:
        update_state(self.path, **{LAST_ERROR: "RateLimit: 太多请求"})
        self.assertEqual(read_state(self.path)[LAST_ERROR], "RateLimit: 太多请求")


if __name__ == "__main__":
    unittest.main()
