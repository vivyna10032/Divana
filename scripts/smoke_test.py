"""最小连通性检查：确认 .env 里的配置能真正调通模型。

用法：python -m scripts.smoke_test
"""

import asyncio

from agents import Runner
from dotenv import load_dotenv

from divana.agent import build_agent
from divana.config import Settings, setup_agents_sdk
from divana.context import build_context


async def main() -> None:
    load_dotenv()
    settings = Settings.from_env()
    setup_agents_sdk(settings)

    context = build_context(settings)
    print(f"model    : {settings.model}")
    print(f"base_url : {settings.base_url}")
    print(f"画像     : {context.profile.path}")
    print(f"搜索     : {context.search.provider}，{'已配置' if context.search.configured else '未配置'}")

    # 不传 session：自检不该往真实的对话存档里写东西
    agent = build_agent(settings, context)
    result = await Runner.run(agent, "用一句话介绍你自己。")
    print(f"Divana   : {result.final_output}")


if __name__ == "__main__":
    asyncio.run(main())
