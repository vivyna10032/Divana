"""定时复盘的判定逻辑测试。

不真跑复盘（那要调模型），而是把 run_review 换成假的——这里要验的是
"什么时候该跑""失败了记不记得住"。

用法：python -m unittest tests.test_scheduler -v
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from divana.contracts import Summary
from divana.review import ReviewResult
from divana.scheduler import is_due, review_status, run_due_review
from divana.state import LAST_ERROR, LAST_REVIEW, read_state, write_state

TODAY = date(2026, 9, 20)


class IsDueTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name) / "state.json"

    def test_never_reviewed_is_due(self) -> None:
        self.assertTrue(is_due(today=TODAY, state_path=self.state))

    def test_recent_review_is_not_due(self) -> None:
        write_state({LAST_REVIEW: "2026-09-17"}, self.state)  # 3 天前
        self.assertFalse(is_due(interval_days=7, today=TODAY, state_path=self.state))

    def test_exactly_on_interval_is_due(self) -> None:
        write_state({LAST_REVIEW: "2026-09-13"}, self.state)  # 正好 7 天
        self.assertTrue(is_due(interval_days=7, today=TODAY, state_path=self.state))

    def test_long_overdue_is_due(self) -> None:
        write_state({LAST_REVIEW: "2026-09-01"}, self.state)
        self.assertTrue(is_due(interval_days=7, today=TODAY, state_path=self.state))

    def test_interval_can_be_changed(self) -> None:
        write_state({LAST_REVIEW: "2026-09-17"}, self.state)
        self.assertTrue(is_due(interval_days=3, today=TODAY, state_path=self.state))


class ReviewStatusTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name) / "state.json"

    def test_never_reviewed(self) -> None:
        status = review_status(today=TODAY, state_path=self.state)
        self.assertEqual(status["last"], "")
        self.assertTrue(status["due"])
        self.assertEqual(status["due_in"], 0)

    def test_counts_days_since_and_until(self) -> None:
        write_state({LAST_REVIEW: "2026-09-17"}, self.state)
        status = review_status(interval_days=7, today=TODAY, state_path=self.state)
        self.assertEqual(status["last"], "2026-09-17")
        self.assertEqual(status["days_since"], 3)
        self.assertEqual(status["due_in"], 4)
        self.assertFalse(status["due"])

    def test_surfaces_the_last_error(self) -> None:
        write_state({LAST_REVIEW: "2026-09-17", LAST_ERROR: "RateLimit"}, self.state)
        self.assertEqual(review_status(today=TODAY, state_path=self.state)["last_error"], "RateLimit")


class RunDueReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state = Path(self._tmp.name) / "state.json"
        self.service = SimpleNamespace(settings=SimpleNamespace(model="test"))
        self.settings = self.service.settings

    def test_skips_when_not_due(self) -> None:
        write_state({LAST_REVIEW: "2026-09-19"}, self.state)  # 1 天前
        with patch("divana.scheduler.run_review", new=AsyncMock()) as fake:
            result = run_due_review_sync(self)
        self.assertIsNone(result)
        fake.assert_not_awaited()

    def test_runs_and_reports_when_due(self) -> None:
        result = ReviewResult(markdown="## 复盘", days=7, message_count=42)
        with patch("divana.scheduler.run_review", new=AsyncMock(return_value=result)):
            got = run_due_review_sync(self)
        self.assertEqual(got, result)

    def test_failure_is_recorded_not_raised(self) -> None:
        """后台任务失败了不能让循环死掉——但要留下痕迹。"""
        boom = AsyncMock(side_effect=RuntimeError("模型没返回"))
        with patch("divana.scheduler.run_review", new=boom):
            got = run_due_review_sync(self)

        self.assertIsNone(got)
        self.assertIn("模型没返回", read_state(self.state)[LAST_ERROR])

    def test_failure_does_not_mark_reviewed(self) -> None:
        """失败不能算复盘过——否则要再等一周才会重试。"""
        boom = AsyncMock(side_effect=RuntimeError("失败"))
        with patch("divana.scheduler.run_review", new=boom):
            run_due_review_sync(self)
        self.assertNotIn(LAST_REVIEW, read_state(self.state))


def run_due_review_sync(case: RunDueReviewTest):
    """跑一次 run_due_review（同步包异步，测试里图个方便）。"""
    import asyncio

    return asyncio.run(
        run_due_review(
            case.settings, case.service, today=TODAY, state_path=case.state
        )
    )


if __name__ == "__main__":
    unittest.main()
