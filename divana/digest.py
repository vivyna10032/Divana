"""早报：查最近的 AI 动态，按学习者的方向挑出跟他有关的，写成一份短的。

和复盘是同一类东西——**你不在的时候它自己跑，跑完留个结果给你看**。所以机制整套
复用：间隔判定、后台任务、失败留痕、状态文件。区别只在"跑什么"。

一个设计选择：**搜索工具交给早报 agent 自己用**，而不是我们定死几个查询词。
它能看到学习者的画像和计划，让它自己决定搜什么，比写死"AI agent 最新进展"
这种泛词准得多。

agents 的导入放在函数里，纯函数（拼简报、存文件、读列表）才能离线测。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .config import Settings
from .prompt import format_today, load_digester
from .state import DIGEST_ERROR, LAST_DIGEST, update_state
from .storage import atomic_write

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIGEST_DIR = PROJECT_ROOT / "vault" / "digest"

# 多久出一期（天）
DIGEST_INTERVAL_DAYS = 1

# 简报里列最近几期的标题，免得天天重复同一个话题
RECENT_TITLES = 5


class DigestError(RuntimeError):
    """可预期的失败：搜不到东西、或者早报撰写者没返回内容。"""


@dataclass(frozen=True)
class DigestInfo:
    """一期早报的元信息。"""

    date: str  # YYYY-MM-DD
    title: str
    path: Path


@dataclass(frozen=True)
class DigestResult:
    markdown: str
    path: Path


def compose_brief(
    *,
    today_text: str,
    goal: str,
    level: str,
    weak: str,
    plan_now: str,
    plan_next: str,
    recent_titles: str,
) -> str:
    """拼给早报撰写者的简报：他是谁、在学什么、最近报过什么。

    全是字符串进、字符串出，不碰文件也不碰模型，所以能直接测。
    """
    return (
        f"今天是 {today_text}。\n\n"
        "# 这位学习者的情况\n\n"
        f"目标：{goal or '（空）'}\n\n"
        f"当前水平：{level or '（空）'}\n\n"
        f"薄弱点：{weak or '（空）'}\n\n"
        f"现在在学：{plan_now or '（空）'}\n\n"
        f"下一步：{plan_next or '（空）'}\n\n"
        "# 最近几期早报报过什么（今天别重复这些话题）\n\n"
        f"{recent_titles or '（这是第一期）'}\n"
    )


def _date_in_name(path: Path) -> str:
    match = re.match(r"(\d{4}-\d{2}-\d{2})", path.name)
    return match.group(1) if match else ""


def _title_of(text: str, fallback: str) -> str:
    """取正文里第一个标题当标题，取不到就用日期。"""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title
    return fallback


def list_digests(digest_dir: Path | None = None) -> list[DigestInfo]:
    """列出所有早报，新的在前。目录不存在就返回空列表（第一期之前就是这样）。"""
    target = Path(digest_dir) if digest_dir is not None else DIGEST_DIR
    if not target.is_dir():
        return []

    infos: list[DigestInfo] = []
    for path in sorted(target.glob("*.md"), reverse=True):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        stamp = _date_in_name(path) or path.stem
        infos.append(DigestInfo(date=stamp, title=_title_of(text, stamp), path=path))
    return infos


def read_digest(name: str, digest_dir: Path | None = None) -> tuple[DigestInfo, str]:
    """按日期或文件名读一期早报。

    和笔记那边一样的规矩：**输入只用来在已枚举的文件里做匹配**，不参与路径拼接，
    所以 `../../.env` 这种输入天然读不到东西。
    """
    wanted = name.strip().lower()
    if not wanted:
        raise DigestError("要读哪一期？给我日期或文件名")

    infos = list_digests(digest_dir)
    if not infos:
        raise DigestError("还没有任何早报")

    for info in infos:
        if wanted in {info.date, info.path.name.lower(), info.path.stem.lower()}:
            return info, info.path.read_text(encoding="utf-8")

    partial = [info for info in infos if wanted in info.date or wanted in info.title.lower()]
    if len(partial) == 1:
        return partial[0], partial[0].path.read_text(encoding="utf-8")
    if not partial:
        raise DigestError(f"没找到「{name}」这一期")
    names = "、".join(info.date for info in partial[:5])
    raise DigestError(f"「{name}」匹配到多期：{names}。用完整日期再试一次。")


def save_digest(
    markdown: str, *, today: date | None = None, digest_dir: Path | None = None
) -> Path:
    """写成 vault/digest/YYYY-MM-DD.md。同一天重复跑就覆盖当天那一期。"""
    moment = today or date.today()
    target = Path(digest_dir) if digest_dir is not None else DIGEST_DIR
    path = target / f"{moment.isoformat()}.md"
    atomic_write(path, markdown.strip() + "\n")
    return path


def mark_digest_done(*, today: date | None = None, state_path: Path | None = None) -> None:
    """记下"这一期出过了"。定时任务靠这个日期判断下次什么时候该跑。"""
    moment = today or date.today()
    update_state(state_path, **{LAST_DIGEST: moment.isoformat(), DIGEST_ERROR: ""})


async def run_digest(
    settings: Settings,
    service,
    *,
    today: date | None = None,
    digest_dir: Path | None = None,
    state_path: Path | None = None,
) -> DigestResult:
    """抓一次热点，写成早报。

    注意这里**给 agent 挂了搜索工具**：它自己决定搜什么。它不传 session，
    所以这次调用不会写回任何对话。
    """
    from agents import Agent, Runner

    from .tools import build_search_tools

    moment = today or date.today()
    plan = service.context.plan
    profile = service.context.profile
    recent = "\n".join(
        f"- {info.date}　{info.title}" for info in list_digests(digest_dir)[:RECENT_TITLES]
    )

    brief = compose_brief(
        today_text=format_today(moment),
        goal=profile.read_section("目标"),
        level=profile.read_section("当前水平"),
        weak=profile.read_section("薄弱点"),
        plan_now=plan.read_section("现在的位置"),
        plan_next=plan.read_section("下一步"),
        recent_titles=recent,
    )

    agent = Agent(
        name="Divana 早报",
        instructions=load_digester(),
        model=settings.model,
        tools=build_search_tools(),
    )
    result = await Runner.run(agent, brief, context=service.context)
    markdown = (result.final_output or "").strip()
    if not markdown:
        raise DigestError("早报撰写者没有返回内容")

    path = save_digest(markdown, today=moment, digest_dir=digest_dir)
    mark_digest_done(today=moment, state_path=state_path)
    return DigestResult(markdown=markdown, path=path)


def main() -> None:
    """手动出一期早报：`python -m divana.digest`（定时任务调的是同一个函数）。"""
    import argparse
    import asyncio

    from dotenv import load_dotenv

    from .config import setup_agents_sdk
    from .scheduler import is_digest_due
    from .service import DivanaService

    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="python -m divana.digest", description="出一期 AI 早报"
    )
    parser.add_argument(
        "--if-due",
        action="store_true",
        help="只在今天还没出过时才跑（给系统定时任务用）",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    setup_agents_sdk(settings)

    if args.if_due and not is_digest_due():
        print("今天已经出过早报了，跳过")
        return

    service = DivanaService(settings)
    try:
        try:
            result = asyncio.run(run_digest(settings, service))
        except DigestError as exc:
            print(f"跳过：{exc}")
            return
        print(result.markdown)
        print()
        print(f"已存成 {result.path}")
    finally:
        service.close()


if __name__ == "__main__":
    main()
