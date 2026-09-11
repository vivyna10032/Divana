"""命令行交互入口。"""

import asyncio
import os

from agents import Runner
from dotenv import load_dotenv

from .agent import build_agent

EXIT_WORDS = {"exit", "quit", "退出"}


async def chat() -> None:
    """一轮一轮地聊下去，历史由我们自己保存。"""
    divana = build_agent()
    history: list = []

    print("Divana 已就绪。输入 exit 退出。\n")
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

        history.append({"role": "user", "content": user_input})
        result = await Runner.run(divana, history)
        history = result.to_input_list()  # 官方推荐的上下文续接方式
        print(f"\nDivana > {result.final_output}\n")


def main() -> None:
    load_dotenv()
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit(
            "没有找到 OPENAI_API_KEY。"
            "请把 .env.example 复制成 .env 并填入你的 key。"
        )
    asyncio.run(chat())
