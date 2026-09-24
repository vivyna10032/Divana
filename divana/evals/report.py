"""汇总、和 baseline 对比、出报告。

**评测的价值在对比，不在分数。** 一个孤立的"通过 12/14"没什么意义；
有意义的是"改完之后，原来能过的哪几条挂了"。
"""

from __future__ import annotations

import json
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


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    outcome: Outcome
    findings: tuple[Finding, ...]
    attempts: int = 1
    passed_attempts: int = 1

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
        lines.append("")
        lines.append(
            f"token：合计 {sum(costs)}，单条平均 {sum(costs) // len(costs)}"
            "（这个数字本身也是指标）"
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
