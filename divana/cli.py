"""命令行交互入口。"""

from __future__ import annotations

import argparse
import asyncio

from agents import Runner, ToolCallItem
from dotenv import load_dotenv

from .agent import build_agent
from .config import Settings, setup_agents_sdk
from .context import build_context
from .profile import ProfileStore
from .session import DEFAULT_SESSION_ID, SESSION_DB, open_session

EXIT_WORDS = {"exit", "quit", "退出"}


def handle_command(raw: str, profile: ProfileStore) -> None:
    """处理 /profile、/help 这类本地命令（不发给模型，不花钱）。"""
    command = raw.split(maxsplit=1)[0].lower()

    if command == "/profile":
        print(f"\n--- {profile.path} ---")
        print(profile.read().strip())
        print("---\n")
    elif command == "/help":
        print("\n可用命令：")
        print("  /profile   看 Divana 记了你什么（也可以直接编辑那个文件）")
        print("  exit       退出，也可以用 quit 或 退出\n")
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


async def chat(settings: Settings, session_id: str) -> None:
    """一轮一轮地聊下去。历史交给 session 存取，我们只管收发。"""
    context = build_context(settings)
    divana = build_agent(settings, context)
    session = open_session(session_id)

    search_state = (
        f"{context.search.provider}（已配置）"
        if context.search.configured
        else "未配置，查到不确定的事只会说不确定"
    )
    print(f"Divana 已就绪（model: {settings.model}，会话: {session_id}）")
    print(f"  对话存档: {SESSION_DB}")
    print(f"  学习者画像: {context.profile.path}")
    print(f"  联网搜索: {search_state}")
    print("输入 /help 看命令，exit 退出。\n")

    try:
        while True:
            try:
                user_input = input("你 > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if user_input in EXIT_WORDS:
                break
            if not user_input:
                continue
            if user_input.startswith("/"):
                handle_command(user_input, context.profile)
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
