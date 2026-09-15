"""用第二个 agent 把这次对话整理成一篇结构化笔记。

为什么需要第二个 agent：主 agent 的上下文只有最近若干条，**它看不到自己的完整
历史**。要总结"这次聊了什么"，必须直接去读会话库，再交给一个"只有总结任务、
没有工具、指令完全不同"的 agent。

这就是多 agent 最朴素的形式——不是"多个角色在聊天"，而是**不同的任务用不同的
上下文和不同的指令**。总结者的指令在 prompts/summarizer.md 里。
"""

from __future__ import annotations

from agents import Agent, Runner, Session

from .config import Settings
from .contracts import SummarizeError
from .prompt import load_summarizer
from .transcript import MIN_TRANSCRIPT_CHARS, render_transcript

# 总结时要读多少条会话记录。
#
# 这个参数不能省：SQLiteSession 默认只给 session_settings.limit 条（我们设的 40），
# 那只够"接着聊"，不够"回顾全程"。显式传 limit 才会覆盖它。
SUMMARY_ITEMS_LIMIT = 400


def build_summarizer(settings: Settings) -> Agent:
    """总结者：没有工具，只有一段专门写总结的指令。"""
    return Agent(
        name="Divana 总结者",
        instructions=load_summarizer(),
        model=settings.model,
    )


async def summarize_session(settings: Settings, session: Session) -> str:
    """读会话记录 -> 交给总结者 -> 返回 markdown。

    注意这里**不传 session**给 Runner：总结是一次性的旁路调用，不该写回对话，
    也不该被对话的历史影响。
    """
    items = await session.get_items(limit=SUMMARY_ITEMS_LIMIT)
    transcript = render_transcript(items)
    if len(transcript) < MIN_TRANSCRIPT_CHARS:
        raise SummarizeError("这次对话内容太少，没什么好总结的")

    result = await Runner.run(build_summarizer(settings), transcript)
    output = (result.final_output or "").strip()
    if not output:
        raise SummarizeError("总结者没有返回内容")
    return output
