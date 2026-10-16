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

# ---------------------------------------------------------------- 三个刹车
#
# 这三条以前全靠 SDK 的默认行为，等于"没有配置"。现在把它们摆到台面上：
# 出了问题能一眼看出是哪一条在起作用，也能在 .env 里按需调。
#
# 单次 HTTP 请求的超时（秒）。DeepSeek 出长答案（带推理）实测要 30 ~ 60 秒，
# 所以给到 120：正常回答绝不会被它打断，真卡住了也不会永远等下去。
# 注意最坏情况是 timeout × (1 + max_retries) 秒。
DEFAULT_TIMEOUT_SECONDS = 120

# 传输层重试次数（openai 客户端自带指数退避）。重试的是"网络抖 / 429 / 5xx"
# 这一类**可能自己好**的失败；参数写错这种，重试一百次也没用。
DEFAULT_MAX_RETRIES = 2

# 一轮对话里最多几次模型调用（防她绕圈）。10 是 SDK 自己的默认值——
# 这里**显式钉住**：上游哪天改了默认值，我们的行为不会跟着漂。
DEFAULT_MAX_TURNS = 10


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


def _read_non_negative_int(name: str, default: int) -> int:
    """读一个 ≥0 的整数。重试次数允许写 0（= 不重试）——那是个合法选择。"""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        raise SystemExit(f"{name} 得是整数，现在写的是：{raw}") from None
    if value < 0:
        raise SystemExit(f"{name} 不能是负数，现在写的是：{raw}")
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
    github_token: str
    timeout_seconds: int
    max_retries: int
    max_turns: int

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
            github_token=os.environ.get("DIVANA_GITHUB_TOKEN", "").strip(),
            timeout_seconds=_read_positive_int(
                "DIVANA_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS
            ),
            max_retries=_read_non_negative_int(
                "DIVANA_MAX_RETRIES", DEFAULT_MAX_RETRIES
            ),
            max_turns=_read_positive_int("DIVANA_MAX_TURNS", DEFAULT_MAX_TURNS),
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

    client = AsyncOpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        # 超时和重试挂在客户端上，而不是每次调用现传：它们描述的是**这个接口有多可靠**，
        # 属于配置，不属于某一次请求。
        timeout=settings.timeout_seconds,
        max_retries=settings.max_retries,
    )
    set_default_openai_client(client, use_for_tracing=False)
    set_default_openai_api("chat_completions")
    set_tracing_disabled(True)

    # 重试是"悄悄发生"的：用户只感觉到慢，看不到刚才抖了一下。挂个计数器，
    # 好让日常用量台账把"这次慢"和"网络在抖"分开（见 usage.py）。
    from .usage import watch_retries

    watch_retries()
