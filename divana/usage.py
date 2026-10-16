"""日常用量台账：她这一轮花了多少钱、慢在哪、有没有抖过。

**为什么评测有账、日常反而没有。** 评测那边早就有 token 明细和成本台账了——
那是为了回答"这次改动有没有用"。可真正花钱的是你每天跟她聊天，而那件事一分钱
都没记过。结果是"实验室里有仪表，生产线上没有"。这个文件补的就是那一半。

它记什么：时间、类型（chat / summary / review / digest）、模型、模型调用次数、
四种 token、耗时、调过哪些工具、重试了几次、有没有失败。

**它不记什么：你说的话、她说的话。** 台账只存数字和工具名——这样这个文件
就算不小心被看到、被传到别处，也不会泄露你聊过什么。隐私不是靠"小心别传"，
是靠"根本没存"。

存成 JSONL（一行一条，追加不重写，坏一行也读得出其余的），放在 data/ 下——
那个目录本来就在 .gitignore 里。

看账：`python -m divana.usage`
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .pricing import Price, format_money

#: 默认台账文件。故意不 import config（那个模块要 import 这个来装重试计数器，
#: 反过来引一次就成了循环）。
USAGE_PATH = Path(__file__).resolve().parent.parent / "data" / "usage.jsonl"

DAY = "%Y-%m-%d"


# ------------------------------------------------------------------ 一条记录


@dataclass(frozen=True)
class UsageEntry:
    """一次"她干活"的用量。一次 = 你问一句，或者一个后台任务跑一轮。"""

    ts: str
    """'2026-10-16 09:12'，本地时间。按天分组直接取前 10 个字符。"""
    kind: str
    """chat（你问的）/ summary / review / digest。"""
    model: str
    calls: int = 0
    """这次跑了几个模型调用——**成本的第一驱动**，比 token 稳。"""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    """input 里命中缓存的部分（子集）。它决定"输入长"到底贵不贵。"""
    reasoning_tokens: int = 0
    seconds: float = 0.0
    tools: tuple[str, ...] = ()
    retries: int = 0
    """传输层重试次数。>0 说明那一轮网络抖过——"慢"和"坏"要能分开。"""
    error: str = ""
    """失败时的一句短码（turns / timeout / api）。空 = 成功。"""

    @property
    def day(self) -> str:
        return self.ts[:10]

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def ok(self) -> bool:
        return not self.error

    def cost(self, price: Price | None = None) -> float:
        return (price or Price()).cost(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_tokens=self.cached_tokens,
        )

    def to_json(self) -> str:
        raw = asdict(self)
        raw["tools"] = list(self.tools)   # 元组 → 列表，JSON 才认
        return json.dumps(raw, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: dict) -> "UsageEntry":
        """从一行 JSON 还原。**认不出来的字段直接忽略**，别让以后加字段把老账读挂。"""
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {key: value for key, value in raw.items() if key in known}
        clean["tools"] = tuple(clean.get("tools") or ())
        return cls(**clean)


# ------------------------------------------------------------------ 存


class UsageStore:
    """台账文件。追加一行就完事，不需要读改写。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else USAGE_PATH

    def append(self, entry: UsageEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry.to_json() + "\n")

    def entries(self) -> list[UsageEntry]:
        """读全部。坏行跳过——台账是附录，不该因为一行坏了就整份读不出来。"""
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return []

        made: list[UsageEntry] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                with suppress(TypeError):
                    made.append(UsageEntry.from_json(raw))
        return made


# ------------------------------------------------------------------ 记一笔


def entry_from(
    result,
    *,
    kind: str,
    model: str,
    seconds: float,
    retries: int = 0,
    tools: tuple[str, ...] = (),
    error: str = "",
    now: datetime | None = None,
) -> UsageEntry:
    """从一次 Runner 的结果里抽出用量。

    用 `getattr` 层层往下摸、每层都给默认值：这一层不值得为了几个数字去 import
    整个 agents 栈，而且 SDK 偶尔改名时这里只会少一个数，不会把整轮对话带崩。
    """
    usage = getattr(getattr(result, "context_wrapper", None), "usage", None)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    return UsageEntry(
        ts=stamp,
        kind=kind,
        model=model,
        calls=int(getattr(usage, "requests", 0) or 0),
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cached_tokens=int(
            getattr(getattr(usage, "input_tokens_details", None), "cached_tokens", 0) or 0
        ),
        reasoning_tokens=int(
            getattr(
                getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0
            )
            or 0
        ),
        seconds=round(seconds, 1),
        tools=tuple(tools),
        retries=retries,
        error=error,
    )


def record(
    result,
    *,
    kind: str,
    model: str,
    seconds: float,
    retries: int = 0,
    tools: tuple[str, ...] = (),
    error: str = "",
    store: UsageStore | None = None,
) -> UsageEntry:
    """记一笔，并把这一笔返回。

    **凡是调用模型的地方都该走它**（聊天、总结、复盘、早报四个入口）。漏掉一个，
    台账就是残的——而你不会发现，只会觉得"怎么这么便宜"。
    """
    made = entry_from(
        result,
        kind=kind,
        model=model,
        seconds=seconds,
        retries=retries,
        tools=tools,
        error=error,
    )
    (store or UsageStore()).append(made)
    return made


# ------------------------------------------------------------------ 数一数重试

#: openai 的客户端在重试前会打一行 "Retrying request in X seconds"。这一行就是
#: "刚才抖了一下"的唯一痕迹——用户只感觉到慢，看不到它。
RETRY_MARK = "Retrying request"


class RetryWatcher(logging.Handler):
    """挂在 `openai` 这个 logger 上，只数重试，不打印。

    为什么要它：把"**这次慢**"和"**网络在抖**"分开。不然你只会看到她今天特别慢，
    然后开始怀疑模型或者自己的代码——而真相可能只是出口网络抖了三下。
    """

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if RETRY_MARK in record.getMessage():
                self.count += 1
        except Exception:   # 记账失败绝不该影响一次真实调用
            return


_WATCHER = RetryWatcher()


def watch_retries() -> RetryWatcher:
    """装好计数器（幂等：重复调用不会挂上第二个 handler）。"""
    logger = logging.getLogger("openai")
    if _WATCHER not in logger.handlers:
        logger.addHandler(_WATCHER)
        # openai 的 INFO 级日志默认不会发出来（根 logger 是 WARNING）。想收到那一行，
        # 就得把它的级别放开；同时别再往上传，免得刷你一屏。
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return _WATCHER


def take_retries() -> int:
    """取走并清零。**每轮开工前调一次**，这样拿到的就是"这一轮重试了几次"。"""
    count = _WATCHER.count
    _WATCHER.count = 0
    return count


# ------------------------------------------------------------------ 汇总


@dataclass(frozen=True)
class DayRow:
    day: str
    turns: int
    cost: float


def summarize(
    entries: list[UsageEntry],
    *,
    days: int = 7,
    today: date | None = None,
    price: Price | None = None,
) -> dict:
    """把一串账压成几个能直接说出口的数。"""
    table = price or Price()
    stamp = (today or date.today()).strftime(DAY)
    since = (datetime.strptime(stamp, DAY) - timedelta(days=days - 1)).strftime(DAY)

    by_day: dict[str, DayRow] = {}
    for entry in entries:
        row = by_day.get(entry.day)
        if row is None:
            by_day[entry.day] = DayRow(entry.day, 1, entry.cost(table))
        else:
            by_day[entry.day] = DayRow(row.day, row.turns + 1, row.cost + entry.cost(table))

    window = [by_day[day] for day in sorted(by_day) if day >= since]
    today_row = by_day.get(stamp)
    kinds: dict[str, list[float]] = {}
    for entry in entries:
        bucket = kinds.setdefault(entry.kind, [0, 0.0])
        bucket[0] += 1
        bucket[1] += entry.cost(table)

    spent = sum(entry.cost(table) for entry in entries)
    seconds = sum(entry.seconds for entry in entries)
    calls = sum(entry.calls for entry in entries)
    return {
        "turns": len(entries),
        "cost": spent,
        "input_tokens": sum(entry.input_tokens for entry in entries),
        "output_tokens": sum(entry.output_tokens for entry in entries),
        "cached_tokens": sum(entry.cached_tokens for entry in entries),
        "reasoning_tokens": sum(entry.reasoning_tokens for entry in entries),
        "calls": calls,
        "seconds": seconds,
        "avg_seconds": seconds / len(entries) if entries else 0.0,
        "avg_cost": spent / len(entries) if entries else 0.0,
        "avg_calls": calls / len(entries) if entries else 0.0,
        "window": window,
        "days": days,
        "today": today_row,
        "by_kind": kinds,
        "worst": max(entries, key=lambda one: one.cost(table), default=None),
        "retried": sum(1 for entry in entries if entry.retries),
        "failed": [entry for entry in entries if not entry.ok],
        "price": table,
    }


def render_report(
    entries: list[UsageEntry],
    *,
    days: int = 7,
    today: date | None = None,
    price: Price | None = None,
) -> str:
    """给人看的账。**一眼能回答"今天花了多少、比平时贵吗"。**"""
    if not entries:
        return (
            "台账还是空的。跟她聊几句就有了——\n"
            f"（文件：{USAGE_PATH}；记的是数字，不记聊天内容）"
        )

    table = price or Price()
    data = summarize(entries, days=days, today=today, price=table)
    lines: list[str] = []

    today_row = data["today"]
    if today_row is not None:
        lines.append(
            f"今天（{today_row.day}）：{today_row.turns} 轮　"
            f"{format_money(today_row.cost)}"
        )
    lines.append(f"最近 {data['days']} 天：")
    for row in data["window"]:
        lines.append(
            f"  {row.day}　{row.turns:>3} 轮　{format_money(row.cost)}"
        )

    lines.append("")
    lines.append(f"合计 {data['turns']} 轮　{format_money(data['cost'])}")
    lines.append(
        f"token：input {data['input_tokens']}"
        + (
            f"（{round(100 * data['cached_tokens'] / data['input_tokens'])}% 命中缓存）"
            if data["input_tokens"]
            else ""
        )
        + f" / output {data['output_tokens']}"
        + (
            f"（其中推理 {data['reasoning_tokens']}）"
            if data["reasoning_tokens"]
            else ""
        )
    )
    lines.append(
        f"一轮平均：{data['avg_calls']:.1f} 次模型调用　"
        f"{data['avg_seconds']:.0f} 秒　{format_money(data['avg_cost'])}"
    )

    if data["by_kind"]:
        parts = [
            f"{kind} {count} 轮 {format_money(spent)}"
            for kind, (count, spent) in sorted(data["by_kind"].items())
        ]
        lines.append("按类型：" + "　".join(parts))

    worst = data["worst"]
    if worst is not None:
        doing = "、".join(worst.tools) or "没调工具"
        lines.append(
            f"最贵的一次：{worst.ts} {worst.kind} {format_money(worst.cost(table))}"
            f"（{worst.calls} 次调用、{worst.seconds:.0f} 秒、{doing}）"
        )

    if data["retried"]:
        lines.append(
            f"重试：{data['retried']} 轮里发生过传输层重试"
            "——慢不一定是她慢，那几轮是网络在抖"
        )
    if data["failed"]:
        reasons = "、".join(sorted({entry.error for entry in data["failed"]}))
        lines.append(f"失败：{len(data['failed'])} 轮（{reasons}）")

    lines.append(f"（价格：{table.describe()}）")
    return "\n".join(lines)


# ------------------------------------------------------------------ 命令行


def main() -> None:
    # 中文 Windows 的控制台是 GBK：报告里出现一个它编不出来的字符，print 会直接
    # 抛 UnicodeEncodeError（整条命令崩掉）。和评测那边同样的兜底。
    with suppress(Exception):
        sys.stdout.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(
        prog="python -m divana.usage", description="看日常用量：花了多少、慢在哪"
    )
    parser.add_argument("--days", type=int, default=7, help="逐日那一段看几天，默认 7")
    parser.add_argument("--path", default="", help="台账文件路径（默认 data/usage.jsonl）")
    args = parser.parse_args()

    store = UsageStore(Path(args.path) if args.path else None)
    print(render_report(store.entries(), days=args.days))


if __name__ == "__main__":
    main()
