"""服务层：把"能力"和"怎么显示"分开。

cli.py 负责读键盘和打印；这一层只管干活、返回结构化结果。前端接进来的时候，
换一个调用者就行，这里的逻辑一个字都不用改。

**没有这一层，前端就会变成"再抄一份 cli.py"**，然后两套逻辑各自改、各自漂移——
这是这类项目最常见的死法。所以抽层是做前端真正的第一步。

流式和普通模式共用同一段结果解析（`to_reply`），只有"怎么调模型"不一样。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from agents import Runner, Session, ToolCallItem
from openai.types.responses import ResponseTextDeltaEvent

from .agent import build_agent
from .config import Settings
from .context import DivanaContext, build_context
from .notes import Note, NoteInfo
from .search import SearchResult
from .session import DEFAULT_SESSION_ID, open_session
from .summarize import summarize_session
from .transcript import split_title


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


@dataclass
class Summary:
    """整理出来的总结：既有正文（前端要显示），也有落盘位置。"""

    markdown: str
    note: Note


def tool_call_from(item: ToolCallItem) -> ToolCall:
    """从 SDK 的工具调用条目里抽出名字和参数。"""
    raw = item.raw_item
    return ToolCall(
        name=str(getattr(raw, "name", "") or "未知工具"),
        arguments=str(getattr(raw, "arguments", "") or ""),
    )


def to_reply(result) -> Reply:
    """把 Runner 的结果压成 Reply。流式和非流式共用这一段。"""
    calls = [
        tool_call_from(item)
        for item in result.new_items
        if isinstance(item, ToolCallItem)
    ]
    return Reply(text=str(result.final_output or ""), tool_calls=calls)


class DivanaService:
    """一次运行需要的全部能力。终端、网页、定时任务都调它。"""

    def __init__(self, settings: Settings, session_id: str = DEFAULT_SESSION_ID) -> None:
        self.settings = settings
        self.session_id = session_id
        self.context: DivanaContext = build_context(settings)
        self.agent = build_agent(settings, self.context)
        self.session: Session = open_session(session_id)

    # ---------------------------------------------------------- 对话

    async def ask(self, text: str, *, on_event: EventCallback | None = None) -> Reply:
        """问一句，拿回答案。

        传了 `on_event` 就走流式：边生成边回调（文字片段 + 工具调用），
        调用方可以立刻显示出来。不传就等整段生成完再返回。
        **两条路的最终结果一样**，区别只在"什么时候看到字"。
        """
        if on_event is None:
            result = await Runner.run(
                self.agent, text, session=self.session, context=self.context
            )
            return to_reply(result)

        streamed = Runner.run_streamed(
            self.agent, text, session=self.session, context=self.context
        )
        async for event in streamed.stream_events():
            item = getattr(event, "item", None)
            if isinstance(item, ToolCallItem):
                on_event(ToolCalled(tool_call_from(item)))
                continue
            data = getattr(event, "data", None)
            if isinstance(data, ResponseTextDeltaEvent) and data.delta:
                on_event(TextDelta(data.delta))
        return to_reply(streamed)

    # ---------------------------------------------------------- 总结

    async def summarize(self) -> Summary:
        """把这次对话整理成笔记并落盘。失败会抛 SummarizeError。"""
        markdown = await summarize_session(self.settings, self.session)
        title, body = split_title(markdown)
        note = self.context.notes.save(title, body, ["conversation", "summary"])
        return Summary(markdown=markdown, note=note)

    # ---------------------------------------------------------- 只看不写

    def read_profile(self) -> str:
        return self.context.profile.read()

    def read_plan(self) -> str:
        return self.context.plan.read()

    def list_notes(self) -> list[NoteInfo]:
        return self.context.notes.list_all()

    def search_notes(self, query: str) -> list[tuple[NoteInfo, str]]:
        return self.context.notes.search(query)

    def read_note(self, name: str) -> NoteInfo:
        return self.context.notes.read(name)

    def search_web(self, query: str) -> list[SearchResult]:
        return self.context.search.search(query)

    # ---------------------------------------------------------- 收尾

    def close(self) -> None:
        self.session.close()
