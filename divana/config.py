"""Divana 的运行配置：密钥、接口地址、模型名全部从 .env 读取。

想换模型或换服务商时只改 .env，不用动代码。
"""

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 默认值，可以被 .env 里的同名变量覆盖
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-pro"

# 联网搜索是可选的：不配 key 也能跑，只是 search_web 会告诉她"查不了"
DEFAULT_SEARCH_PROVIDER = "tavily"
DEFAULT_SEARCH_MAX_RESULTS = 5


def env_file() -> Path:
    """实际会被读到的那个 .env 文件。

    python-dotenv 是"从当前目录往上找 .env"，所以这里也用它自己的查找逻辑，
    免得我们以为读了 A 文件、其实读了 B 文件。
    """
    try:
        from dotenv import find_dotenv
    except ImportError:
        return PROJECT_ROOT / ".env"
    try:
        found = find_dotenv()
    except Exception:
        return PROJECT_ROOT / ".env"
    return Path(found) if found else PROJECT_ROOT / ".env"


def describe_env_file() -> str:
    """给启动信息用：读的是哪个文件、什么时候改的。

    加上"最后修改时间"是有意的——"我明明填了为什么没生效"这类问题，
    九成是改错了文件或者没保存，把文件名和改动时间摆出来一眼就清楚了。
    """
    path = env_file()
    try:
        stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return f"{path}（不存在，只用了系统环境变量）"
    return f"{path}（最后修改 {stamp}）"


def _read_positive_int(name: str, default: int) -> int:
    """读一个正整数配置。写错了就明确报出来，别让它悄悄用默认值。"""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"{name} 得是整数，现在写的是：{raw}") from None
    if value <= 0:
        raise SystemExit(f"{name} 得是正整数，现在写的是：{raw}")
    return value


@dataclass(frozen=True)
class Settings:
    """一次运行需要的全部配置。"""

    api_key: str
    base_url: str
    model: str
    search_provider: str
    search_api_key: str
    search_max_results: int

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
            search_provider=os.environ.get(
                "DIVANA_SEARCH_PROVIDER", DEFAULT_SEARCH_PROVIDER
            )
            .strip()
            .lower(),
            search_api_key=os.environ.get("DIVANA_SEARCH_API_KEY", "").strip(),
            search_max_results=_read_positive_int(
                "DIVANA_SEARCH_MAX_RESULTS", DEFAULT_SEARCH_MAX_RESULTS
            ),
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
