"""Divana 的运行配置：密钥、接口地址、模型名全部从 .env 读取。

想换模型或换服务商时只改 .env，不用动代码。
"""

import os
from dataclasses import dataclass

# 默认值，可以被 .env 里的同名变量覆盖
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-pro"


@dataclass(frozen=True)
class Settings:
    """一次运行需要的全部配置。"""

    api_key: str
    base_url: str
    model: str

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.environ.get("DIVANA_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise SystemExit(
                "没有找到 API key。请把 .env.example 复制成 .env，并填好 DIVANA_API_KEY。"
            )
        return cls(
            api_key=api_key,
            base_url=os.environ.get("DIVANA_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            model=os.environ.get("DIVANA_MODEL", DEFAULT_MODEL),
        )


def setup_agents_sdk(settings: Settings) -> None:
    """把 Agents SDK 指到 settings 指定的服务上。

    DeepSeek 这类第三方接口只兼容 chat completions，不提供 Responses API，
    所以必须显式切换。use_for_tracing=False 是为了不把这个 key 传给 OpenAI
    的追踪服务。
    """
    from agents import (
        set_default_openai_api,
        set_default_openai_client,
        set_tracing_disabled,
    )
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.api_key, base_url=settings.base_url)
    set_default_openai_client(client, use_for_tracing=False)
    set_default_openai_api("chat_completions")
    set_tracing_disabled(True)