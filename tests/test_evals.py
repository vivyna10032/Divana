"""评测的纯逻辑测试：用例解析、确定性检查、报告对比。

全都不联网、不碰模型——所以能天天跑。

用法：python -m unittest tests.test_evals -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from divana.evals.cases import Case, CaseError, load_cases, parse_case
from divana.evals.checks import Finding, Outcome, ToolCall, check_case, urls_in
from divana.evals.report import (
    CaseResult,
    compare,
    load_baseline,
    render_report,
    save_baseline,
    summarize,
)


def case(**overrides) -> Case:
    base = {"id": "demo", "question": "问一句"}
    return parse_case(base | overrides)


def outcome(**overrides) -> Outcome:
    base = {"text": "回答"}
    return Outcome(**(base | overrides))


def failed_labels(findings: list[Finding]) -> list[str]:
    return [item.label for item in findings if not item.ok]


class LoadCasesTest(unittest.TestCase):
    def test_reads_the_real_file(self) -> None:
        cases = load_cases()
        self.assertGreaterEqual(len(cases), 10)
        self.assertEqual(len({c.id for c in cases}), len(cases))  # id 不重复
        self.assertTrue(all(c.question for c in cases))

    def test_case_file_is_written_with_the_fields_we_support(self) -> None:
        # 用例文件里真的用到了这几类断言（不然等于白写）
        cases = load_cases()
        self.assertTrue(any(c.expect_tools for c in cases))
        self.assertTrue(any(c.forbid_tools for c in cases))
        self.assertTrue(any(c.check_citations for c in cases))
        self.assertTrue(any(c.follow_ups for c in cases))
        self.assertTrue(any(c.given_profile for c in cases))

    def write(self, text: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "cases.yaml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_unknown_field_is_rejected(self) -> None:
        """字段名拼错会让断言悄悄失效——宁可报错。"""
        path = self.write("- id: a\n  question: b\n  expect_tool: [x]\n")
        with self.assertRaises(CaseError) as ctx:
            load_cases(path)
        self.assertIn("expect_tool", str(ctx.exception))

    def test_missing_id_or_question_is_rejected(self) -> None:
        path = self.write("- id: a\n")
        with self.assertRaises(CaseError):
            load_cases(path)

    def test_wrong_type_is_rejected(self) -> None:
        with self.assertRaises(CaseError):
            parse_case({"id": "a", "question": "b", "expect_tools": 123})
        with self.assertRaises(CaseError):
            parse_case({"id": "a", "question": "b", "max_tool_calls": "三次"})

    def test_duplicate_id_is_rejected(self) -> None:
        path = self.write("- id: a\n  question: x\n- id: a\n  question: y\n")
        with self.assertRaises(CaseError) as ctx:
            load_cases(path)
        self.assertIn("重复", str(ctx.exception))

    def test_broken_yaml_is_rejected(self) -> None:
        with self.assertRaises(CaseError):
            load_cases(self.write("- id: a\n   question: 缩进错了\n"))

    def test_missing_file_is_rejected(self) -> None:
        with self.assertRaises(CaseError):
            load_cases(Path("不存在的用例文件.yaml"))

    def test_top_level_must_be_a_list(self) -> None:
        with self.assertRaises(CaseError):
            load_cases(self.write("id: a\n"))

    def test_turns_include_the_follow_ups(self) -> None:
        made = case(follow_ups=["追问一", "追问二"])
        self.assertEqual(made.turns, ("问一句", "追问一", "追问二"))

    def test_expect_pattern_accepts_a_plain_string(self) -> None:
        made = case(expect_pattern="没找到|搜不到")
        self.assertEqual(made.expect_pattern[0].pattern, "没找到|搜不到")
        self.assertEqual(made.expect_pattern[0].label, "")

    def test_expect_pattern_accepts_the_label_form(self) -> None:
        made = parse_case(
            {
                "id": "a",
                "question": "b",
                "expect_pattern": [{"label": "如实说", "pattern": "没"}],
            }
        )
        self.assertEqual(made.expect_pattern[0].label, "如实说")

    def test_broken_regex_is_rejected_at_load_time(self) -> None:
        """正则写错要在加载时就炸——别等某天正好跑到这条用例才发现。"""
        path = self.write("- id: a\n  question: b\n  expect_pattern: '[没闭合'\n")
        with self.assertRaises(CaseError) as ctx:
            load_cases(path)
        self.assertIn("正则", str(ctx.exception))

    def test_expect_pattern_rejects_a_typo_in_the_item(self) -> None:
        with self.assertRaises(CaseError):
            parse_case(
                {"id": "a", "question": "b", "expect_pattern": [{"patern": "x"}]}
            )

    def test_given_state_is_parsed(self) -> None:
        made = parse_case(
            {"id": "a", "question": "b", "given": {"profile": {"目标": "找实习"}}}
        )
        self.assertEqual(made.given_profile, {"目标": "找实习"})


class CheckCaseTest(unittest.TestCase):
    def test_all_good(self) -> None:
        made = case(expect_tools=["read_plan"], max_tool_calls=2)
        got = outcome(tool_calls=(ToolCall("read_plan", "{}"),))
        self.assertEqual(failed_labels(check_case(made, got)), [])

    def test_expected_tool_not_called(self) -> None:
        made = case(expect_tools=["read_plan"])
        labels = failed_labels(check_case(made, outcome()))
        self.assertIn("调用过 read_plan", labels)

    def test_forbidden_tool_called(self) -> None:
        made = case(forbid_tools=["search_web"])
        got = outcome(tool_calls=(ToolCall("search_web", "{}"),))
        self.assertIn("没有调用 search_web", failed_labels(check_case(made, got)))

    def test_too_many_tool_calls(self) -> None:
        made = case(max_tool_calls=1)
        got = outcome(tool_calls=(ToolCall("a", ""), ToolCall("b", "")))
        self.assertIn("工具调用不超过 1 次", failed_labels(check_case(made, got)))

    def test_zero_tool_calls_allowed(self) -> None:
        made = case(max_tool_calls=0)
        self.assertEqual(failed_labels(check_case(made, outcome())), [])

    def test_token_ceiling(self) -> None:
        made = case(max_tokens=100)
        self.assertIn("token 不超过 100", failed_labels(check_case(made, outcome(tokens=500))))

    def test_token_ceiling_is_skipped_when_unknown(self) -> None:
        made = case(max_tokens=100)
        self.assertEqual(failed_labels(check_case(made, outcome(tokens=0))), [])

    def test_expect_text(self) -> None:
        made = case(expect_text=["注意力"])
        self.assertIn("回答里提到「注意力」", failed_labels(check_case(made, outcome())))

    def test_expect_any_needs_one_hit(self) -> None:
        made = case(expect_any=["没找到", "搜不到"])
        self.assertEqual(
            failed_labels(check_case(made, outcome(text="这个仓库我没找到"))), []
        )
        self.assertIn(
            "回答里出现 没找到/搜不到 之一",
            failed_labels(check_case(made, outcome(text="这个项目很棒"))),
        )

    def test_expect_pattern_hit_and_miss(self) -> None:
        made = case(
            expect_pattern=[
                {"label": "如实说没搜到", "pattern": r"(没|未|不)[^\n]{0,60}(有关|相关|结果)"}
            ]
        )
        self.assertEqual(
            failed_labels(check_case(made, outcome(text="结果里没有一个相关的"))), []
        )
        self.assertEqual(
            failed_labels(check_case(made, outcome(text="我搜到了三条相关资料"))),
            ["如实说没搜到"],
        )

    def test_expect_pattern_label_falls_back_to_the_regex(self) -> None:
        made = case(expect_pattern="没找到")
        self.assertIn(
            "回答符合 没找到", failed_labels(check_case(made, outcome(text="找到了")))
        )

    def test_empty_search_still_catches_the_wording_that_used_to_fail(self) -> None:
        """那次误判的原话，得一直被这条用例接住。

        2026-09-22 实测她答「结果里**没有一条**跟「zzqqxxyy」…有关的东西」——
        意思对，但当时的词表一个都没命中，用例被判成失败。那次是尺子不准。
        这条断言跟着用例文件走：以后谁把正则改窄了，这里会亮。
        """
        made = next(item for item in load_cases() if item.id == "empty-search")
        real = (
            "搜完了，结果里**没有一条**跟「zzqqxxyy」这个精确字符串有关的东西。\n\n"
            "搜出来的是些字母相近、但拼写不一样的账号"
        )
        searched = outcome(text=real, tool_calls=(ToolCall("search_web", '{"query": "zzqqxxyy"}'),))
        self.assertEqual(failed_labels(check_case(made, searched)), [])

        # 反方向也要拦住：真找到了却说成别的，不该被判成过。
        wrong = check_case(made, outcome(text="我搜到了三条相关资料，都在下面", tool_calls=searched.tool_calls))
        self.assertTrue(any("如实说没搜到" in item.label for item in wrong if not item.ok))

    def test_empty_answer_fails(self) -> None:
        self.assertIn("有回答", failed_labels(check_case(case(), outcome(text="   "))))

    def test_citation_check_catches_invented_links(self) -> None:
        """这是唯一能自动判的幻觉检查，值得单独盯着。"""
        made = case(check_citations=True)
        got = outcome(
            text="见 [文档](https://编的.example/abc)",
            tool_outputs=("搜索结果：https://真实.example/def",),
        )
        self.assertIn("回答里的链接都来自工具结果", failed_labels(check_case(made, got)))

    def test_citation_check_passes_for_real_links(self) -> None:
        made = case(check_citations=True)
        got = outcome(
            text="见 [文档](https://真实.example/def)",
            tool_outputs=("搜索结果：https://真实.example/def",),
        )
        self.assertEqual(failed_labels(check_case(made, got)), [])

    def test_citation_check_ignores_trailing_punctuation(self) -> None:
        made = case(check_citations=True)
        got = outcome(
            text="见 https://真实.example/def。",
            tool_outputs=("https://真实.example/def",),
        )
        self.assertEqual(failed_labels(check_case(made, got)), [])

    def test_urls_in_strips_chinese_punctuation(self) -> None:
        self.assertEqual(urls_in("看 https://a.example/x。"), ["https://a.example/x"])


class ReportTest(unittest.TestCase):
    def result(self, case_id: str, *, ok: bool, tokens: int = 100) -> CaseResult:
        findings = (Finding(ok, "某个断言", "" if ok else "实际不行"),)
        return CaseResult(
            case_id=case_id,
            outcome=Outcome(text="回答", tokens=tokens, requests=2),
            findings=findings,
            passed_attempts=1 if ok else 0,
        )

    def test_summarize_shape(self) -> None:
        summary = summarize([self.result("a", ok=True)], model="m", commit="c", ran_at="t")
        self.assertEqual(summary["passed"], 1)
        self.assertEqual(summary["total"], 1)
        info = summary["cases"]["a"]
        for key in ("passed", "tool_calls", "requests", "tokens", "failed_checks"):
            self.assertIn(key, info)

    def test_compare_marks_regression(self) -> None:
        before = summarize([self.result("a", ok=True)], model="m")
        after = summarize([self.result("a", ok=False)], model="m")
        self.assertIn("[回归] a", compare(before, after)[0])

    def test_compare_marks_fixed_and_new(self) -> None:
        before = summarize([self.result("a", ok=False)], model="m")
        after = summarize([self.result("a", ok=True), self.result("b", ok=True)], model="m")
        diff = compare(before, after)
        self.assertTrue(any("[修好] a" in line for line in diff))
        self.assertTrue(any("[新增] b" in line for line in diff))

    def test_render_report_shows_failed_checks_and_details(self) -> None:
        text = render_report(summarize([self.result("a", ok=False)], model="m", commit="c"))
        self.assertIn("通过 0 / 1", text)
        self.assertIn("某个断言", text)
        self.assertIn("实际：实际不行", text)

    def test_render_report_shows_cost(self) -> None:
        text = render_report(summarize([self.result("a", ok=True, tokens=1234)], model="m"))
        self.assertIn("1234", text)

    def test_render_report_says_no_change(self) -> None:
        text = render_report(summarize([self.result("a", ok=True)], model="m"), diff=[])
        self.assertIn("没有变化", text)

    def test_baseline_roundtrip(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "baseline.json"

        summary = summarize([self.result("a", ok=True)], model="m", commit="c")
        save_baseline(summary, path)
        self.assertEqual(load_baseline(path)["cases"]["a"]["passed"], True)

    def test_missing_baseline_returns_none(self) -> None:
        self.assertIsNone(load_baseline(Path("不存在的 baseline.json")))


if __name__ == "__main__":
    unittest.main()
