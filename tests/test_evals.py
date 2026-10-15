"""评测的纯逻辑测试：用例解析、确定性检查、报告对比。

全都不联网、不碰模型——所以能天天跑。

用法：python -m unittest tests.test_evals -v
"""

from __future__ import annotations

import re
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from divana.evals.calibrate import CapSuggestion, render_caps, suggest_cap, suggest_caps
from divana.evals.cases import (
    Case,
    CaseError,
    cases_fingerprint,
    fingerprint,
    load_cases,
    parse_case,
)
from divana.evals.checks import (
    FileSnapshot,
    Finding,
    LlmCall,
    Outcome,
    Step,
    ToolCall,
    attach_output,
    check_case,
    tool_call_id,
    urls_in,
)
from divana.evals.report import (
    AttemptCost,
    CaseResult,
    append_history,
    cached_tokens_of,
    compare,
    cost_of,
    estimate_tokens,
    load_baseline,
    load_history,
    history_record,
    render_report,
    render_attempts_table,
    render_history,
    render_token_breakdown,
    render_token_table,
    render_trajectory,
    save_baseline,
    summarize,
    token_totals,
    write_trajectory,
)
from divana.evals.pricing import CURRENCY, Price, format_money, format_rate
from divana.notes import render_note


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

    def test_expect_files_is_parsed(self) -> None:
        made = case(
            expect_files=[
                {
                    "path": "notes/*.md",
                    "contains": ["上下文工程"],
                    "pattern": r"^#\s",
                    "label": "笔记落盘了",
                }
            ]
        )
        spec = made.expect_files[0]
        self.assertEqual(spec.path, "notes/*.md")
        self.assertEqual(spec.contains, ("上下文工程",))
        self.assertEqual(spec.label, "笔记落盘了")
        self.assertTrue(spec.exists)

    def test_expect_files_needs_some_assertion(self) -> None:
        """只写个 path 什么条件都不写，等于白写——宁可报错。"""
        with self.assertRaises(CaseError) as ctx:
            parse_case({"id": "a", "question": "b", "expect_files": [{"path": "plan.md"}]})
        self.assertIn("白写", str(ctx.exception))

    def test_expect_files_rejects_a_broken_regex(self) -> None:
        with self.assertRaises(CaseError) as ctx:
            parse_case(
                {
                    "id": "a",
                    "question": "b",
                    "expect_files": [{"path": "plan.md", "pattern": "[没闭合"}],
                }
            )
        self.assertIn("正则", str(ctx.exception))

    def test_max_calls_per_tool_is_parsed(self) -> None:
        made = case(max_calls_per_tool={"update_plan": 1})
        self.assertEqual(made.max_calls_per_tool, {"update_plan": 1})
        for bad in ("一次", -1, True):
            with self.assertRaises(CaseError):
                parse_case({"id": "a", "question": "b", "max_calls_per_tool": {"x": bad}})

    def test_max_llm_calls_is_parsed(self) -> None:
        self.assertEqual(case(max_llm_calls=3).max_llm_calls, 3)
        for bad in ("三次", -1, 1.5, True):
            with self.assertRaises(CaseError):
                parse_case({"id": "a", "question": "b", "max_llm_calls": bad})
        # 老字段也享受同样的检查（以前 -1 会被默默收下）
        for bad in (-1, True):
            with self.assertRaises(CaseError):
                parse_case({"id": "a", "question": "b", "max_tokens": bad})

    def test_given_settings_is_parsed(self) -> None:
        made = parse_case(
            {"id": "a", "question": "b", "given": {"settings": {"search_api_key": ""}}}
        )
        self.assertEqual(made.given_settings, {"search_api_key": ""})

    def test_given_rejects_unknown_sections(self) -> None:
        with self.assertRaises(CaseError):
            parse_case({"id": "a", "question": "b", "given": {"setting": {}}})

    def test_given_settings_fields_really_exist_on_Settings(self) -> None:
        """用例里覆盖的配置项必须真的存在。

        `given.settings` 是拿字段名去覆盖 Settings 的，写错一个字母只会在跑用例时
        抛 TypeError。这一层（cases.py）不该 import config，所以这条测试就是那个
        检查——把"字段名写错"挡在评测开跑之前。
        """
        from dataclasses import fields

        from divana.config import Settings

        real = {item.name for item in fields(Settings)}
        used = {key for item in load_cases() for key in item.given_settings}
        self.assertTrue(used.issubset(real), f"用例里写了不存在的配置项：{used - real}")

    def test_case_file_uses_the_new_assertions(self) -> None:
        """用例文件里得真的用上这几类断言，不然等于白加功能。"""
        cases = load_cases()
        self.assertTrue(any(item.expect_files for item in cases))
        self.assertTrue(any(item.expect_tool_output for item in cases))
        self.assertTrue(any(item.max_calls_per_tool for item in cases))
        self.assertTrue(all(item.max_llm_calls for item in cases))
        self.assertTrue(any(item.given_settings for item in cases))
        self.assertTrue(any(item.max_tokens for item in cases))

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

    def test_llm_call_ceiling(self) -> None:
        """绕圈的直接度量：一次用户输入触发了几次模型调用。"""
        made = case(max_llm_calls=3)
        three = outcome(
            llm_calls=(
                LlmCall(turn=1, input_tokens=100, output_tokens=10),
                LlmCall(turn=1, input_tokens=200, output_tokens=20),
                LlmCall(turn=1, input_tokens=300, output_tokens=30),
            )
        )
        self.assertEqual(failed_labels(check_case(made, three)), [])
        four = outcome(
            llm_calls=three.llm_calls
            + (LlmCall(turn=1, input_tokens=400, output_tokens=40),)
        )
        self.assertIn("模型调用不超过 3 次", failed_labels(check_case(made, four)))

    def test_llm_call_ceiling_falls_back_to_requests(self) -> None:
        """老数据/手写的 Outcome 里没有逐次记录，就得退回 requests 字段。"""
        made = case(max_llm_calls=2)
        self.assertEqual(failed_labels(check_case(made, outcome(requests=2))), [])
        self.assertIn(
            "模型调用不超过 2 次", failed_labels(check_case(made, outcome(requests=3)))
        )

    def test_zero_tool_calls_allowed(self) -> None:
        made = case(max_tool_calls=0)
        self.assertEqual(failed_labels(check_case(made, outcome())), [])

    def test_token_ceiling(self) -> None:
        made = case(max_tokens=100)
        self.assertIn("token 不超过 100", failed_labels(check_case(made, outcome(tokens=500))))

    def test_token_ceiling_is_skipped_when_unknown(self) -> None:
        made = case(max_tokens=100)
        self.assertEqual(failed_labels(check_case(made, outcome(tokens=0))), [])

    def test_token_ceiling_says_what_the_cap_should_be(self) -> None:
        """护栏挂了要自己给出下一步：是护栏过期，还是她真绕圈了。

        2026-10-15 有两条用例是这么挂的——行为完全正常（调用 3 次、上限 4 次），
        只是护栏还按旧开销写的。只报一个"实际 24484"，下一步只能靠人再算一遍，
        而人一算就容易开始怀疑她。所以这里把该有的数直接摊开。
        """
        made = case(max_tokens=24000, max_llm_calls=4)
        three_calls = tuple(
            LlmCall(turn=1, input_tokens=8000, output_tokens=161) for _ in range(3)
        )
        got = Outcome(text="回答", tokens=24484, llm_calls=three_calls)
        finding = self.token_finding(check_case(made, got))
        self.assertIn("超 2%", finding.detail)
        self.assertIn("跑了 3 次调用", finding.detail)
        self.assertIn("39000", finding.detail)  # 4 × 8161 × 1.2，取整到千

    def test_no_calibration_noise_when_the_cap_holds(self) -> None:
        """过了就别啰嗦——挂在过关的用例上的提示，只会让报告变吵。"""
        made = case(max_tokens=40000, max_llm_calls=4)
        got = Outcome(
            text="回答",
            tokens=24484,
            llm_calls=(LlmCall(turn=1, input_tokens=8000, output_tokens=161),),
        )
        self.assertEqual(
            self.token_finding(check_case(made, got)).detail, f"实际 {got.tokens}"
        )

    def token_finding(self, findings: list[Finding]) -> Finding:
        return next(item for item in findings if item.label.startswith("token 不超过"))

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

    def test_expect_tool_output_checks_what_the_tool_reported(self) -> None:
        """有些结论只有工具能给（比如自测的判定）——断言要落在工具返回上。

        她可以一边说"你答得不错"，一边让工具记下"错"。只查她的措辞抓不到这种偏差。
        """
        made = case(expect_tool_output=[{"label": "批改判了错", "pattern": "(错|半对)"}])
        good = outcome(text="你的答案不错", tool_outputs=("批改完了：1 题，错 1。",))
        self.assertEqual(failed_labels(check_case(made, good)), [])
        bad = outcome(text="你的答案不错", tool_outputs=("批改完了：1 题，对 1。",))
        self.assertIn("批改判了错", failed_labels(check_case(made, bad)))

    def test_expect_tool_output_fails_when_no_tool_ran(self) -> None:
        made = case(expect_tool_output=[{"label": "批改判了错", "pattern": "错"}])
        labels = failed_labels(check_case(made, outcome(text="你答得不错")))
        self.assertIn("批改判了错", labels)

    def test_the_verdict_assertion_is_not_fooled_by_the_counts(self) -> None:
        """查工具返回的那条断言，必须带上题号和冒号。

        批改工具的返回里印着三个计数（"对 1、半对 0、错 0"），所以只写 `(错|半对)`
        的话，**连"这一测全对"都能命中**——断言就成了摆设。
        这个坑是我自己写完立刻验出来的（先量再说，连尺子也要量）。
        """
        made = next(item for item in load_cases() if item.id == "quiz-graded-into-review")
        pattern = made.expect_tool_output[0].pattern
        all_right = "批改完了：1 题，对 1、半对 0、错 0（100 分）。\n- 第 1 题：对"
        self.assertIsNone(re.search(pattern, all_right))
        wrong = "批改完了：1 题，对 0、半对 0、错 1（0 分）。\n- 第 1 题：错"
        self.assertIsNotNone(re.search(pattern, wrong))

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

    # ------------------------------------------------------ 文件副作用断言

    def test_file_expectation_passes_for_the_real_note_format(self) -> None:
        """拿真的 render_note 输出验证式，而不是手写一段"我以为的样子"。"""
        text = render_note(
            "上下文工程",
            "## 什么进 prompt\n\n由每轮是不是都要用来决定。",
            ("agent",),
            date(2026, 9, 23),
        )
        made = case(
            expect_files=[
                {
                    "path": "notes/*.md",
                    "contains": ["上下文工程"],
                    "pattern": r"^#\s[^\n]*\n(?:[ \t]*\n)*##\s",
                }
            ]
        )
        got = outcome(files=(FileSnapshot("notes/2026-09-23-上下文工程.md", text),))
        self.assertEqual(failed_labels(check_case(made, got)), [])

    def test_file_expectation_fails_when_nothing_was_written(self) -> None:
        made = case(expect_files=[{"path": "plan.md", "contains": ["- [x]"]}])
        labels = failed_labels(check_case(made, outcome(files=())))
        self.assertIn("文件 plan.md 被写出来了", labels)

    def test_file_expectation_passes_if_any_matching_file_fits(self) -> None:
        """她多存一篇笔记，不该把这条判死——有一个满足就算过。"""
        made = case(expect_files=[{"path": "notes/*.md", "contains": ["RAG"]}])
        got = outcome(
            files=(
                FileSnapshot("notes/a.md", "## 别的"),
                FileSnapshot("notes/b.md", "## 关于 RAG"),
            )
        )
        self.assertEqual(failed_labels(check_case(made, got)), [])

    def test_file_expectation_can_assert_a_file_was_absent(self) -> None:
        made = case(expect_files=[{"path": "notes/*.md", "exists": False}])
        self.assertEqual(failed_labels(check_case(made, outcome(files=()))), [])
        got = outcome(files=(FileSnapshot("notes/a.md", "## 擅自存的"),))
        self.assertIn("文件 notes/*.md 不该被写出来", failed_labels(check_case(made, got)))

    def test_plan_update_in_one_call_is_the_good_shape(self) -> None:
        """接口改对之后，"同一个工具只调 1 次"这条**曾经错怪过她的**断言真的成立了。

        这条用例被错怪过三次：卡 update_plan 次数（那时接口一次只能改一节）、
        卡 update_learner_profile（prompt 自己两条规则打架）、卡里程碑（并发写丢数据）。
        v0.11 把 update_plan 改成一次能传多节，第一条才从错变成对。
        """
        made = next(item for item in load_cases() if item.id == "plan-update")
        calls = (
            ToolCall("read_plan", "{}"),
            ToolCall(
                "update_plan",
                '{"updates": [{"section": "路线图", "content": "…- [x] …"},'
                ' {"section": "现在的位置", "content": "…"}]}',
            ),
            ToolCall("update_learner_profile", '{"section": "已掌握"}'),
        )
        files = (
            FileSnapshot("plan.md", "### 阶段一：agent 基础\n- [x] 自己写一个最小 agent\n"),
        )
        three = tuple(LlmCall(turn=1, input_tokens=1, output_tokens=1) for _ in range(3))

        # 改画像是允许的（用户说的正是"已经掌握什么"），一次 update_plan 改两节也可以
        ok = outcome(text="改好了", tool_calls=calls, llm_calls=three, files=files)
        self.assertEqual(failed_labels(check_case(made, ok)), [])

        # 但拆成两次调用仍然要挂——这正是接口现在能避免的绕圈
        split = outcome(
            text="改好了",
            tool_calls=(calls[0], calls[1], calls[1], calls[2]),
            llm_calls=three,
            files=files,
        )
        self.assertIn("update_plan 最多调 1 次", failed_labels(check_case(made, split)))

        # 模型调用次数也卡着：上限 3，这里 4
        four = outcome(
            text="改好了",
            tool_calls=calls,
            llm_calls=three + (LlmCall(turn=1, input_tokens=1, output_tokens=1),),
            files=files,
        )
        self.assertIn("模型调用不超过 3 次", failed_labels(check_case(made, four)))

    def test_followup_case_bans_rereading_the_same_repo(self) -> None:
        """追问时把整个仓库重读一遍：总次数上限拦不住它。"""
        made = next(item for item in load_cases() if item.id == "followup-stays-on-topic")
        got = outcome(
            text="它的核心概念是 handoff",
            tool_calls=(
                ToolCall("read_github_repo", '{"repo": "openai/openai-agents-python"}'),
                ToolCall("read_github_repo", '{"repo": "openai/openai-agents-python"}'),
            ),
        )
        self.assertIn(
            "read_github_repo 最多调 1 次", failed_labels(check_case(made, got))
        )

    def test_note_case_accepts_the_wording_that_used_to_fail(self) -> None:
        """2026-09-24 她那次的问句，得被这条用例接住。

        她写的是「要不要把这套「embedding 是什么」存成一篇笔记？」——原来的正则
        只允许"要不要"和"存"之间隔 14 个字符，这句隔了 18 个，于是被判成"没问"。
        **那是尺子写窄了，不是她没说。**
        """
        made = next(item for item in load_cases() if item.id == "no-unprompted-note")
        asked = (
            "embedding 就是把语义映射成向量。"
            "要不要把这套「embedding 是什么」存成一篇笔记？下次复习可以直接翻。"
        )
        got = outcome(text=asked, tool_calls=(ToolCall("update_learner_profile", "{}"),))
        labels = failed_labels(check_case(made, got))
        self.assertFalse(any("要不要存笔记" in item for item in labels))
        # 记画像不该算错：prompt 明说"反复卡在哪"就该记进画像
        self.assertFalse(any("update_learner_profile" in item for item in labels))

    def test_note_case_fails_when_she_only_reports_bookkeeping(self) -> None:
        """她那次真正的问题：只说了"我记进画像了"和"要不要存笔记"，问题没答。

        `expect_text` 就是为这个加的——光有"有回答"（非空）是拦不住的。
        """
        made = next(item for item in load_cases() if item.id == "no-unprompted-note")
        got = outcome(
            text="我把你反复搞混的这一点记进了画像的「薄弱点」。要不要存成一篇笔记？",
            tool_calls=(ToolCall("update_learner_profile", "{}"),),
        )
        self.assertIn("回答里提到「向量」", failed_labels(check_case(made, got)))

    def test_failure_detail_shows_enough_of_the_answer(self) -> None:
        """明细里 60 字太短了，长回答里出问题的那句常在后面，看着像"被截断"。"""
        made = case(expect_pattern="^绝不会出现的$")
        got = outcome(text="前" * 120 + "关键的一句在这里")
        detail = next(item.detail for item in check_case(made, got) if not item.ok)
        self.assertIn("关键的一句在这里", detail)

    def test_plan_update_case_requires_the_checkmark(self) -> None:
        made = next(item for item in load_cases() if item.id == "plan-update")
        got = outcome(
            text="改好了",
            tool_calls=(ToolCall("update_plan", "{}"),),
            files=(FileSnapshot("plan.md", "### 阶段一：agent 基础\n- [ ] 自己写一个最小 agent\n"),),
        )
        self.assertTrue(
            any("里程碑" in item for item in failed_labels(check_case(made, got)))
        )


class TrajectoryTest(unittest.TestCase):
    """失败轨迹：报告里看不出来的东西，得靠它。"""

    def make(self, *, ok: bool = False, steps=(), files=()):
        made = case(expect_tools=["read_plan"], max_tool_calls=1)
        findings = (Finding(ok, "调用过 read_plan", "" if ok else "实际调用：update_plan"),)
        result = CaseResult(
            case_id=made.id,
            outcome=Outcome(text="她说的话", steps=tuple(steps), files=tuple(files)),
            findings=findings,
            attempts=1,
            passed_attempts=1 if ok else 0,
        )
        return made, result

    def test_shows_the_tool_order_with_arguments_and_output(self) -> None:
        made, result = self.make(
            steps=(
                Step(kind="turn", text="我接下来该学什么？"),
                Step(
                    kind="tool",
                    name="read_plan",
                    arguments='{"x": 1}',
                    output="## 现在的位置",
                ),
                Step(kind="reply", text="你该先学 agent 循环"),
            )
        )
        text = render_trajectory(made, result)
        self.assertIn("### 第 1 轮", text)
        self.assertIn("**你**：我接下来该学什么？", text)
        self.assertIn("**调用 `read_plan`**", text)
        self.assertIn("## 现在的位置", text)
        self.assertIn("**她说**", text)
        self.assertIn("你该先学 agent 循环", text)

    def test_trajectory_keeps_every_thing_she_said_in_a_turn(self) -> None:
        """一轮里她可能"先说话 → 调工具 → 再收尾"，三段都要按顺序留在轨迹里。

        2026-09-24 的教训：只看 `result.final_output` 的话，第一段会被丢掉，
        于是"她其实答了"被误判成"她没答"。
        """
        made, result = self.make(
            steps=(
                Step(kind="turn", text="问"),
                Step(kind="reply", text="先把结论讲一遍"),
                Step(kind="tool", name="update_learner_profile", output="已更新"),
                Step(kind="reply", text="记好了，要不要存成笔记？"),
            )
        )
        text = render_trajectory(made, result)
        first = text.index("先把结论讲一遍")
        middle = text.index("**调用 `update_learner_profile`**")
        last = text.index("记好了，要不要存成笔记？")
        self.assertLess(first, middle)
        self.assertLess(middle, last)

    def test_lists_the_failed_checks(self) -> None:
        made, result = self.make()
        text = render_trajectory(made, result)
        self.assertIn("## 挂了哪几条", text)
        self.assertIn("调用过 read_plan", text)
        self.assertIn("实际调用：update_plan", text)

    def test_passing_case_does_not_list_failures(self) -> None:
        made, result = self.make(ok=True, steps=(Step(kind="turn", text="问"),))
        text = render_trajectory(made, result)
        self.assertNotIn("## 挂了哪几条", text)
        self.assertIn("- 结果：过（1/1 次通过）", text)

    def test_shows_the_files_left_behind(self) -> None:
        """文件类断言挂了（里程碑没勾），得能直接看到文件最后长什么样。"""
        made, result = self.make(
            files=(FileSnapshot("plan.md", "### 阶段一\n- [ ] 没勾上\n"),)
        )
        text = render_trajectory(made, result)
        self.assertIn("## 跑完之后的文件", text)
        self.assertIn("`plan.md`（", text)
        self.assertIn("- [ ] 没勾上", text)

    def test_says_so_when_there_is_no_trajectory(self) -> None:
        made, result = self.make()
        self.assertIn("这次运行没有留下轨迹", render_trajectory(made, result))

    def test_long_output_is_clipped_but_says_how_long_it_was(self) -> None:
        made, result = self.make(
            steps=(Step(kind="tool", name="read_url", output="啊" * 3000),)
        )
        self.assertIn("截断，原文 3000 字", render_trajectory(made, result))

    def test_head_records_model_and_commit(self) -> None:
        """commit 记错的话，回头就找不到"那版代码"了。"""
        made, result = self.make()
        text = render_trajectory(made, result, model="deepseek-x", commit="abc123")
        self.assertIn("deepseek-x", text)
        self.assertIn("abc123", text)

    def test_writes_one_file_per_case(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        made, result = self.make()
        path = write_trajectory(
            made, result, directory=Path(tmp.name), model="m", commit="c"
        )
        self.assertEqual(path.name, "demo.md")
        self.assertIn("demo", path.read_text(encoding="utf-8"))


class TokenTest(unittest.TestCase):
    """"超上限"类失败的排查入口：别只看总数，要看钱花在哪一次。"""

    def call(self, turn: int, prompt: int, out: int, tools=()) -> LlmCall:
        return LlmCall(turn=turn, input_tokens=prompt, output_tokens=out, tools=tools)

    def test_estimate_tokens_counts_chinese_heavier_than_latin(self) -> None:
        # DeepSeek 的经验值：1 个汉字 ≈ 0.6 token，1 个英文字符 ≈ 0.3
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("中文"), 2)  # 1.2 → 2
        self.assertEqual(estimate_tokens("abcd"), 2)  # 1.2 → 2
        # 同样是 4 个字符：汉字约 2.4 → 3，英文约 1.2 → 2
        self.assertGreater(estimate_tokens("中文中文"), estimate_tokens("abcd"))

    def test_totals_prefer_the_per_call_records(self) -> None:
        """逐次调用的记录在，就该信它——汇总字段可能是老的/错的。"""
        got = Outcome(
            text="x",
            tokens=999,
            llm_calls=(self.call(1, 100, 20), self.call(1, 300, 40)),
        )
        self.assertEqual(token_totals(got), (400, 60, 460))

    def test_totals_fall_back_to_the_summary(self) -> None:
        got = Outcome(text="x", tokens=500, input_tokens=400, output_tokens=100)
        self.assertEqual(token_totals(got), (400, 100, 500))

    def test_breakdown_marks_the_most_expensive_call(self) -> None:
        result = CaseResult(
            case_id="demo",
            outcome=Outcome(
                text="最终回答",
                steps=(
                    Step(kind="turn", text="问"),
                    Step(kind="tool", name="read_plan", output="计划正文"),
                ),
                llm_calls=(
                    self.call(1, 100, 10, ("read_plan",)),
                    self.call(1, 900, 90),
                ),
            ),
            findings=(),
            attempts=1,
            passed_attempts=0,
        )
        text = render_token_breakdown(result)
        self.assertIn("模型调用 **2 次**", text)
        self.assertIn("input 1000", text)
        self.assertIn("最贵的一次：第 2 次调用", text)
        self.assertIn("read_plan", text)  # 第一次调用之后调了什么，也要能看到
        self.assertIn("工具返回合计 4 字符", text)
        self.assertIn("最终回答 4 字符", text)

    def test_table_flags_cases_over_their_cap(self) -> None:
        def made(case_id: str, tokens: int, cap: int):
            made_case = parse_case({"id": case_id, "question": "q", "max_tokens": cap})
            result = CaseResult(
                case_id=case_id,
                outcome=Outcome(
                    text="x", tokens=tokens, input_tokens=tokens, output_tokens=0
                ),
                findings=(),
                attempts=1,
                passed_attempts=1,
            )
            return made_case, result

        cheap_case, cheap = made("cheap", 100, 500)
        pricey_case, pricey = made("pricey", 900, 500)
        table = render_token_table(
            [cheap, pricey], {"cheap": cheap_case, "pricey": pricey_case}
        )
        self.assertIn("超上限", table)
        self.assertLess(table.index("pricey"), table.index("cheap"))  # 贵的排前面
        self.assertNotIn("cheap ←", table)

    def test_attempts_table_shows_the_spread(self) -> None:
        """一次跑分说明不了问题——要看均值，更要看极差。

        起因：改完工具说明之后单次 input 从 15491 掉到 14593，看着省了 898；
        但这个差是结构性的（说明文字少了 306 字符）还是模型在抖，只有多次跑才知道。
        """
        made = CaseResult(
            case_id="demo",
            outcome=Outcome(text="x"),
            findings=(),
            attempts=3,
            passed_attempts=2,
            attempt_costs=(
                AttemptCost(calls=3, input_tokens=14000, output_tokens=800, passed=True),
                AttemptCost(calls=3, input_tokens=15200, output_tokens=1200, passed=False),
                AttemptCost(calls=3, input_tokens=14600, output_tokens=900, passed=True),
            ),
        )
        table = render_attempts_table([made])
        self.assertIn("14600", table)          # 均值
        self.assertIn("14000 ~ 15200", table)  # 极差
        self.assertIn("2/3", table)
        self.assertIn("极差", table)

    def test_attempts_table_is_empty_for_a_single_run(self) -> None:
        single = CaseResult(
            case_id="demo",
            outcome=Outcome(text="x"),
            findings=(),
            attempt_costs=(AttemptCost(calls=1, input_tokens=100, output_tokens=10, passed=True),),
        )
        self.assertEqual(render_attempts_table([single]), "")

    def test_one_run_can_hold_several_model_calls(self) -> None:
        """一次 Runner.run 里有几次模型调用，取决于她中间调了几次工具。"""
        try:
            from divana.evals.runner import _llm_calls
        except ImportError:  # 没装 agent 栈就跳过
            self.skipTest("没有 agents，跳过")

        usage = SimpleNamespace(input_tokens=100, output_tokens=10)
        result = SimpleNamespace(raw_responses=[SimpleNamespace(usage=usage)] * 3)
        calls = _llm_calls(result, 2)
        self.assertEqual([(call.turn, call.input_tokens) for call in calls], [(2, 100)] * 3)

    def test_llm_calls_falls_back_to_the_aggregate_entries(self) -> None:
        try:
            from divana.evals.runner import _llm_calls
        except ImportError:
            self.skipTest("没有 agents，跳过")

        entries = [SimpleNamespace(input_tokens=7, output_tokens=3)]
        result = SimpleNamespace(
            raw_responses=[],
            context_wrapper=SimpleNamespace(
                usage=SimpleNamespace(request_usage_entries=entries)
            ),
        )
        calls = _llm_calls(result, 1)
        self.assertEqual([(call.input_tokens, call.output_tokens) for call in calls], [(7, 3)])

    def test_tools_are_attributed_to_the_call_that_asked_for_them(self) -> None:
        """2026-09-24 踩过：一次 run 里的工具全被挂到最后一次调用上。

        原因是从事件流里猜（`raw_responses` 是一次性灌进来的，`[-1]` 永远是最后
        一个）。现在改成看每次调用**自己的输出**里有没有 `function_call`。
        """
        try:
            from divana.evals.runner import _llm_calls
        except ImportError:
            self.skipTest("没有 agents，跳过")

        def response(tools):
            output = [
                SimpleNamespace(type="function_call", name=name) for name in tools
            ]
            return SimpleNamespace(
                output=output,
                usage=SimpleNamespace(input_tokens=100, output_tokens=10),
            )

        result = SimpleNamespace(
            raw_responses=[
                response(["read_plan"]),
                response(["update_plan", "update_plan"]),
                response([]),
            ]
        )
        calls = _llm_calls(result, 1)
        self.assertEqual(
            [call.tools for call in calls],
            [("read_plan",), ("update_plan", "update_plan"), ()],
        )

    def test_tools_can_come_from_dict_shaped_output(self) -> None:
        try:
            from divana.evals.runner import _llm_calls
        except ImportError:
            self.skipTest("没有 agents，跳过")

        usage = SimpleNamespace(input_tokens=1, output_tokens=1)
        result = SimpleNamespace(
            raw_responses=[
                SimpleNamespace(
                    output=[{"type": "function_call", "name": "save_note"}],
                    usage=usage,
                )
            ]
        )
        self.assertEqual(_llm_calls(result, 1)[0].tools, ("save_note",))


class AttachmentTest(unittest.TestCase):
    """工具调用和它的返回要配成对——配错了，整份轨迹就是假的。"""

    def test_parallel_calls_do_not_swap_their_outputs(self) -> None:
        """两个并行调用挨在一起时，第一个返回不能被挂到第二个上。

        2026-10-15 的真事：轨迹里 `read_plan` 的返回写着"没找到…笔记"，
        而 `search_notes` 的返回是计划正文——两个返回整体互换。
        轨迹是排查问题的主工具，它串了行，后面所有判断都会跟着跑偏。
        """
        steps = [
            Step(kind="turn", text="考考我"),
            Step(kind="tool", name="search_notes", arguments='{"query":"*"}', call_id="c1"),
            Step(kind="tool", name="read_plan", arguments="{}", call_id="c2"),
        ]
        attach_output(steps, "笔记搜索的结果", "c1")
        attach_output(steps, "计划正文", "c2")
        self.assertEqual(steps[1].output, "笔记搜索的结果")
        self.assertEqual(steps[2].output, "计划正文")

    def test_outputs_arriving_out_of_order_still_pair(self) -> None:
        """返回的顺序万一是反的，配对也得对——认的是编号，不是先后。"""
        steps = [
            Step(kind="tool", name="search_notes", call_id="c1"),
            Step(kind="tool", name="read_plan", call_id="c2"),
        ]
        attach_output(steps, "计划正文", "c2")
        attach_output(steps, "笔记搜索的结果", "c1")
        self.assertEqual(steps[0].output, "笔记搜索的结果")
        self.assertEqual(steps[1].output, "计划正文")

    def test_serial_calls_still_pair_without_an_id(self) -> None:
        """适配器不给编号时退回老办法。真实串行是"调用→返回→调用→返回"，不会错位。"""
        steps = [Step(kind="tool", name="read_plan")]
        attach_output(steps, "计划正文")
        steps.append(Step(kind="tool", name="read_github_repo"))
        attach_output(steps, "仓库内容")
        self.assertEqual([step.output for step in steps], ["计划正文", "仓库内容"])

    def test_ids_are_read_from_objects_and_dicts(self) -> None:
        """SDK 给对象，有些适配器给字典，两种都得认。"""
        self.assertEqual(tool_call_id(SimpleNamespace(call_id="a1")), "a1")
        self.assertEqual(tool_call_id({"call_id": "a2"}), "a2")
        self.assertEqual(tool_call_id(None), "")
        self.assertEqual(tool_call_id(SimpleNamespace()), "")


class ReportTest(unittest.TestCase):
    """报告的组装。"""

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

    def test_compare_in_subset_mode_does_not_list_the_skipped_cases(self) -> None:
        """--only 时别刷一屏"只存在于 baseline"——它会把真正的 [回归] 淹掉。

        2026-09-26 真的被淹过一次：只跑 plan-update，结果 17 条没跑的用例全被标成
        "只存在于 baseline"，我第一眼还以为出了大范围回归。
        """
        before = summarize([self.result("a", ok=True), self.result("b", ok=True)], model="m")
        after = summarize([self.result("a", ok=False)], model="m")
        diff = compare(before, after, subset=True)
        self.assertFalse(any("[只存在于 baseline]" in line for line in diff))
        self.assertTrue(any("[回归] a" in line for line in diff))
        self.assertTrue(any("没跑" in line for line in diff))

    def test_compare_warns_when_the_baseline_is_from_another_commit(self) -> None:
        """过期 baseline 会刷出假回归——先说清楚，再列变化。

        2026-09-26 真被坑过：prompt 和工具都改过好几版了，baseline 还是几天前存的，
        结果一行 [回归] 让排查方向一开始就跑偏。
        """
        before = summarize([self.result("a", ok=True)], model="m", commit="aaa1111")
        after = summarize([self.result("a", ok=False)], model="m", commit="bbb2222")
        diff = compare(before, after)
        self.assertTrue(any("[注意]" in line for line in diff))
        self.assertTrue(any("aaa1111" in line for line in diff))
        self.assertEqual(diff[0].startswith("[注意]"), True)  # 放在最前面

    def test_compare_stays_quiet_when_commits_match(self) -> None:
        before = summarize([self.result("a", ok=True)], model="m", commit="same")
        after = summarize([self.result("a", ok=True)], model="m", commit="same")
        self.assertEqual(compare(before, after), [])

    def test_render_report_shows_failed_checks_and_details(self) -> None:
        text = render_report(summarize([self.result("a", ok=False)], model="m", commit="c"))
        self.assertIn("端到端成功率 0/1", text)
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

    def test_summarize_records_rate_money_and_ruler(self) -> None:
        """一次运行的汇总里要能直接读到：成功率、钱、判据指纹。

        判据指纹得传 `cases` 才有——所以这里顺带盯住"忘了传"这件事：
        没有指纹，历史台账里就没法证明两次跑的是同一把尺子。
        """
        summary = summarize(
            [self.result("a", ok=True)],
            model="m",
            cases=[case(id="a")],
        )
        self.assertEqual(summary["pass_rate"], 1.0)
        self.assertEqual(summary["attempts"], 1)
        self.assertIn("cost", summary)
        self.assertIn("price", summary)
        self.assertEqual(summary["fingerprints"]["a"], fingerprint(case(id="a")))
        self.assertTrue(summary["cases_fingerprint"])

    def test_compare_reports_that_the_ruler_moved(self) -> None:
        """尺子动过要明说。

        分数掉了可能是她退化了，也可能是我们悄悄放宽/收紧了断言——这两件事在报告里
        长得一模一样。所以逐条比判据指纹，把"尺子动过"单独标出来。
        """
        before = summarize([self.result("a", ok=True)], cases=[case(id="a", max_tool_calls=5)])
        after = summarize([self.result("a", ok=True)], cases=[case(id="a", max_tool_calls=8)])
        diff = compare(before, after)
        self.assertTrue(any("[尺子]" in line for line in diff))
        self.assertTrue(any("a" in line for line in diff if "[尺子]" in line))

    def test_compare_stays_quiet_when_the_ruler_is_untouched(self) -> None:
        """没动就是没动——一个天天喊的警告等于没有警告。"""
        before = summarize([self.result("a", ok=True)], cases=[case(id="a", max_tool_calls=5)])
        after = summarize([self.result("a", ok=True)], cases=[case(id="a", max_tool_calls=5)])
        self.assertFalse(any("[尺子]" in line for line in compare(before, after)))

    def test_cost_is_visible_per_case_and_in_total(self) -> None:
        """每条的钱、整轮的钱都要看得见——报告里只给 token 是不好拿去说事的。"""
        token_case = CaseResult(
            case_id="a",
            outcome=Outcome(
                text="回答",
                llm_calls=(
                    LlmCall(turn=1, input_tokens=10_000, output_tokens=2_000),
                ),
            ),
            findings=(Finding(True, "某个断言", ""),),
        )
        summary = summarize([token_case], model="m", price=Price())
        expected = Price().cost(input_tokens=10_000, output_tokens=2_000)
        self.assertAlmostEqual(summary["cost"], round(expected, 6))
        self.assertAlmostEqual(summary["cases"]["a"]["cost"], round(expected, 6))
        self.assertIn("成本", render_report(summary))
        self.assertIn(
            format_money(expected), render_token_table([token_case], price=Price())
        )


class CalibrateTest(unittest.TestCase):
    """护栏校准：这条护栏是不是已经低于"按实测算该有的"了。

    它存在的理由很具体：2026-10-15 一天之内，过期护栏假报了四次回归——
    最少的一次只超 0.2%。每次都花掉一轮评测的钱和一轮读轨迹的时间，
    去证明"她其实没做错"。
    """

    def test_suggestion_uses_allowed_calls_and_a_margin(self) -> None:
        """按「允许的调用次数 × 单次实测 × 1.2」算，取整到千。"""
        # 3 次调用 23421 → 单次 7807 → 3 × 7807 × 1.2 = 28105 → 28000
        self.assertEqual(suggest_cap(measured=23421, calls=3, allowed_calls=3), 28000)
        # 5 次调用 10000 → 单次 2000 → 5 × 2000 × 1.2 = 12000
        self.assertEqual(suggest_cap(measured=10000, calls=5, allowed_calls=5), 12000)

    def test_no_data_means_no_suggestion(self) -> None:
        """没跑过的用例别瞎给数——一个凭空的建议比没有建议更糟。"""
        self.assertEqual(suggest_cap(measured=0, calls=0, allowed_calls=3), 0)
        self.assertEqual(suggest_cap(measured=500, calls=0, allowed_calls=3), 0)
        self.assertEqual(suggest_cap(measured=500, calls=1, allowed_calls=None), 0)

    def test_margin_only_bites_when_the_cap_is_below_the_suggestion(self) -> None:
        loose = CapSuggestion("a", measured=1000, calls=1, per_call=1000, current=5000, suggested=1200)
        tight = CapSuggestion("b", measured=1000, calls=1, per_call=1000, current=900, suggested=1200)
        self.assertFalse(loose.is_tight)   # 只调高不调低：宽松的护栏不用管
        self.assertTrue(tight.is_tight)

    def test_table_lists_only_the_ones_that_need_a_change(self) -> None:
        """20 行"没问题"没人读——只列要动的，其余只报数量。"""
        loose = CapSuggestion("already-loose", measured=1000, calls=1, per_call=1000, current=5000, suggested=1200)
        tight = CapSuggestion("needs-bump", measured=23421, calls=3, per_call=7807, current=16000, suggested=28000)
        blank = CapSuggestion("never-ran", measured=0, calls=0, per_call=0, current=10000, suggested=0)

        text = render_caps([loose, tight, blank], ran_at="2026-10-15 18:37")
        self.assertIn("2026-10-15 18:37", text)
        self.assertIn("needs-bump", text)
        self.assertIn("28000", text)
        self.assertNotIn("already-loose", text)  # 不用动的别列出来
        self.assertIn("没有实测数据", text)       # 没数据要单独说一句
        self.assertIn("never-ran", text)

    def test_table_says_so_when_nothing_needs_changing(self) -> None:
        loose = CapSuggestion("a", measured=1000, calls=1, per_call=1000, current=5000, suggested=1200)
        self.assertIn("没有需要动的", render_caps([loose]))

    def test_suggestions_come_from_a_run_summary(self) -> None:
        """对着一次运行的结果逐条算——数据来自 baseline 或刚跑完的汇总。"""
        made = [case(id="a", max_tokens=16000, max_llm_calls=3)]
        summary = {"cases": {"a": {"tokens": 23421, "llm_calls": 3}}}
        (item,) = suggest_caps(summary, made)
        self.assertEqual((item.measured, item.calls, item.per_call), (23421, 3, 7807))
        self.assertEqual(item.suggested, 28000)
        self.assertTrue(item.is_tight)

    def test_a_case_missing_from_the_run_gets_no_number(self) -> None:
        made = [case(id="never-ran", max_tokens=10000, max_llm_calls=3)]
        (item,) = suggest_caps({"cases": {}}, made)
        self.assertFalse(item.has_data)
        self.assertEqual(item.suggested, 0)


class PricingTest(unittest.TestCase):
    """token 换算成钱。纯算术，但算错了整份台账就不可信——所以单独测。"""

    def test_cached_input_goes_at_the_cheap_rate(self) -> None:
        """命中缓存的那部分 input 走便宜价。

        这是"输入很长"到底贵不贵的关键：人格 prompt 和工具定义每次调用都重发，
        但它们基本都命中缓存，按未命中价算会把它算贵十倍。
        """
        price = Price(input_per_million=4, output_per_million=12, cached_per_million=0.5)
        got = price.cost(input_tokens=10_000, output_tokens=2_000, cached_tokens=8_000)
        self.assertAlmostEqual(got, (2_000 * 4 + 8_000 * 0.5 + 2_000 * 12) / 1_000_000)

    def test_cached_can_never_exceed_input(self) -> None:
        """服务商报的命中数比 input 还大时不至于算出负数。"""
        price = Price(input_per_million=4, output_per_million=12, cached_per_million=0.5)
        self.assertEqual(
            price.cost(input_tokens=100, cached_tokens=999),
            price.cost(input_tokens=100, cached_tokens=100),
        )

    def test_nothing_used_is_free(self) -> None:
        self.assertEqual(Price().cost(), 0.0)
        self.assertEqual(format_money(0), f"{CURRENCY}0")

    def test_env_overrides_and_the_source_is_visible(self) -> None:
        """价目表的**来源**要写进报告：一个不知道单价的成本数字没人敢引用。"""
        self.assertTrue(Price().is_default)
        given = Price.from_env({"DIVANA_PRICE_IN": "1", "DIVANA_PRICE_OUT": "2"})
        self.assertFalse(given.is_default)
        self.assertIn(".env", given.describe())
        self.assertEqual(given.input_per_million, 1)
        self.assertEqual(given.output_per_million, 2)
        # 只填了一个，另一个照样有默认值——不能变成 0（那等于白送）
        self.assertEqual(given.cached_per_million, Price().cached_per_million)

    def test_a_typo_in_the_price_falls_back(self) -> None:
        """.env 里写错一个价，不该让评测跑不起来，也不该按 0 元记成"免费"。"""
        given = Price.from_env({"DIVANA_PRICE_IN": "四块", "DIVANA_PRICE_OUT": "-1"})
        self.assertEqual(given.input_per_million, Price().input_per_million)
        self.assertEqual(given.output_per_million, Price().output_per_million)

    def test_money_and_rate_print_on_a_gbk_console(self) -> None:
        """货币符号必须能被 GBK 编出来。

        中文 Windows 的控制台是 cp936：报告里出现一个它编不出的字符（半角 ¥ 就不行），
        `print` 会抛 UnicodeEncodeError——不是显示成问号，是**整条命令崩掉**，
        报告一个字都看不到。一个符号带走整份报告，代价太大。
        """
        CURRENCY.encode("gbk")
        format_money(0.036).encode("gbk")
        format_rate(19, 20).encode("gbk")

    def test_rate_keeps_the_sample_size(self) -> None:
        """百分比要连着分子分母一起给——只给百分比会把样本量藏起来。"""
        self.assertEqual(format_rate(19, 20), "19/20（95%）")
        self.assertEqual(format_rate(0, 0), "—")


class FingerprintTest(unittest.TestCase):
    """判据指纹：分数变了，是她退化了，还是尺子被挪了？"""

    def test_same_case_same_mark(self) -> None:
        self.assertEqual(fingerprint(case()), fingerprint(case()))

    def test_changing_an_assertion_moves_the_mark(self) -> None:
        """放宽、收紧、换材料都算动了尺子——这正是要抓的事。"""
        base = case(max_tool_calls=4)
        self.assertNotEqual(fingerprint(base), fingerprint(case(max_tool_calls=6)))
        self.assertNotEqual(
            fingerprint(base),
            fingerprint(case(max_tool_calls=4, expect_tools=["read_plan"])),
        )
        self.assertNotEqual(
            fingerprint(base),
            fingerprint(
                case(
                    max_tool_calls=4,
                    expect_pattern=[{"pattern": "没搜到", "label": "如实说"}],
                )
            ),
        )
        self.assertNotEqual(
            fingerprint(base),
            fingerprint(case(max_tool_calls=4, question="换成另一问")),
        )
        self.assertNotEqual(
            fingerprint(base),
            fingerprint(case(max_tool_calls=4, given={"plan": {"路线图": "阶段一"}})),
        )

    def test_human_words_do_not_move_the_mark(self) -> None:
        """只改写给人看的字（note / label）不该报警。

        算进去的后果很实际：润色一段注释就喊一次"尺子变了"，喊上三次就没人看这个
        警告了——等真改了判据，反而被忽略。
        """
        annotated = case(
            expect_tools=["read_plan"],
            expect_pattern=[{"pattern": "没搜到", "label": "如实说没搜到"}],
            note="这条来自 2026-09-20 那次真实误判",
        )
        renamed = case(
            expect_tools=["read_plan"],
            expect_pattern=[{"pattern": "没搜到", "label": "换了个说法"}],
            note="注释也重写了一遍",
        )
        self.assertEqual(fingerprint(annotated), fingerprint(renamed))

    def test_order_of_assertions_does_not_matter(self) -> None:
        """expect_pattern 是"每条都要命中"，顺序不影响结果，重排一下不该报警。"""
        first = case(expect_pattern=[{"pattern": "甲"}, {"pattern": "乙"}])
        second = case(expect_pattern=[{"pattern": "乙"}, {"pattern": "甲"}])
        self.assertEqual(fingerprint(first), fingerprint(second))

    def test_the_whole_set_has_one_mark(self) -> None:
        self.assertEqual(cases_fingerprint([case()]), cases_fingerprint([case()]))
        self.assertNotEqual(
            cases_fingerprint([case()]),
            cases_fingerprint([case(), case(id="other")]),
        )


class HistoryTest(unittest.TestCase):
    """台账：成功率和单次成本的趋势。**这张表才是能拿出去说的东西。**"""

    def record(self, **overrides) -> dict:
        base = {
            "ran_at": "2026-10-15 10:00",
            "model": "m",
            "commit": "abc1234",
            "cases_fingerprint": "ffff0000",
            "attempts": 1,
            "total": 20,
            "passed": 19,
            "pass_rate": 0.95,
            "tokens": 168_000,
            "avg_tokens": 8_400,
            "cost": 0.152,
            "avg_cost": 0.0076,
        }
        return base | overrides

    def test_append_then_read_back(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "history.jsonl"

        append_history(self.record(), path)
        append_history(
            self.record(ran_at="2026-10-15 11:00", passed=20, pass_rate=1.0), path
        )
        records = load_history(path)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[-1]["passed"], 20)

    def test_broken_lines_are_skipped(self) -> None:
        """台账是附录：写坏一行不该让整份读不出来。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "history.jsonl"
        path.write_text(
            '{"ran_at": "t1", "passed": 1, "total": 1}\n'
            "这不是 JSON\n"
            "\n"
            '{"ran_at": "t2", "passed": 0, "total": 1}\n',
            encoding="utf-8",
        )
        self.assertEqual([item["ran_at"] for item in load_history(path)], ["t1", "t2"])

    def test_missing_history_is_not_an_error(self) -> None:
        self.assertEqual(load_history(Path("不存在的台账.jsonl")), [])
        self.assertIn("还是空的", render_history([]))

    def test_table_shows_the_trend(self) -> None:
        """要能一眼看出"成功率没动、钱少了两成"这种变化。"""
        text = render_history(
            [
                self.record(ran_at="2026-10-14 10:00", avg_cost=0.01, cost=0.2),
                self.record(ran_at="2026-10-15 10:00", avg_cost=0.008, cost=0.16),
            ]
        )
        self.assertIn("2026-10-14 10:00", text)
        self.assertIn("19/20（95%）", text)
        self.assertIn("-20%", text)

    def test_trend_refuses_to_compare_two_different_rulers(self) -> None:
        """题集指纹不同就明说不可比——这正是记指纹的用处。"""
        text = render_history(
            [
                self.record(cases_fingerprint="aaaa1111"),
                self.record(cases_fingerprint="bbbb2222"),
            ]
        )
        self.assertIn("不可直接比", text)

    def test_the_record_keeps_only_the_columns_that_trend(self) -> None:
        record = history_record(
            summarize(
                [
                    CaseResult(
                        case_id="a",
                        outcome=Outcome(text="回答", tokens=100),
                        findings=(Finding(True, "某个断言", ""),),
                    )
                ],
                model="m",
            )
        )
        self.assertEqual(
            sorted(record),
            [
                "attempts",
                "avg_cost",
                "avg_tokens",
                "cases_fingerprint",
                "commit",
                "cost",
                "model",
                "pass_rate",
                "passed",
                "ran_at",
                "tokens",
                "total",
            ],
        )


if __name__ == "__main__":
    unittest.main()
