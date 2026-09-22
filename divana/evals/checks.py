"""确定性检查：只看她**做了什么**，不看她说得好不好。

这是评测里最可信的一层——零成本、零噪声、可重复。所以第一版只做这一层。
模型裁判留到以后：它贵、有噪声，而且判"事实对不对"最不可靠。

检查器只吃一个 `Outcome` 对象（纯数据），所以它自己也能离线测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .cases import Case

# URL 后面常跟着中文标点，抠出来之后要截掉
_URL = re.compile(r"https?://[^\s)\]<>\"'，。；、]+")


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: str


@dataclass(frozen=True)
class Outcome:
    """一次运行的原始结果。检查器只看这个对象。"""

    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_outputs: tuple[str, ...] = ()
    tokens: int = 0
    requests: int = 0

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(call.name for call in self.tool_calls)


@dataclass(frozen=True)
class Finding:
    """一条断言的结论。报告里逐条列出来，失败那条就是排查入口。"""

    ok: bool
    label: str
    detail: str = ""


def urls_in(text: str) -> list[str]:
    """把文本里的链接抠出来（去掉尾部标点）。"""
    return [raw.rstrip(".,;:)）】") for raw in _URL.findall(text)]


def check_case(case: Case, outcome: Outcome) -> list[Finding]:
    """跑一条用例的所有断言。"""
    findings: list[Finding] = []
    names = outcome.tool_names

    # 1. 有没有回答
    has_text = bool(outcome.text.strip())
    findings.append(Finding(has_text, "有回答", "" if has_text else "她一个字都没说"))

    # 2. 该调的工具调了吗
    for wanted in case.expect_tools:
        findings.append(
            Finding(
                wanted in names,
                f"调用过 {wanted}",
                f"实际调用：{'、'.join(names) or '一个都没调'}",
            )
        )

    # 3. 不该调的调了吗
    for banned in case.forbid_tools:
        findings.append(
            Finding(banned not in names, f"没有调用 {banned}", f"实际调用：{'、'.join(names)}")
        )

    # 4. 上限：工具调用次数（转太多圈 = 又慢又贵）
    if case.max_tool_calls is not None:
        findings.append(
            Finding(
                len(names) <= case.max_tool_calls,
                f"工具调用不超过 {case.max_tool_calls} 次",
                f"实际 {len(names)} 次（{'、'.join(names)}）",
            )
        )

    # 5. 上限：token
    if case.max_tokens is not None and outcome.tokens:
        findings.append(
            Finding(
                outcome.tokens <= case.max_tokens,
                f"token 不超过 {case.max_tokens}",
                f"实际 {outcome.tokens}",
            )
        )

    # 6. 回答里必须出现某些词
    for word in case.expect_text:
        findings.append(Finding(word in outcome.text, f"回答里提到「{word}」"))

    # 7. 至少出现一个（判"她有没有如实说做不到"）
    if case.expect_any:
        hit = next((word for word in case.expect_any if word in outcome.text), "")
        findings.append(
            Finding(
                bool(hit),
                f"回答里出现 {'/'.join(case.expect_any)} 之一",
                "" if hit else f"实际回答：{outcome.text[:60]}",
            )
        )

    # 8. 结构断言：回答得符合某条正则
    #
    # 比词表（第 7 条）稳：词表是在追措辞的尾巴，而"如实说没搜到"这种判断
    # 真正要的是**结构**——"否定词 + 结果词"出现在同一行，她怎么说都行。
    # 详见 cases.py 里 TextPattern 那段。
    for item in case.expect_pattern:
        hit = re.search(item.pattern, outcome.text) is not None
        label = item.label or f"回答符合 {item.pattern}"
        findings.append(
            Finding(hit, label, "" if hit else f"实际回答：{outcome.text[:60]}")
        )

    # 9. 引用是不是真的来自工具结果
    #
    # 这是**唯一能自动判的幻觉检查**：prompt 里写着"链接只能来自搜索结果"，
    # 而"编出处"恰好是 agent 最常见的幻觉形态。能编程验证就别浪费。
    if case.check_citations:
        haystack = "\n".join(outcome.tool_outputs)
        invented = [url for url in urls_in(outcome.text) if url not in haystack]
        findings.append(
            Finding(
                not invented,
                "回答里的链接都来自工具结果",
                f"疑似编出来的：{'、'.join(invented[:3])}",
            )
        )

    return findings
