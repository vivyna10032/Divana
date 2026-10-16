"""服务层：把"能力"和"怎么显示"分开。

cli.py 负责读键盘和打印；这一层只管干活、返回结构化结果。前端接进来的时候，
换一个调用者就行，这里的逻辑一个字都不用改。

**没有这一层，前端就会变成"再抄一份 cli.py"**，然后两套逻辑各自改、各自漂移——
这是这类项目最常见的死法。所以抽层是做前端真正的第一步。

流式和普通模式共用同一段结果解析（`to_reply`），只有"怎么调模型"不一样。
"""

from __future__ import annotations

import time
from pathlib import Path

from agents import MaxTurnsExceeded, Runner, Session, ToolCallItem
from openai import APIConnectionError, APITimeoutError
from openai.types.responses import ResponseTextDeltaEvent

from .agent import build_agent
from .config import Settings
from .context import DivanaContext, build_context
from .contracts import (
    EventCallback,
    PracticeView,
    Reply,
    Summary,
    TextDelta,
    ToolCall,
    ToolCalled,
)
from .digest import DigestInfo, list_digests, read_digest
from .notes import NoteInfo
from .plan import PlanProgress
from .practice import build_view
from .search import SearchResult
from .session import (
    DEFAULT_SESSION_ID,
    HISTORY_LIMIT,
    SessionInfo,
    delete_session as remove_session,
    list_sessions,
    open_session,
)
from .summarize import summarize_session
from .trash import trash_text
from .transcript import history_messages, split_title
from .usage import UsageStore, record, take_retries


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
        # 日常用量台账。留一个实例（而不是每次现建）是为了测试能塞一个临时路径进来。
        self.usage = UsageStore()

    # ---------------------------------------------------------- 对话

    async def ask(self, text: str, *, on_event: EventCallback | None = None) -> Reply:
        """问一句，拿回答案。

        传了 `on_event` 就走流式：边生成边回调（文字片段 + 工具调用），
        调用方可以立刻显示出来。不传就等整段生成完再返回。
        **两条路的最终结果一样**，区别只在"什么时候看到字"。

        两条路都把用量记进台账（见 usage.py）；两种**可预期的失败**会变成一句人话，
        而不是让异常一路飙到界面上去：

        - 她绕圈绕到上限（`max_turns`）——是"这个问题太难/太糊"的信号；
        - 接口连不上或超时（重试也救不回来）——是"网络/代理"的信号。

        其余异常照旧往外抛：401、参数写错这类是**配置或代码的问题**，必须响，
        不能让一句"抱歉我出错了"把它盖住。
        """
        started = time.monotonic()
        retries = take_retries()   # 先清零：这样拿到的就是"这一轮"重试了几次

        try:
            if on_event is None:
                result = await Runner.run(
                    self.agent,
                    text,
                    session=self.session,
                    context=self.context,
                    max_turns=self.settings.max_turns,
                )
            else:
                result = Runner.run_streamed(
                    self.agent,
                    text,
                    session=self.session,
                    context=self.context,
                    max_turns=self.settings.max_turns,
                )
                async for event in result.stream_events():
                    item = getattr(event, "item", None)
                    if isinstance(item, ToolCallItem):
                        on_event(ToolCalled(tool_call_from(item)))
                        continue
                    data = getattr(event, "data", None)
                    if isinstance(data, ResponseTextDeltaEvent) and data.delta:
                        on_event(TextDelta(data.delta))
        except MaxTurnsExceeded:
            return self._failed_reply(
                "turns",
                started,
                retries,
                f"这一轮她绕了 {self.settings.max_turns} 步还没收住，我先打断了。"
                "把问题拆小一点再问一次，或者直接看她卡在哪一步（轨迹在网页上）。",
            )
        except (APITimeoutError, APIConnectionError) as exc:
            tried = self.settings.max_retries + 1
            return self._failed_reply(
                "network",
                started,
                retries,
                f"模型接口这一轮没通（一共试了 {tried} 次）：{exc}。"
                "先看一眼网络或代理，再问一次就行——这一轮没有算进对话。",
            )

        reply = to_reply(result)
        record(
            result,
            kind="chat",
            model=self.settings.model,
            seconds=time.monotonic() - started,
            retries=retries,
            tools=tuple(call.name for call in reply.tool_calls),
            store=self.usage,
        )
        return reply

    def _failed_reply(
        self, error: str, started: float, retries: int, message: str
    ) -> Reply:
        """把"她没答上来"变成一句人话，同时照记账。

        记账这一步不能省：失败的那一轮没有 token（它根本没跑完），但它**发生过**——
        台账里留着它，你才答得出"这周她坏了几次、坏在哪一类"。
        """
        record(
            None,
            kind="chat",
            model=self.settings.model,
            seconds=time.monotonic() - started,
            retries=retries,
            error=error,
            store=self.usage,
        )
        return Reply(text=message)

    # ---------------------------------------------------------- 总结

    async def summarize(self) -> Summary:
        """把这次对话整理成笔记并落盘。失败会抛 SummarizeError。"""
        markdown = await summarize_session(self.settings, self.session)
        title, body = split_title(markdown)
        note = self.context.notes.save(title, body, ["conversation", "summary"])
        return Summary(markdown=markdown, note=note)

    # ---------------------------------------------------------- 会话

    def list_sessions(self) -> list[SessionInfo]:
        """所有会话，最近用过的在前。"""
        return list_sessions()

    async def history(self, limit: int = HISTORY_LIMIT) -> list[tuple[str, str]]:
        """当前会话的历史对话（只有人和助手说的话）。"""
        items = await self.session.get_items(limit=limit)
        return history_messages(items)

    def switch_session(self, session_id: str) -> None:
        """换一段对话：关掉旧的、开新的。历史由各自的 session 管。

        传一个从没用过的 id 就是开新会话——SDK 会在这条会话第一次写入时建记录。
        """
        session_id = session_id.strip()
        if not session_id or session_id == self.session_id:
            return
        self.session.close()
        self.session = open_session(session_id)
        self.session_id = session_id

    # ---------------------------------------------------------- 删除（都进回收站）

    def delete_session(self, session_id: str) -> Path:
        """删一个会话。删的要是当前会话，就把当前会话换到还剩下的那个。"""
        saved = remove_session(session_id)
        if session_id == self.session_id:
            remaining = [info.session_id for info in self.list_sessions()]
            target = remaining[0] if remaining else DEFAULT_SESSION_ID
            self.session.close()
            self.session = open_session(target)
            self.session_id = target
        return saved

    def delete_note(self, name: str) -> Path:
        return self.context.notes.delete(name)

    def delete_review(self, title: str) -> Path:
        """删掉复盘记录里的一次复盘。"""
        removed = self.context.plan.remove_block("复盘记录", title)
        return trash_text("review", title, removed)

    def delete_stage(self, name: str) -> Path:
        """删掉路线图里的一个阶段（连同它的里程碑）。"""
        removed = self.context.plan.remove_block("路线图", name)
        return trash_text("stage", name, removed)

    def list_reviews(self) -> list[tuple[str, str]]:
        """复盘记录里的每一条 (标题, 正文)，**新的在前**（界面想先看最近的）。"""
        return list(reversed(self.context.plan.list_blocks("复盘记录")))

    # ---------------------------------------------------------- 早报

    def list_digests(self) -> list[DigestInfo]:
        """所有早报，新的在前。"""
        return list_digests()

    def read_digest(self, name: str) -> tuple[DigestInfo, str]:
        return read_digest(name)

    # ---------------------------------------------------------- 只看不写

    def read_profile(self) -> str:
        return self.context.profile.read()

    def read_plan(self) -> str:
        return self.context.plan.read()

    # ---------------------------------------------------------- 练习（考考我）
    def practice_state(self) -> PracticeView:
        """练习页要的全部内容。没有题目时是空 view，不是报错。"""
        return build_view(self.context.practice)

    async def start_quiz(self, topic: str = "") -> Reply:
        """让她出一份小测。

        **走的是同一条对话通路**：页面按钮和你在聊天里说"考考我"没有任何区别——
        所以 prompt、工具、行为只有一份，不会出现"网页版偷偷弱一点"。
        """
        asked = f"考考我：{topic.strip()}" if topic.strip() else "考考我，就考我最近在学的"
        return await self.ask(asked)

    async def submit_answers(self, text: str) -> Reply:
        """把答案交给她批改。同样走对话通路——判定必须由她（模型）做，
        程序只能比字符串，那正是我们刚在评测里修掉的"词表判据"。"""
        return await self.ask(f"我的答案：\n{text.strip()}")

    def plan_progress(self) -> PlanProgress:
        """路线图的完成情况：分阶段的里程碑列表 + 已完成/总数/百分比。"""
        return self.context.plan.progress()

    def list_notes(self) -> list[NoteInfo]:
        return self.context.notes.list_all()

    def search_notes(
        self, query: str, limit: int = 5, *, tag: str = ""
    ) -> list[tuple[NoteInfo, str]]:
        return self.context.notes.search(query, limit, tag=tag)

    def read_note(self, name: str) -> NoteInfo:
        return self.context.notes.read(name)

    def search_web(self, query: str) -> list[SearchResult]:
        return self.context.search.search(query)

    # ---------------------------------------------------------- 收尾

    def close(self) -> None:
        self.session.close()
