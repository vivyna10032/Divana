"""汇总、和 baseline 对比、出报告。

**评测的价值在对比，不在分数。** 一个孤立的"通过 12/14"没什么意义；
有意义的是"改完之后，原来能过的哪几条挂了"。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .checks import Finding, Outcome

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_PATH = PROJECT_ROOT / "evals" / "baseline.json"


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
