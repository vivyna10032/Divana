"""汇总、和 baseline 对比、出报告。

**评测的价值在对比，不在分数。** 一个孤立的"通过 12/14"没什么意义；
有意义的是"改完之后，原来能过的哪几条挂了"。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .cases import Case
from .checks import Finding, Outcome

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_PATH = PROJECT_ROOT / "evals" / "baseline.json"
TRAJECTORY_DIR = PROJECT_ROOT / "evals" / "trajectory"

# 轨迹文件里每段最多留多少字。工具返回和文件内容都可能很长，全量写进去反而
# 看不清重点——截断时会把原长度标出来，需要细节可以自己重跑一条（--only）。
TOOL_ARGS_CHARS = 400
TOOL_OUTPUT_CHARS = 900
REPLY_CHARS = 2000
FILE_CHARS = 1500

# 估算 token 时按字符类型折算。DeepSeek 自己给的经验值：1 个汉字 ≈ 0.6 token，
# 1 个英文字符 ≈ 0.3 token。
_CJK_PER_CHAR = 0.6
_OTHER_PER_CHAR = 0.3
_CJK_RANGES = (
    (0x3000, 0x303F),  # 中日韩标点
    (0x3040, 0x30FF),  # 假名
    (0x3400, 0x4DBF),  # 汉字扩展 A
    (0x4E00, 0x9FFF),  # 基本汉字
    (0xF900, 0xFAFF),  # 兼容汉字
    (0xFF00, 0xFFEF),  # 全角字符
)


def _is_cjk(char: str) -> bool:
    point = ord(char)
    return any(low <= point <= high for low, high in _CJK_RANGES)


def _display_width(text: str) -> int:
    """终端里占几格（汉字算两格），用来把表格对齐。"""
    return sum(2 if _is_cjk(char) else 1 for char in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def estimate_tokens(text: str) -> int:
    """粗估一段文本占多少 token。

    为什么要估：API 只报**整次调用**的用量，"到底哪一段吃掉了上下文"——某个工具的
    返回、最后那段回答——问不出来，只能按字符折。所以这是个**估算，别当账目用**：
    真账看 API 报的 input / output。
    """
    if not text:
        return 0
    weight = sum(_CJK_PER_CHAR if _is_cjk(char) else _OTHER_PER_CHAR for char in text)
    return max(1, math.ceil(weight))


def token_totals(outcome: Outcome) -> tuple[int, int, int]:
    """(input, output, 合计)。逐次调用的记录最准，没有就退回汇总字段。"""
    if outcome.llm_calls:
        prompt = sum(call.input_tokens for call in outcome.llm_calls)
        completion = sum(call.output_tokens for call in outcome.llm_calls)
        return prompt, completion, prompt + completion
    return outcome.input_tokens, outcome.output_tokens, outcome.tokens


@dataclass(frozen=True)
class AttemptCost:
    """一次尝试的成本与结果。

    `--attempts N` 时要把**每一次**都记下来：只看最后一次的话，你没法判断
    "这次改动省了 450 token"是结构性的，还是模型本来就在抖。同一个数字，
    有均值和极差才敢下结论。
    """

    calls: int
    input_tokens: int
    output_tokens: int
    passed: bool


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    outcome: Outcome
    findings: tuple[Finding, ...]
    attempts: int = 1
    passed_attempts: int = 1
    attempt_costs: tuple[AttemptCost, ...] = ()

    @property
    def passed(self) -> bool:
        return self.passed_attempts == self.attempts

    @property
    def failed(self) -> list[Finding]:
        return [item for item in self.findings if not item.ok]


def summarize(
    results: list[CaseResult], *, model: str = "", commit: str = "", ran_at: str = ""
) -> dict:
    """把一次运行压成可存、可比的结果。

    存的东西里，**tool_calls / requests / tokens 三个也要留下来**：
    回答对了但绕了八圈，也是一种退化，光看"过没过"看不出来。
    """
    cases = {}
    for result in results:
        cases[result.case_id] = {
            "passed": result.passed,
            "attempts": result.attempts,
            "passed_attempts": result.passed_attempts,
            "tool_calls": list(result.outcome.tool_names),
            "requests": result.outcome.requests,
            "tokens": result.outcome.tokens,
            "input_tokens": result.outcome.input_tokens,
            "output_tokens": result.outcome.output_tokens,
            "llm_calls": len(result.outcome.llm_calls),
            "failed_checks": [item.label for item in result.failed],
            "detail": [item.detail for item in result.failed if item.detail],
        }

    return {
        "model": model,
        "commit": commit,
        "ran_at": ran_at,
        "total": len(results),
        "passed": sum(1 for result in results if result.passed),
        "cases": cases,
    }


def compare(baseline: dict, current: dict) -> list[str]:
    """只看变化。这是整个评测里最该盯的一段输出。"""
    before = baseline.get("cases", {})
    after = current.get("cases", {})
    lines: list[str] = []

    for case_id, now in after.items():
        was = before.get(case_id)
        if was is None:
            lines.append(f"[新增] {case_id}" + ("" if now["passed"] else "（未过）"))
        elif was["passed"] and not now["passed"]:
            reason = "、".join(now.get("failed_checks") or []) or "见下面的明细"
            lines.append(f"[回归] {case_id}  ← 原来能过，现在挂了（{reason}）")
        elif not was["passed"] and now["passed"]:
            lines.append(f"[修好] {case_id}")

    for case_id in before:
        if case_id not in after:
            lines.append(f"[只存在于 baseline] {case_id}")
    return lines


def render_report(summary: dict, diff: list[str] | None = None) -> str:
    """给人看的报告。失败的那几条列到断言一级，否则不知道该改哪儿。"""
    lines = [
        f"模型 {summary.get('model', '?')}　提交 {summary.get('commit', '?')}"
        f"　{summary.get('ran_at', '')}",
        f"通过 {summary['passed']} / {summary['total']}",
        "",
    ]

    cases = summary.get("cases", {})
    failed = {key: value for key, value in cases.items() if not value["passed"]}
    if failed:
        lines.append("没过的：")
        for case_id, info in failed.items():
            lines.append(f"  {case_id}  （{info['passed_attempts']}/{info['attempts']} 次通过）")
            for label in info.get("failed_checks", []):
                lines.append(f"      · {label}")
            for detail in info.get("detail", []):
                lines.append(f"        实际：{detail}")
        lines.append("")

    if diff is None:
        pass
    elif diff:
        lines.append("和 baseline 比：")
        lines.extend(f"  {line}" for line in diff)
    else:
        lines.append("和 baseline 比：没有变化")

    costs = [info["tokens"] for info in cases.values() if info["tokens"]]
    if costs:
        asked = sum(info.get("input_tokens", 0) for info in cases.values())
        answered = sum(info.get("output_tokens", 0) for info in cases.values())
        lines.append("")
        lines.append(
            f"token：合计 {sum(costs)}（input {asked} / output {answered}），"
            f"单条平均 {sum(costs) // len(costs)}（这个数字本身也是指标）"
        )
    return "\n".join(lines)


def load_baseline(path: Path | None = None) -> dict | None:
    target = Path(path) if path is not None else BASELINE_PATH
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_baseline(summary: dict, path: Path | None = None) -> Path:
    from ..storage import atomic_write

    target = Path(path) if path is not None else BASELINE_PATH
    atomic_write(target, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return target


# ------------------------------------------------------------------ 失败轨迹


def _clip(text: str, limit: int) -> str:
    """太长的内容截一段，但要标出原来有多长——不然会以为她只说了这么多。"""
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}\n…（截断，原文 {len(text)} 字）"


def _bullet(label: str, value: str) -> list[str]:
    """一条列表项。值可能是多行的（比如注入的计划），续行要缩进，
    不然 markdown 会把它们当成新的段落，列表就断了。"""
    rows = value.splitlines() or [""]
    out = [f"- {label}：{rows[0]}".rstrip()]
    out.extend(f"    {row}".rstrip() for row in rows[1:])
    return out


def render_token_breakdown(result: CaseResult) -> str:
    """token 花在哪：每次模型调用、每个工具返回、最后那段回答。

    触发这次改动的场景：评测因为"超过 token 上限"挂掉，可报告里只有一个总数——
    看不出是输入本身太长、绕了太多圈，还是某个工具返回太胖。所以这里把它拆开，
    并**把最贵的那一次模型调用标出来**。
    """
    outcome = result.outcome
    prompt, completion, total = token_totals(outcome)
    tools = [step for step in outcome.steps if step.kind == "tool"]
    tool_chars = sum(len(step.output) for step in tools)

    lines = [
        "## token 明细",
        "",
        f"- 模型调用 **{len(outcome.llm_calls) or outcome.requests} 次**"
        f"　input {prompt}　output {completion}　合计 **{total}**",
        f"- 工具返回合计 {tool_chars} 字符"
        f"（约 {estimate_tokens(''.join(step.output for step in tools))} token，"
        f"共 {len(tools)} 次调用）",
        f"- 最终回答 {len(outcome.text)} 字符（约 {estimate_tokens(outcome.text)} token）",
    ]

    calls = outcome.llm_calls
    if calls:
        dearest = max(calls, key=lambda call: call.total_tokens)
        share = round(100 * dearest.total_tokens / total) if total else 0
        doing = "、".join(dearest.tools) or "出最终回答"
        lines.append(
            f"- **最贵的一次：第 {calls.index(dearest) + 1} 次调用**"
            f"（第 {dearest.turn} 轮）input {dearest.input_tokens} + "
            f"output {dearest.output_tokens} = {dearest.total_tokens}，占 {share}%"
            f"——这一次她做的是：{doing}"
        )
        thinking = sum(call.reasoning_tokens for call in calls)
        if thinking and completion:
            lines.append(
                f"- 其中推理（思考）tokens：{thinking}"
                f"（占 output 的 {round(100 * thinking / completion)}%）"
                "——这部分按 output 计费，说话少不代表不花钱"
            )
        lines += [
            "",
            "| # | 轮次 | input | output | 其中推理 | 合计 | 占合计 | 这次调用之后 |",
            "|---|------|------:|-------:|--------:|-----:|-------:|--------------|",
        ]
        for index, call in enumerate(calls, start=1):
            rate = round(100 * call.total_tokens / total) if total else 0
            mark = " ← **最贵**" if call is dearest else ""
            what = "、".join(call.tools) if call.tools else "出最终回答"
            lines.append(
                f"| {index} | 第 {call.turn} 轮 | {call.input_tokens} | "
                f"{call.output_tokens} | {call.reasoning_tokens} | "
                f"{call.total_tokens} | {rate}% | {what}{mark} |"
            )

    if tools:
        lines += [
            "",
            "工具返回的体量（估算）：",
            "",
            "| 轮次 | 工具 | 字符 | 约 token |",
            "|------|------|-----:|--------:|",
        ]
        turn = 0
        for step in outcome.steps:
            if step.kind == "turn":
                turn += 1
            elif step.kind == "tool":
                lines.append(
                    f"| 第 {turn} 轮 | `{step.name}` | {len(step.output)} | "
                    f"{estimate_tokens(step.output)} |"
                )
    return "\n".join(lines)


def render_attempts_table(results: list[CaseResult]) -> str:
    """多次尝试的成本：均值和极差。

    **没有极差就没法判断改动有没有效**：同一个版本三次跑下来 input 就能差 800 的话，
    "这次省了 450 token" 什么都说明不了。这一节就是给这种判断用的——
    它是「先量再说」的最后一块拼图。
    """
    rows = [item for item in results if len(item.attempt_costs) > 1]
    if not rows:
        return ""

    lines = [
        "多次尝试的成本（--attempts 的用处就在这儿）：",
        "",
        f"{_pad('用例', 24)}{_pad('次数', 6)}{_pad('input 均值', 12)}"
        f"{_pad('input 极差', 18)}{_pad('output 均值', 12)}通过",
    ]
    spreads: list[int] = []
    for result in rows:
        ins = [cost.input_tokens for cost in result.attempt_costs]
        outs = [cost.output_tokens for cost in result.attempt_costs]
        spreads.append(max(ins) - min(ins))
        lines.append(
            f"{_pad(result.case_id, 24)}{_pad(str(len(ins)), 6)}"
            f"{_pad(str(sum(ins) // len(ins)), 12)}"
            f"{_pad(f'{min(ins)} ~ {max(ins)}', 18)}"
            f"{_pad(str(sum(outs) // len(outs)), 12)}"
            f"{result.passed_attempts}/{result.attempts}"
        )

    lines.append("")
    lines.append(
        f"input 极差平均 {sum(spreads) // len(spreads)} 左右："
        "**极差比你要验证的改动还大的时候，单次跑分不能作数**。"
    )
    return "\n".join(lines)


def render_token_table(
    results: list[CaseResult], cases: dict[str, Case] | None = None
) -> str:
    """跑完之后的 token 分布表——一眼看出哪条贵、贵在 input 还是绕圈。"""
    known = cases or {}
    rows = []
    for result in results:
        prompt, completion, total = token_totals(result.outcome)
        calls = result.outcome.llm_calls
        dearest = max(calls, key=lambda call: call.total_tokens) if calls else None
        share = round(100 * dearest.total_tokens / total) if dearest and total else 0
        tool_chars = sum(
            len(step.output) for step in result.outcome.steps if step.kind == "tool"
        )
        cap = known[result.case_id].max_tokens if result.case_id in known else None
        rows.append(
            (result, prompt, completion, total, len(calls), share, tool_chars, cap)
        )

    rows.sort(key=lambda row: row[3], reverse=True)
    lines = [
        "token 分布（按合计从大到小）：",
        "",
        f"{_pad('用例', 24)}{_pad('调用', 6)}{_pad('input', 9)}"
        f"{_pad('output', 8)}{_pad('合计', 9)}{_pad('最贵一次', 10)}"
        f"{_pad('工具返回', 11)}上限",
    ]
    for result, prompt, completion, total, calls, share, tool_chars, cap in rows:
        note = " ← 超上限" if cap and total > cap else ""
        lines.append(
            f"{_pad(result.case_id, 24)}{_pad(str(calls), 6)}{_pad(str(prompt), 9)}"
            f"{_pad(str(completion), 8)}{_pad(str(total), 9)}"
            f"{_pad(f'{share}%', 10)}{_pad(f'{tool_chars} 字', 11)}"
            f"{cap if cap else '—'}{note}"
        )

    if rows:
        everything = sum(row[3] for row in rows)
        lines.append("")
        lines.append(
            f"合计 {everything} tokens　平均每条 {everything // len(rows)}"
            f"　（input 占 {round(100 * sum(r[1] for r in rows) / everything)}%）"
        )
    return "\n".join(lines)


def render_trajectory(
    case: Case, result: CaseResult, *, model: str = "", commit: str = ""
) -> str:
    """把一条用例的完整轨迹写成 markdown。

    报告里只有"哪条断言挂了"，看不出**她绕到哪去了**。要查"同一个工具调了三次"
    "读了不该读的东西""答到一半跑题"，必须看轨迹——所以失败时把它整份写出来。

    带上跑完之后的**文件**：文件类断言挂了（里程碑没勾、画像没写进去），
    光看工具返回不够，得看文件最后长什么样。
    """
    outcome = result.outcome
    lines = [
        f"# 轨迹：{case.id}",
        "",
        f"- 结果：{'过' if result.passed else '挂'}"
        f"（{result.passed_attempts}/{result.attempts} 次通过）",
        f"- 模型：{model or '?'}　提交：{commit or '?'}",
        f"- 成本：{outcome.requests} 次请求　{outcome.tokens} tokens",
        "",
        "## 她拿到了什么",
        "",
    ]
    lines += _bullet("问题", case.question)
    for index, follow in enumerate(case.follow_ups, start=1):
        lines += _bullet(f"追问 {index}", follow)
    for key, value in sorted(case.given_profile.items()):
        lines += _bullet(f"画像（{key}）", value)
    for key, value in sorted(case.given_plan.items()):
        lines += _bullet(f"计划（{key}）", value)
    for key, value in sorted(case.given_settings.items()):
        lines.append(f"- 覆盖配置：{key} = {value!r}")
    lines.append("")

    if not result.passed:
        lines += ["## 挂了哪几条", ""]
        for item in result.findings:
            if item.ok:
                continue
            lines.append(f"- **{item.label}**")
            if item.detail:
                lines.append(f"    - 实际：{item.detail}")
        lines.append("")

    lines += [render_token_breakdown(result), ""]

    lines += ["## 每一轮发生了什么", ""]
    turn = 0
    for step in outcome.steps:
        if step.kind == "turn":
            turn += 1
            lines += [f"### 第 {turn} 轮", "", f"**你**：{step.text}", ""]
        elif step.kind == "tool":
            lines.append(f"**调用 `{step.name}`**")
            if step.arguments:
                lines += ["", "参数：", "", "```json", _clip(step.arguments, TOOL_ARGS_CHARS), "```"]
            if step.output:
                lines += ["", "返回：", "", "```", _clip(step.output, TOOL_OUTPUT_CHARS), "```"]
            lines.append("")
        elif step.kind == "reply":
            lines += ["**她说**：", "", _clip(step.text, REPLY_CHARS), ""]
    if not outcome.steps:
        lines += ["（这次运行没有留下轨迹）", ""]

    lines += ["## 跑完之后的文件（临时 vault）", ""]
    if outcome.files:
        for item in outcome.files:
            lines += [
                f"### `{item.path}`（{len(item.text)} 字）",
                "",
                "```markdown",
                _clip(item.text, FILE_CHARS),
                "```",
                "",
            ]
    else:
        lines += ["（一个文件都没有）", ""]

    return "\n".join(lines).rstrip() + "\n"


def write_trajectory(
    case: Case,
    result: CaseResult,
    *,
    directory: Path | None = None,
    model: str = "",
    commit: str = "",
) -> Path:
    """写一份轨迹，文件名就是用例 id（同名覆盖）。"""
    from ..storage import atomic_write

    target_dir = Path(directory) if directory is not None else TRAJECTORY_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{case.id}.md"
    atomic_write(path, render_trajectory(case, result, model=model, commit=commit))
    return path
