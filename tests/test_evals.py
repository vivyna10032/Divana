"""评测的纯逻辑测试：用例解析、确定性检查、报告对比。

全都不联网、不碰模型——所以能天天跑。

用法：python -m unittest tests.test_evals -v
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from divana.evals.cases import Case, CaseError, load_cases, parse_case
from divana.evals.checks import (
    FileSnapshot,
    Finding,
    LlmCall,
    Outcome,
    Step,
    ToolCall,
    check_case,
    urls_in,
)
from divana.evals.report import (
    AttemptCost,
    CaseResult,
    compare,
    estimate_tokens,
    load_baseline,
    render_report,
    render_attempts_table,
    render_token_breakdown,
    render_token_table,
    render_trajectory,
    save_baseline,
    summarize,
    token_totals,
    write_trajectory,
)
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
