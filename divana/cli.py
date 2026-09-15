"""命令行交互入口。

这个文件只做两件事：**读键盘、打印**。所有实际能力都在 service.py 里——
前端接进来的时候换的是这一层，能力本身一个字都不用动。
"""

from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv

from .config import Settings, describe_env_file, setup_agents_sdk
from .contracts import Reply, TextDelta, ToolCall, ToolCalled
from .search import SearchError, render
from .service import DivanaService
from .session import DEFAULT_SESSION_ID, SESSION_DB
from .summarize import SummarizeError

EXIT_WORDS = {"exit", "quit", "退出"}
YES_WORDS = {"y", "yes", "是", "好"}


def format_tool_call(call: ToolCall, limit: int = 120) -> str:
    """把一次工具调用压成一行。

    显示格式属于这一层——服务层只给数据（名字 + 参数），怎么展示由调用方决定。
    网页那边想画成卡片就画成卡片，不用改服务层。
    """
    arguments = call.arguments
    if len(arguments) > limit:
        arguments = arguments[:limit] + "…"
    return f"{call.name}({arguments})"


def print_banner(service: DivanaService) -> None:
    context = service.context
    print(
        f"Divana 已就绪（model: {service.settings.model}，会话: {service.session_id}）"
    )
    print(f"  配置来源: {describe_env_file()}")
    print(
        f"  文件: 画像 {context.profile.path.name}"
        f" · 计划 {context.plan.path.name}"
        f" · 存档 {SESSION_DB.name}"
        f" · 笔记 {context.notes.notes_dir.name}/"
    )
    print(f"  联网搜索: {context.search.status()}")
    print(
        "  GitHub: "
        + (
            "已配 token（5000 次/小时）"
            if context.github_token
            else "匿名（60 次/小时，按出口 IP 算，走代理容易被别人用光）"
        )
    )
    print("输入 /help 看命令，exit 退出。\n")


def search_command(query: str, service: DivanaService) -> None:
    """直接调搜索，不经过模型——用来把"搜索能不能用"和"模型会不会调"分开。"""
    if not query:
        print("\n用法：/search 想搜的内容\n")
        return
    try:
        results = service.search_web(query)
    except SearchError as exc:
        print(f"\n[搜索失败] {exc}\n")
        return
    print(f"\n{render(results)}\n")


def notes_command(service: DivanaService) -> None:
    notes = service.list_notes()
    if not notes:
        print("\n笔记目录还是空的。\n")
        return
    print(f"\n--- {service.context.notes.notes_dir} ---")
    for info in notes:
        tags = "、".join(info.tags) if info.tags else "无标签"
        print(f"  {info.date or '无日期'}  {info.title}  [{tags}]")
    print(f"\n共 {len(notes)} 篇。\n")


async def summarize_command(service: DivanaService) -> None:
    print("\n正在整理这次对话……（要花一次模型调用）")
    try:
        summary = await service.summarize()
    except SummarizeError as exc:
        print(f"跳过：{exc}\n")
        return
    except Exception as exc:
        print(f"[出错] 整理失败：{type(exc).__name__}: {exc}\n")
        return
    print(f"\n{summary.markdown}\n")
    print(f"已存成 {summary.note.path}\n")


async def handle_command(raw: str, service: DivanaService) -> None:
    """处理本地命令。/summary 要花钱，其余都是纯本地操作。"""
    parts = raw.split(maxsplit=1)
    command = parts[0].lower()
    argument = parts[1].strip() if len(parts) > 1 else ""

    if command == "/profile":
        print(f"\n--- {service.context.profile.path} ---")
        print(service.read_profile().strip())
        print("---\n")
    elif command == "/plan":
        print(f"\n--- {service.context.plan.path} ---")
        print(service.read_plan().strip())
        print("---\n")
    elif command == "/notes":
        notes_command(service)
    elif command == "/search":
        search_command(argument, service)
    elif command == "/summary":
        await summarize_command(service)
    elif command == "/help":
        print("\n可用命令：")
        print("  /profile           看画像（也可以直接编辑那个文件）")
        print("  /plan              看学习计划")
        print("  /notes             列出手上的笔记")
        print("  /search 关键词      不走模型，直接试一次联网搜索")
        print("  /summary           把这次对话整理成一篇笔记")
        print("  exit               退出，也可以用 quit 或 退出\n")
    else:
        print(f"\n没这个命令：{command}。输入 /help 看看有哪些。\n")


async def ask_and_show(service: DivanaService, user_input: str, *, stream: bool) -> Reply:
    """问一句，并把她干活的过程显示出来。

    流式：文字边生成边打印，工具调用在自己的行上——后者发生在正文之前，
    所以读起来是"先说要查什么，再给答案"。
    非流式：等整段生成完，先列工具调用，再打印答案。
    """
    if not stream:
        reply = await service.ask(user_input)
        for call in reply.tool_calls:
            print(f"[工具] {format_tool_call(call)}")
        print(f"\nDivana > {reply.text}\n")
        return reply

    started = False

    def show(event: object) -> None:
        nonlocal started
        if isinstance(event, TextDelta):
            if not started:
                print("\nDivana > ", end="", flush=True)
                started = True
            print(event.text, end="", flush=True)
        elif isinstance(event, ToolCalled):
            print(f"\n[工具] {format_tool_call(event.call)}")

    reply = await service.ask(user_input, on_event=show)
    print("\n")
    return reply


async def offer_summary(service: DivanaService) -> None:
    """退出前问一句。默认不总结——不打扰，也不偷偷花调用。"""
    try:
        answer = input("\n要不要把这次对话整理成一篇笔记？(y/N) ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if answer in YES_WORDS:
        await summarize_command(service)


async def chat(settings: Settings, session_id: str, *, stream: bool = False) -> None:
    """一轮一轮地聊下去。历史交给 session 存取，能力交给 service。"""
    service = DivanaService(settings, session_id)
    print_banner(service)

    try:
        while True:
            try:
                user_input = input("你 > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if user_input in EXIT_WORDS:
                await offer_summary(service)
                break
            if not user_input:
                continue
            if user_input.startswith("/"):
                await handle_command(user_input, service)
                continue

            try:
                await ask_and_show(service, user_input, stream=stream)
            except Exception as exc:
                # 网络抖动、限流、超时都会走到这里。这一轮不落库，但会话还在，
                # 用户直接重说一遍就行，不用从头开始。
                print(f"\n[出错] {type(exc).__name__}: {exc}")
                print("这一轮没记录下来，你可以直接重说一遍。\n")
    finally:
        try:
            service.close()
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
    parser.add_argument(
        "--stream",
        action="store_true",
        help="边生成边显示（默认是等整段生成完）。这条路径比较新，出问题就先不加这个参数",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    setup_agents_sdk(settings)
    asyncio.run(chat(settings, args.session, stream=args.stream))
