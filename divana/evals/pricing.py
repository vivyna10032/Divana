"""token 换算成钱。

为什么值得单独一个文件：**token 数不是钱。** 同一份 token，单价能差一个数量级——
缓存命中的输入便宜十倍左右，而输出的价格通常是输入的 3 倍。所以"这次省了 3000 token"
这句话几乎没有意义，得问"省的是哪一类"：省 3000 个 output 比省 3000 个命中缓存的
input 值钱得多。

这也解释了为什么成本优化要先砍调用次数：每次调用都要重发一遍人格 prompt 和工具定义
（约 4300 token），那部分是新内容、吃的是**未命中**价，比什么都贵。

价格从 .env 读，单位是"元 / 百万 token"：

    DIVANA_PRICE_IN=4        输入（缓存未命中）
    DIVANA_PRICE_OUT=12      输出（含推理 token）
    DIVANA_PRICE_CACHED=0.5  输入（缓存命中）

没配就用下面的默认值。默认值是写这个文件时 deepseek 的挂牌价——**以官网为准**，
不对就用 .env 覆盖。方向是**宁可高估**：估高了你只是保守，估低了你会在账单上发现真相。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

#: 默认价（元 / 百万 token）。用 .env 覆盖。
DEFAULT_INPUT = 4.0
DEFAULT_OUTPUT = 12.0
DEFAULT_CACHED = 0.5

M_TOKENS = 1_000_000

# 货币符号用**全角**的 ￥（U+FFE5），不是半角的 ¥（U+00A5）。
# 这不是审美问题：中文 Windows 的控制台编码是 GBK（cp936），它编得出全角 ￥，
# 编不出半角 ¥——`print` 一个半角 ¥ 会让整条命令抛 UnicodeEncodeError 崩掉。
# 一个符号把整份评测报告带走，太不划算了。
CURRENCY = "￥"


def _number(raw: str | None, fallback: float) -> float:
    """读一个价格。写错了就当没写——评测的价格标错会让整份台账不可信，
    但也不该因为一个笔误就跑不起来。"""
    if raw is None or not str(raw).strip():
        return fallback
    try:
        value = float(str(raw).strip())
    except ValueError:
        return fallback
    return value if value >= 0 else fallback


@dataclass(frozen=True)
class Price:
    """一份价目表。"""

    input_per_million: float = DEFAULT_INPUT
    output_per_million: float = DEFAULT_OUTPUT
    cached_per_million: float = DEFAULT_CACHED
    currency: str = CURRENCY
    source: str = "默认值"
    """这批数字是哪来的：`.env` 还是内置默认值。报告里会打出来——
    不写清来源，一个过期的价目表会被当成事实。"""

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Price":
        values = os.environ if env is None else env
        given = [
            values.get("DIVANA_PRICE_IN"),
            values.get("DIVANA_PRICE_OUT"),
            values.get("DIVANA_PRICE_CACHED"),
        ]
        return cls(
            input_per_million=_number(given[0], DEFAULT_INPUT),
            output_per_million=_number(given[1], DEFAULT_OUTPUT),
            cached_per_million=_number(given[2], DEFAULT_CACHED),
            source=".env" if any(str(item or "").strip() for item in given) else "默认值",
        )

    @property
    def is_default(self) -> bool:
        return self.source == "默认值"

    def cost(
        self, *, input_tokens: int = 0, output_tokens: int = 0, cached_tokens: int = 0
    ) -> float:
        """这次调用/这条用例花了多少钱（元）。

        `cached_tokens` 是 `input_tokens` 里**已经被缓存命中**的那部分（子集，不是
        额外的一部分）。服务商这么报就这么算——OpenAI 口径如此，DeepSeek 也把
        命中数单列。有些适配器干脆不报（那就是 0），这时整份 input 按未命中价算，
        结果是**偏保守**的，不会让我们低估。
        """
        cached = max(0, min(int(cached_tokens), int(input_tokens)))
        fresh = max(0, int(input_tokens) - cached)
        return (
            fresh * self.input_per_million
            + cached * self.cached_per_million
            + max(0, int(output_tokens)) * self.output_per_million
        ) / M_TOKENS

    def describe(self) -> str:
        """一句话说清用了什么价，好让人一眼看出这数是不是过期的。"""
        return (
            f"{self.currency}{self.input_per_million:g}/百万 input"
            f"（命中 {self.currency}{self.cached_per_million:g}）· "
            f"{self.currency}{self.output_per_million:g}/百万 output"
            f"　价格来源：{self.source}"
        )


def format_money(value: float, currency: str = CURRENCY) -> str:
    """钱要好读。

    默认保留 4 位（单条成本通常在 0.001~0.05 之间，4 位够看趋势）；但比 0.0001
    还小的时候再多给几位——四舍五入成 ￥0.0000 等于把"很便宜"显示成了"不花钱"，
    那种数字读起来比没有还糟。
    """
    if not value:
        return f"{currency}0"
    text = f"{value:.4f}"
    if float(text) == 0:
        text = f"{value:.6f}"
    return f"{currency}{text}"


def format_rate(part: int, whole: int) -> str:
    """百分比，同时把分子分母写出来。**比率和分数要一起给**：
    "45 次里挂 1 次"和"14/15 条通过"说的是两件事，只报一个会被问住。"""
    if not whole:
        return "—"
    return f"{part}/{whole}（{round(100 * part / whole)}%）"
