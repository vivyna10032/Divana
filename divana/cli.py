"""命令行交互入口。"""

from __future__ import annotations

import argparse
import asyncio

from agents import Runner, Session, ToolCallItem
from dotenv import load_dotenv

from .agent import build_agent
from .config import Settings, describe_env_file, setup_agents_sdk
from .context import DivanaContext, build_context
from .search import SearchError, render
from .session import DEFAULT_SESSION_ID, SESSION_DB, open_session
from .summarize import SummarizeError, summarize_session
from .transcript import split_title

EXIT_WORDS = {"exit", "quit", "退出"}


def run_search_command(query: str, context: DivanaContext) -> None:
    """直接调搜索，不经过模型。

    用来把"搜索本身能不能用"和"模型会不会去调它"分开——排查问题时这两件事
    必须能单独验证，否则你永远不知道是哪一环坏了。
    """
    if not query:
        print("\n用法：/search 想搜的内容\n")
        return
    try:
        results = context.search.search(query)
    except SearchError as exc:
        print(f"\n[搜索失败] {exc}\n")
        return
    print(f"\n{render(results)}\n")


async def handle_command(
    raw: str,
    context: DivanaContext,
    settings: Settings,
    session: Session,
) -> None:
    """处理 /profile、/search、/summary、/help 这类本地命令。

    只有 /summary 要花钱（一次总结者的调用），其余都是纯本地操作。
    """
    parts = raw.split(maxsplit=1)
    command = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""

    if command == "/profile":
        print(f"\n--- {context.profile.path} ---")
        print(context.profile.read().strip())
        print("---\n")
    elif command == "/search":
        run_search_command(argument, context)
    elif command == "/summary":
        await run_summary(settings, context, session)
    elif command == "/help":
        print("\n可用命令：")
        print("  /profile           看 Divana 记了你什么（也可以直接编辑那个文件）")
        print("  /search 关键词      不走模型，直接试一次联网搜索")
        print("  /summary           把这次对话整理成一篇笔记，存进 vault/notes/")
        print("  exit               退出，也可以用 quit 或 退出\n")
    else:
        print(f"\n没这个命令：{command}。输入 /help 看看有哪些。\n")


def describe_tool_call(item: ToolCallItem) -> str:
    """把一次工具调用压成一行，让人看得见模型动了什么手。"""
    raw = item.raw_item
    name = getattr(raw, "name", "?")
    arguments = getattr(raw, "arguments", "") or ""
    if len(arguments) > 120:
        arguments = arguments[:120] + "…"
    return f"{name}({arguments})"


async def run_summary(settings: Settings, context: DivanaContext, session: Session) -> None:
    """整理这次对话并存成笔记。失败只提示，不抛出去——别让总结毁掉退出流程。"""
    print("\n正在整理这次对话……（要花一次模型调用）")
    try:
        markdown = await summarize_session(settings, session)
    except SummarizeError as exc:
        print(f"跳过：{exc}\n")
        return
    except Exception as exc:
        print(f"[出错] 整理失败：{type(exc).__name__}: {exc}\n")
        return

    title, body = split_title(markdown)
    print(f"\n{markdown}\n")
    note = context.notes.save(title, body, ["conversation", "summary"])
    print(f"已存成 {note.path}\n")


async def offer_summary(settings: Settings, context: DivanaContext, session: Session) -> None:
    """退出前问一句。默认不总结——不打扰，也不偷偷花调用。"""
    try:
        answer = input("\n要不要把这次对话整理成一篇笔记？(y/N) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if answer in {"y", "yes", "是", "好"}:
        await run_summary(settings, context, session)


async def chat(settings: Settings, session_id: str) -> None:
    """一轮一轮地聊下去。历史交给 session 存取，我们只管收发。"""
    context = build_context(settings)
    divana = build_agent(settings, context)
    session = open_session(session_id)

    print(f"Divana 已就绪（model: {settings.model}，会话: {session_id}）")
    print(f"  配置来源: {describe_env_file()}")
    print(f"  对话存档: {SESSION_DB}")
    print(f"  学习者画像: {context.profile.path}")
    print(f"  联网搜索: {context.search.status()}")
    print("输入 /help 看命令，exit 退出。\n")

    try:
        while True:
            try:
                user_input = input("你 > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if user_input in EXIT_WORDS:
                await offer_summary(settings, context, session)
                break
            if not user_input:
                continue
            if user_input.startswith("/"):
                await handle_command(user_input, context, settings, session)
                continue

            try:
                # 关键点：这里只传"这一轮的新输入"，历史由 session 自动带上、
                # 聊完自动写回。老写法要自己维护 history 列表，现在不用了。
                result = await Runner.run(
                    divana,
                    user_input,
                    session=session,
                    context=context,
                )
            except Exception as exc:
                # 网络抖动、限流、超时都会走到这里。这一轮不落库，但会话还在，
                # 用户直接重说一遍就行，不用从头开始。
                print(f"\n[出错] {type(exc).__name__}: {exc}")
                print("这一轮没记录下来，你可以直接重说一遍。\n")
                continue

            # 把这一轮里模型调用的工具打出来。排查问题、理解 agent 都靠它：
            # 你能直观看到"她刚才到底记了什么"。
            for item in result.new_items:
                if isinstance(item, ToolCallItem):
                    print(f"[工具] {describe_tool_call(item)}")

            print(f"\nDivana > {result.final_output}\n")
    finally:
        try:
            session.close()
        except Exception as exc:
            print(f"[提示] 关闭会话时出错：{exc}")


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="python -m divana",
        description="Divana：陪伴式 AI 学习助手",
    )
    parser.add_argument(
        "--session",
        default=DEFAULT_SESSION_ID,
        help=f"会话名，默认 {DEFAULT_SESSION_ID}。换个名字就换一段记忆",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    setup_agents_sdk(settings)
    asyncio.run(chat(settings, args.session))
