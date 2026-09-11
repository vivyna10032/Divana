"""最小连通性检查：确认 .env 里的配置能真正调通模型。

用法：python -m scripts.smoke_test
"""

import asyncio

from agents import Runner
from dotenv import load_dotenv

from divana.agent import build_agent
from divana.config import Settings, setup_agents_sdk


async def main() -> None:
    load_dotenv()
    settings = Settings.from_env()
    setup_agents_sdk(settings)
    print(f"model    : {settings.model}")
    print(f"base_url : {settings.base_url}")
    result = await Runner.run(build_agent(settings), "用一句话介绍你自己。")
    print(f"Divana   : {result.final_output}")


if __name__ == "__main__":
    asyncio.run(main())