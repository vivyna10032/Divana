"""命令行交互入口。"""

import asyncio

from agents import Runner
from dotenv import load_dotenv

from .agent import build_agent
from .config import Settings, setup_agents_sdk

EXIT_WORDS = {"exit", "quit", "退出"}


async def chat(settings: Settings) -> None:
    """一轮一轮地聊下去，历史由我们自己保存。"""
    divana = build_agent(settings)
    history: list = []

    print(f"Divana 已就绪（model: {settings.model}）。输入 exit 退出。\n")
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
    settings = Settings.from_env()
    setup_agents_sdk(settings)
    asyncio.run(chat(settings))