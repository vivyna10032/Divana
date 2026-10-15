"""护栏校准：`max_tokens` 该写多少，用实测数据算，不用猜。

**为什么需要这个文件。** `max_tokens` 是这套评测里唯一会"过期"的东西：
人格 prompt 和全部工具定义**每次调用都要重发**，所以每加一个工具、每往 prompt 里
添一段规则，所有用例的地板价就一起上移。护栏却是按写它那天的开销算的。

于是就有了这种事（2026-10-15 一天之内咬了四次）：

    [回归] note-search  ← 原来能过，现在挂了（token 不超过 19000）
           实际 19035，超 0.2%

**她什么都没做错**，只是护栏比现在的开销还低。这种假回归最贵：它花掉的不只是跑一次
评测的钱，还有你去读轨迹、怀疑她、甚至去改 prompt 的时间。

**怎么算。** 一条用例允许跑几次模型调用，是 `max_llm_calls` 说了算（那是行为断言）。
护栏不跟着它走，两个断言就会互相打架——而打架时输的永远是护栏。

    建议上限 = 该用例允许的调用次数 × 单次实测均值 × 1.2

**护栏宁松勿紧**：它退化成"防爆表"（比如某个工具一次返回五万 token）。
"绕了几圈"由 `max_llm_calls` / `max_tool_calls` 精确卡住，不需要护栏来代劳。
只调高不调低——调低就会再咬一次。

用法（不需要 API key）：

    python -m divana.evals --calibrate
"""

from __future__ import annotations

from dataclasses import dataclass

from .cases import Case

#: 建议上限要留的余量
MARGIN = 1.2


@dataclass(frozen=True)
class CapSuggestion:
    """一条用例的护栏情况。"""

    case_id: str
    measured: int
    """上一次跑出来的合计 token（0 表示这次没跑到/没有数据）。"""
    calls: int
    per_call: int
    current: int | None
    suggested: int

    @property
    def is_tight(self) -> bool:
        """护栏比按实测算出来的还低——**迟早会假报一次回归**。"""
        return self.current is not None and self.current < self.suggested

    @property
    def has_data(self) -> bool:
        return self.measured > 0 and self.calls > 0


def suggest_cap(
    *, measured: int, calls: int, allowed_calls: int | None
) -> int:
    """按实测数据算这条用例的护栏该是多少。取整到千（省得数字读起来像精确值）。"""
    if measured <= 0 or calls <= 0 or not allowed_calls:
        return 0
    per_call = measured // calls
    return int(round(allowed_calls * per_call * MARGIN, -3))


def suggest_caps(summary: dict, cases: list[Case]) -> list[CapSuggestion]:
    """对着一次运行的结果（baseline 或刚跑完的汇总），逐条给出建议。"""
    known = summary.get("cases", {})
    made: list[CapSuggestion] = []
    for case in cases:
        info = known.get(case.id) or {}
        measured = int(info.get("tokens", 0) or 0)
        calls = int(info.get("llm_calls") or info.get("requests") or 0)
        made.append(
            CapSuggestion(
                case_id=case.id,
                measured=measured,
                calls=calls,
                per_call=measured // calls if calls else 0,
                current=case.max_tokens,
                suggested=suggest_cap(
                    measured=measured,
                    calls=calls,
                    allowed_calls=case.max_llm_calls,
                ),
            )
        )
    return made


def render_caps(suggestions: list[CapSuggestion], *, ran_at: str = "") -> str:
    """给人看的表。**只列需要动的**，其余的报个总数就行——没人愿意读 20 行"没问题"。"""
    tight = [item for item in suggestions if item.has_data and item.is_tight]
    blank = [item for item in suggestions if not item.has_data]

    lines = [
        "护栏校准（max_tokens）" + (f"　依据：{ran_at}" if ran_at else ""),
        "",
    ]
    if tight:
        lines.append(
            f"这 {len(tight)} 条比实测该有的还低——**迟早会假报一次回归**，建议改成："
        )
        lines.append("")
        lines.append(
            f"{_pad('用例', 26)}{_pad('实测', 9)}{_pad('调用', 6)}"
            f"{_pad('上限', 8)}{_pad('建议', 8)}单次均值"
        )
        for item in sorted(tight, key=lambda one: one.current or 0):
            lines.append(
                f"{_pad(item.case_id, 26)}{_pad(str(item.measured), 9)}"
                f"{_pad(str(item.calls), 6)}{_pad(str(item.current), 8)}"
                f"{_pad(str(item.suggested), 8)}{item.per_call}"
            )
        lines.append("")
        lines.append(
            "改之前想一下：护栏是按**实测**算的，不是按「她应该花多少」算的。"
            "如果哪条是她真绕了圈才顶上去的，先去看轨迹，别顺手把数改大。"
        )
    else:
        lines.append("没有需要动的：每条护栏都不低于按实测算出来的值。")

    if blank:
        lines.append("")
        lines.append(f"（{len(blank)} 条没有实测数据，这次没跑到：{'、'.join(item.case_id for item in blank)}）")
    return "\n".join(lines)


def _pad(text: str, width: int) -> str:
    """对齐用：汉字在终端里占两格。和 report.py 里那份是同一个理由。"""
    from .report import _pad as report_pad  # 只有这一个工具函数，复用比复制强

    return report_pad(text, width)
