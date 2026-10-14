"""服务层和显示层之间的数据契约。

全是纯数据（dataclass）和纯错误类型，**不依赖 agents、不依赖任何 web 框架**。
这么放有两个实际好处：

1. 终端和网页都能安全地 import 它们，不会因为"我只想读个类型"就把整个
   agent 栈拖进来。
2. 它们可以离线测试——网页那一层就能拿假数据跑起来验证（见 tests/test_webapp.py）。

抽出来的理由和前几次一样：纯逻辑和框架胶水分开。这次是被"webapp 导不进来"
逼出来的，而不是提前设计好的。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .notes import Note


@dataclass(frozen=True)
class ToolCall:
    """模型这一轮调用的一次工具。只带数据，怎么显示由调用方决定。"""

    name: str
    arguments: str


@dataclass(frozen=True)
class TextDelta:
    """模型正在吐字。"""

    text: str


@dataclass(frozen=True)
class ToolCalled:
    """模型刚决定调用某个工具（还没执行完）。"""

    call: ToolCall


# 流式过程中会往外抛的事件。前端拿到它就能直接转成 SSE 消息。
AskEvent = TextDelta | ToolCalled
EventCallback = Callable[[AskEvent], None]


@dataclass
class Reply:
    """一次回答的结果。"""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class PracticeQuestionView:
    """练习页上的一道题。

    **故意不带参考要点**：要点是批改时对照用的，发到页面上就等于把答案给了他。
    """

    index: int
    prompt: str
    verdict: str = ""
    """对 / 半对 / 错。空字符串表示还没批改。"""

    comment: str = ""
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class PracticeView:
    """练习页要的全部内容。

    还没出题时返回一个**空 view**（而不是报错）——页面第一句话该是
    "去对话里说『考考我』"，不是一屏红色错误。
    """

    has_quiz: bool = False
    topic: str = ""
    created_at: str = ""
    graded: bool = False
    questions: tuple[PracticeQuestionView, ...] = ()
    right: int = 0
    half: int = 0
    wrong: int = 0
    percent: int = 0

    @property
    def total(self) -> int:
        return len(self.questions)


@dataclass
class Summary:
    """整理出来的总结：既有正文（用于显示），也有落盘位置。"""

    markdown: str
    note: Note


class SummarizeError(RuntimeError):
    """可预期的总结失败：没什么可总结的、或者模型没返回内容。"""
