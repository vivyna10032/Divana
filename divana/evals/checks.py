"""确定性检查：只看她**做了什么**，不看她说得好不好。

这是评测里最可信的一层——零成本、零噪声、可重复。所以第一版只做这一层。
模型裁判留到以后：它贵、有噪声，而且判"事实对不对"最不可靠。

检查器只吃一个 `Outcome` 对象（纯数据），所以它自己也能离线测。
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, replace

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
    call_id: str = ""
    """这一次调用的唯一编号。

    为什么要它：模型可以**一次要求并行调两个工具**（实测常见：`search_notes` +
    `read_plan` 一起来）。两次调用挨在一起，返回也是两次挨在一起——只按"最近一次
    还没返回的调用"去挂，两个返回就会**互换**。2026-10-15 的轨迹里就是这个现象：
    `read_plan` 的返回写着"没找到和…有关的笔记"，而 `search_notes` 的返回是计划正文。
    轨迹是排查问题的主工具，它串了行，后面所有判断都会跟着跑偏。
    """


def tool_call_id(raw) -> str:
    """从一次工具调用/返回里抠出它的编号。

    两种形状都要接住：SDK 给的是对象（`.call_id`），有些适配器给的是字典
    （`{"call_id": ...}`）。抠不到就返回空串，配对时退回老办法。

    放在这个模块里（而不是 runner）：`checks` 不依赖 agent 栈，所以这段逻辑
    能被离线测试盯住——而它恰恰错过一次，见 `Step.call_id` 的说明。
    """
    if raw is None:
        return ""
    found = getattr(raw, "call_id", None)
    if not found and isinstance(raw, dict):
        found = raw.get("call_id")
    return str(found or "")


def attach_output(steps: list[Step], output: str, call_id: str = "") -> None:
    """把一次工具返回，挂回**它对应的那次调用**上。

    先按 `call_id` 精确配对。为什么不能只按"最近一次还没返回的调用"去找：
    模型可以一次要求并行调两个工具（实测常见：`search_notes` + `read_plan`），
    两次调用挨在一起，两个返回按序回来——用"最近一次没返回的"去接，
    第一个返回就挂到了第二个调用上，两个返回整体互换。

    SDK 不给 call_id 时退回老办法：挂到最后一次还没返回的调用上。串行调用仍然对，
    只有并行调用会错——那种情况下我们也确实无从判断，只能不猜。
    """
    if call_id:
        for index in range(len(steps) - 1, -1, -1):
            step = steps[index]
            if step.kind == "tool" and step.call_id == call_id and not step.output:
                steps[index] = replace(step, output=output)
                return
    for index in range(len(steps) - 1, -1, -1):
        step = steps[index]
        if step.kind == "tool" and not step.output:
            steps[index] = replace(step, output=output)
            return


@dataclass(frozen=True)
class LlmCall:
    """一次模型调用。

    一个用户轮次里常常有好几次——**每次工具调用之后都要再问一次模型**。所以
    "跑了 1 轮"和"调了 5 次模型"是两回事，钱要按后者算。

    `tools` 是这次调用**要求调用**的工具（并行调用时可能多个，通常一个）：
    用来回答"最贵的那一次，她当时在干什么"。
    """

    turn: int
    input_tokens: int
    output_tokens: int
    tools: tuple[str, ...] = ()
    # 推理模型的"思考"也算 output、也要计费。有些服务商不报这个数（那就是 0），
    # 报的时候就单独列出来——不然 output 看着大，你会以为是话多。
    reasoning_tokens: int = 0
    # input 里**被缓存命中**的那部分（是 input_tokens 的子集，不是额外的一份）。
    # 它的单价通常只有未命中的十分之一，所以"输入很长"到底贵不贵，得看这一列。
    # 服务商不报就是 0——那时整份 input 按未命中价算，偏保守，不会低估成本。
    cached_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class Outcome:
    """一次运行的原始结果。检查器只看这个对象。"""

    text: str
    """她**这一轮说给用户的所有文本**（按顺序拼起来），不只是最后一次调用的那段。

    模型可以在"要调工具"的那次响应里先把话说完再调工具（实测那次讲解约 700 token），
    这时 `result.final_output` 只剩收尾那几十个字——只取它会把正文丢掉。
    2026-09-24 就被这个坑骗过：看 final_output 以为"她没回答问题"，其实她答了。
    """
    tool_calls: tuple[ToolCall, ...] = ()
    tool_outputs: tuple[str, ...] = ()
    tokens: int = 0
    requests: int = 0
    files: tuple["FileSnapshot", ...] = ()
    steps: tuple["Step", ...] = ()
    llm_calls: tuple[LlmCall, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0

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

    # 4c. 上限：模型调用次数
    #
    # 这才是"她绕了几圈"的直接度量。一次用户输入往往触发好几次模型调用（每次工具
    # 调用之后都要再问一次），而 token 会被推理开销和输入长度带得上下飘——同一条
    # 用例能差一倍。调用次数只跟"多跑了几轮工具"有关，稳得多。
    # 所以：**行为用这个卡，token 只当"防爆表"的宽松护栏。**
    if case.max_llm_calls is not None:
        calls = len(outcome.llm_calls) or outcome.requests
        findings.append(
            Finding(
                calls <= case.max_llm_calls,
                f"模型调用不超过 {case.max_llm_calls} 次",
                f"实际 {calls} 次",
            )
        )

    # 5. 上限：token ——成本护栏，不是行为断言
    #
    # 它挂了要分成两种，而且必须先分清，否则会白白去查她的轨迹：
    #   · 她真绕圈了 → 看轨迹里多调了几次模型；
    #   · **护栏过期了** → 每次调用都要重发全部工具定义，所以每加一个工具，
    #     "最坏合法路径"就整体上移一次。2026-10-15 就有两条是这么挂的（只超 2%）。
    # 所以这里顺手把"按本项目自己的校准规则该是多少"算出来——让这条失败自己
    # 给出下一步动作，而不是只报一个数字。
    if case.max_tokens is not None and outcome.tokens:
        over = outcome.tokens > case.max_tokens
        detail = f"实际 {outcome.tokens}"
        if over:
            calls = len(outcome.llm_calls) or outcome.requests
            if case.max_llm_calls and calls:
                per_call = outcome.tokens // calls
                # 规则见 README：调用上限 × 实测单次 × 约 20% 余量，只调高不调低
                suggest = int(round(case.max_llm_calls * per_call * 1.2, -3))
                detail += (
                    f"（超 {round(100 * (outcome.tokens / case.max_tokens - 1))}%，"
                    f"跑了 {calls} 次调用、单次均值 {per_call}；"
                    f"按「调用上限 {case.max_llm_calls} × 单次 × 1.2」该是 {suggest}）"
                )
        findings.append(
            Finding(not over, f"token 不超过 {case.max_tokens}", detail)
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

    # 9b. 工具返回里该有什么
    #
    # 有些结论**只有工具能给出**：自测的判定（对 / 半对 / 错）是工具算的，
    # 不是她嘴上说的。她完全可能一边说"你答得不错"、一边让工具记下"错"——
    # 这种偏差，只有查工具返回才看得见。
    if case.expect_tool_output:
        haystack = "\n".join(outcome.tool_outputs) or "（一次工具都没调）"
        for item in case.expect_tool_output:
            hit = re.search(item.pattern, haystack, re.MULTILINE) is not None
            label = item.label or f"工具返回符合 {item.pattern}"
            findings.append(
                Finding(
                    hit,
                    label,
                    "" if hit else f"实际工具返回：{haystack[:PREVIEW_CHARS]}",
                )
            )

    # 10. 产物：文件真的写对了吗
    #
    # 放在最后是有意的：前面判的是**过程**（调了哪个工具、说了什么），
    # 这一层判的是**结果**。她调对了工具也可能改错内容，只有看文件才发现得了。
    findings.extend(_file_findings(case, outcome))

    return findings
