"""确定性检查：只看她**做了什么**，不看她说得好不好。

这是评测里最可信的一层——零成本、零噪声、可重复。所以第一版只做这一层。
模型裁判留到以后：它贵、有噪声，而且判"事实对不对"最不可靠。

检查器只吃一个 `Outcome` 对象（纯数据），所以它自己也能离线测。
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

from .cases import Case, FileExpectation

# URL 后面常跟着中文标点，抠出来之后要截掉
_URL = re.compile(r"https?://[^\s)\]<>\"'，。；、]+")

# 报告里回显"实际回答"时留多少字。原来是 60——太短了，长回答里真正出问题的那句
# 常常在 60 字之后，看起来就像"被截断了"。完整内容去 evals/trajectory/ 看。
PREVIEW_CHARS = 200


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: str


@dataclass(frozen=True)
class Step:
    """轨迹里的一步。`kind` 决定哪些字段有意义：

    - `turn`  ：用户这一轮的输入，看 `text`
    - `tool`  ：一次工具调用，看 `name` / `arguments` / `output`
    - `reply` ：她这一轮的最终回答，看 `text`

    为什么要按顺序记下来：报告里只有"哪条断言挂了"，看不出**她绕到哪去了**。
    排查"同一个工具调了三次""读了不该读的东西"这类退化，必须看轨迹。
    """

    kind: str
    text: str = ""
    name: str = ""
    arguments: str = ""
    output: str = ""


@dataclass(frozen=True)
class Outcome:
    """一次运行的原始结果。检查器只看这个对象。"""

    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_outputs: tuple[str, ...] = ()
    tokens: int = 0
    requests: int = 0
    files: tuple["FileSnapshot", ...] = ()
    steps: tuple["Step", ...] = ()

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(call.name for call in self.tool_calls)


@dataclass(frozen=True)
class FileSnapshot:
    """跑完之后，临时 vault 里某个文件的样子（路径相对 vault）。

    文件内容是**跑完就拍下来**递给检查器的，检查器自己不去读盘——这样"读文件"
    和"判断言"两件事分开，后者（`_file_findings`）能离线测。
    """

    path: str
    text: str


@dataclass(frozen=True)
class Finding:
    """一条断言的结论。报告里逐条列出来，失败那条就是排查入口。"""

    ok: bool
    label: str
    detail: str = ""


def urls_in(text: str) -> list[str]:
    """把文本里的链接抠出来（去掉尾部标点）。"""
    return [raw.rstrip(".,;:)）】") for raw in _URL.findall(text)]


def _file_problems(spec: FileExpectation, snapshot: FileSnapshot) -> list[str]:
    """这个文件哪几条不满足。空列表 = 全满足。"""
    text = snapshot.text
    problems: list[str] = []
    for word in spec.contains:
        if word not in text:
            problems.append(f"缺「{word}」")
    for word in spec.not_contains:
        if word in text:
            problems.append(f"出现了不该有的「{word}」")
    if spec.pattern and not re.search(spec.pattern, text, re.MULTILINE):
        problems.append(f"不匹配 {spec.pattern}")
    if spec.not_pattern and re.search(spec.not_pattern, text, re.MULTILINE):
        problems.append(f"命中了不该命中的 {spec.not_pattern}")
    return problems


def _file_findings(case: Case, outcome: Outcome) -> list[Finding]:
    """文件副作用断言：她**改对了没**，而不只是"调了工具没"。"""
    findings: list[Finding] = []
    written = "、".join(item.path for item in outcome.files) or "一个都没有"

    for spec in case.expect_files:
        label = spec.label or f"文件 {spec.path}"
        hits = [item for item in outcome.files if fnmatch.fnmatch(item.path, spec.path)]

        if not spec.exists:
            findings.append(
                Finding(
                    not hits,
                    f"{label} 不该被写出来",
                    f"实际：{'、'.join(item.path for item in hits)}",
                )
            )
            continue

        if not hits:
            findings.append(
                Finding(False, f"{label} 被写出来了", f"实际写出来的：{written}")
            )
            continue

        # 匹配到多个文件时，只要有一个同时满足全部条件就算过——
        # 不然她多存一篇笔记就会被误判成失败。
        problems = {item.path: _file_problems(spec, item) for item in hits}
        ok = any(not items for items in problems.values())
        bad = next((path for path, items in problems.items() if items), "")
        findings.append(
            Finding(ok, label, "" if ok else f"{bad}：{'、'.join(problems[bad])}")
        )
    return findings


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

    # 4b. 上限：**单个**工具的调用次数
    #
    # 总量上限会放过一种退化：prompt 要求"一次改完"，她改一节调一次
    # （read_plan → update_plan → update_plan），总数照样没超。这类"同一个工具
    # 反复调"正是 agent 最典型的绕圈形态，所以得单独卡。
    for tool, limit in case.max_calls_per_tool.items():
        count = names.count(tool)
        findings.append(
            Finding(
                count <= limit,
                f"{tool} 最多调 {limit} 次",
                f"实际 {count} 次（{'、'.join(names)}）",
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
                "" if hit else f"实际回答：{outcome.text[:PREVIEW_CHARS]}",
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
            Finding(
                hit, label, "" if hit else f"实际回答：{outcome.text[:PREVIEW_CHARS]}"
            )
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

    # 10. 产物：文件真的写对了吗
    #
    # 放在最后是有意的：前面判的是**过程**（调了哪个工具、说了什么），
    # 这一层判的是**结果**。她调对了工具也可能改错内容，只有看文件才发现得了。
    findings.extend(_file_findings(case, outcome))

    return findings
