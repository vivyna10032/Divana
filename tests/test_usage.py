"""日常用量台账的测试：全是纯逻辑，不联网、不碰模型。

用法：python -m unittest tests.test_usage -v
"""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from divana import usage
from divana.pricing import Price


def entry(
    ts: str = "2026-10-16 09:12",
    kind: str = "chat",
    *,
    calls: int = 2,
    input_tokens: int = 10000,
    output_tokens: int = 1000,
    cached_tokens: int = 9000,
    seconds: float = 20.0,
    tools: tuple[str, ...] = (),
    retries: int = 0,
    error: str = "",
) -> usage.UsageEntry:
    return usage.UsageEntry(
        ts=ts,
        kind=kind,
        model="deepseek-v4-pro",
        calls=calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        seconds=seconds,
        tools=tools,
        retries=retries,
        error=error,
    )


def fake_result(*, requests: int = 3, input_tokens: int = 20000, output_tokens: int = 1500):
    """假装是 Runner 的结果：只带上 entry_from 会去摸的那几个字段。"""
    return SimpleNamespace(
        context_wrapper=SimpleNamespace(
            usage=SimpleNamespace(
                requests=requests,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_tokens_details=SimpleNamespace(cached_tokens=17000),
                output_tokens_details=SimpleNamespace(reasoning_tokens=900),
            )
        )
    )


class EntryTest(unittest.TestCase):
    def test_reads_the_numbers_off_a_run_result(self) -> None:
        """从 SDK 的结果里抽数——这一层不 import agents，靠 getattr 摸。"""
        got = usage.entry_from(
            fake_result(),
            kind="chat",
            model="deepseek-v4-pro",
            seconds=42.44,
            retries=1,
            tools=("read_plan",),
            now=datetime(2026, 10, 16, 9, 12),
        )
        self.assertEqual(got.ts, "2026-10-16 09:12")
        self.assertEqual((got.calls, got.input_tokens, got.output_tokens), (3, 20000, 1500))
        self.assertEqual((got.cached_tokens, got.reasoning_tokens), (17000, 900))
        self.assertEqual(got.seconds, 42.4)
        self.assertEqual(got.tools, ("read_plan",))

    def test_a_result_without_usage_is_not_fatal(self) -> None:
        """SDK 换形状时不至于把整轮对话带崩——只是少几个数。"""
        got = usage.entry_from(
            SimpleNamespace(), kind="chat", model="m", seconds=1.0
        )
        self.assertEqual((got.calls, got.input_tokens), (0, 0))

    def test_json_roundtrip(self) -> None:
        made = entry(tools=("read_plan", "update_plan"), retries=2)
        back = usage.UsageEntry.from_json(json.loads(made.to_json()))
        self.assertEqual(back, made)
        self.assertEqual(back.tools, ("read_plan", "update_plan"))

    def test_unknown_fields_are_ignored(self) -> None:
        """以后加了字段，也要读得动老账。"""
        raw = json.loads(entry().to_json())
        raw["something_new"] = 123
        self.assertEqual(usage.UsageEntry.from_json(raw), entry())

    def test_cost_uses_the_cached_price(self) -> None:
        made = entry(input_tokens=10000, output_tokens=1000, cached_tokens=9000)
        price = Price()
        expected = (1000 * 4 + 9000 * 0.5 + 1000 * 12) / 1_000_000
        self.assertAlmostEqual(made.cost(price), expected)

    def test_day_and_ok_are_derived(self) -> None:
        self.assertEqual(entry().day, "2026-10-16")
        self.assertTrue(entry().ok)
        self.assertFalse(entry(error="timeout").ok)


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "nested" / "usage.jsonl"

    def test_append_creates_the_directory_and_reads_back(self) -> None:
        store = usage.UsageStore(self.path)
        store.append(entry(ts="2026-10-15 10:00"))
        store.append(entry(ts="2026-10-16 10:00", kind="digest"))
        rows = store.entries()
        self.assertEqual([row.ts for row in rows], ["2026-10-15 10:00", "2026-10-16 10:00"])
        self.assertEqual(rows[1].kind, "digest")

    def test_broken_lines_are_skipped(self) -> None:
        """台账是附录：写坏一行不该让整份读不出来。"""
        self.path.parent.mkdir(parents=True)
        self.path.write_text(
            entry(ts="2026-10-15 10:00").to_json() + "\n"
            "这不是 JSON\n"
            "\n"
            '{"ts": "2026-10-16 10:00", "kind": "chat", "model": "m"}\n',
            encoding="utf-8",
        )
        rows = usage.UsageStore(self.path).entries()
        self.assertEqual([row.ts for row in rows], ["2026-10-15 10:00", "2026-10-16 10:00"])

    def test_missing_file_is_not_an_error(self) -> None:
        self.assertEqual(usage.UsageStore(Path("不存在的台账.jsonl")).entries(), [])

    def test_it_does_not_record_what_was_said(self) -> None:
        """台账里只许有数字和工具名——聊天内容一个字都不该进去。

        这是这个文件敢放在 data/ 下、敢被看到的前提：隐私靠"根本没存"，
        不靠"小心别传出去"。
        """
        raw = entry(tools=("read_plan",)).to_json()
        self.assertIn("read_plan", raw)
        for forbidden in ("content", "text", "prompt", "answer", "message"):
            self.assertNotIn(f'"{forbidden}"', raw)


class RetryWatcherTest(unittest.TestCase):
    def test_counts_only_the_retry_lines(self) -> None:
        watcher = usage.RetryWatcher()
        hit = logging.LogRecord(
            "openai._base_client", logging.INFO, "f", 1,
            "Retrying request in 0.5 seconds", None, None,
        )
        miss = logging.LogRecord(
            "openai._base_client", logging.INFO, "f", 1, "别的日志", None, None
        )
        watcher.emit(hit)
        watcher.emit(miss)
        watcher.emit(hit)
        self.assertEqual(watcher.count, 2)

    def test_watching_is_idempotent(self) -> None:
        """重复装不该挂上第二个 handler（否则重试会被数两遍）。"""
        first = usage.watch_retries()
        second = usage.watch_retries()
        self.assertIs(first, second)
        logger = logging.getLogger("openai")
        self.assertEqual(logger.handlers.count(first), 1)

    def test_take_resets(self) -> None:
        usage.watch_retries().count = 3
        self.assertEqual(usage.take_retries(), 3)
        self.assertEqual(usage.take_retries(), 0)


class SummaryTest(unittest.TestCase):
    def rows(self) -> list[usage.UsageEntry]:
        return [
            entry(ts="2026-10-15 10:00", calls=3, seconds=30),
            entry(ts="2026-10-16 09:00", calls=1, seconds=10),
            entry(ts="2026-10-16 09:30", kind="digest", calls=2, seconds=20, retries=1),
            entry(ts="2026-10-16 09:40", seconds=5, error="timeout"),
        ]

    def test_totals_and_averages(self) -> None:
        data = usage.summarize(self.rows(), today=date(2026, 10, 16))
        self.assertEqual(data["turns"], 4)
        self.assertEqual(data["calls"], 8)          # 3 + 1 + 2 + 2
        self.assertEqual(data["seconds"], 65.0)
        self.assertAlmostEqual(data["avg_calls"], 2.0)
        self.assertEqual(data["retried"], 1)
        self.assertEqual(len(data["failed"]), 1)

    def test_by_kind_and_the_most_expensive_one(self) -> None:
        data = usage.summarize(self.rows(), today=date(2026, 10, 16))
        self.assertEqual(sorted(data["by_kind"]), ["chat", "digest"])
        self.assertEqual(data["by_kind"]["chat"][0], 3)
        self.assertEqual(data["worst"].ts, "2026-10-15 10:00")   # 调用最多、最贵

    def test_day_window_covers_today_backwards(self) -> None:
        data = usage.summarize(self.rows(), days=2, today=date(2026, 10, 16))
        self.assertEqual([row.day for row in data["window"]], ["2026-10-15", "2026-10-16"])
        self.assertEqual(data["today"].turns, 3)
        self.assertEqual(data["today"].day, "2026-10-16")

    def test_the_money_uses_the_cache_rate(self) -> None:
        """钱要按缓存命中价算——不然"输入很长"会被算得离谱。"""
        made = usage.summarize([entry(input_tokens=10000, output_tokens=0, cached_tokens=10000)])
        self.assertAlmostEqual(made["cost"], 10000 * 0.5 / 1_000_000)


class ReportTest(unittest.TestCase):
    def test_empty_ledger_says_how_to_fill_it(self) -> None:
        text = usage.render_report([])
        self.assertIn("还是空的", text)
        self.assertIn("不记聊天内容", text)

    def test_report_answers_todays_question(self) -> None:
        rows = [
            entry(ts="2026-10-15 10:00"),
            # 这一轮明显更贵（多读了计划），所以"最贵的一次"该是它
            entry(
                ts="2026-10-16 09:00",
                input_tokens=40000,
                cached_tokens=39000,
                tools=("read_plan",),
                retries=1,
            ),
            entry(ts="2026-10-16 09:30", kind="digest", error="timeout"),
        ]
        text = usage.render_report(rows, days=7, today=date(2026, 10, 16))
        self.assertIn("今天（2026-10-16）：2 轮", text)
        self.assertIn("2026-10-15", text)
        self.assertIn("合计 3 轮", text)
        self.assertIn("命中缓存", text)
        self.assertIn("read_plan", text)          # 最贵那一次在做什么
        self.assertIn("重试", text)
        self.assertIn("失败：1 轮（timeout）", text)
        self.assertIn("价格来源", text)

    def test_report_stays_quiet_when_nothing_went_wrong(self) -> None:
        """没重试、没失败就别报——报告上的每一行都该是有事才出现的。"""
        text = usage.render_report([entry()], today=date(2026, 10, 16))
        self.assertNotIn("重试", text)
        self.assertNotIn("失败", text)

    def test_it_prints_on_a_gbk_console(self) -> None:
        """和评测报告同一个理由：中文 Windows 的控制台是 GBK，编不出的字符会
        让 print 直接抛异常、整份报告消失。"""
        usage.render_report([entry()], today=date(2026, 10, 16)).encode("gbk")


if __name__ == "__main__":
    unittest.main()
