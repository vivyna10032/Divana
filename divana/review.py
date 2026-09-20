"""复盘：把最近一段时间的学习记录整理成一段，追加进学习计划的"复盘记录"。

为什么这件事也需要另起一个 agent（而不是让主 agent 顺手做）：和"总结对话"同一个
道理——素材不在它的视野里。它只看得到当前会话的最近 40 条，而复盘要横跨所有会话、
按时间过滤，还得同时看着计划和画像。

这一版是**手动触发**的闭环。定时器解决的是"什么时候跑"，而"跑什么、写得对不对"
得先能单独验证——不然定时任务出问题时，你不知道是逻辑坏了还是调度坏了。

agents 的导入放在函数里，这样上面那些纯函数（拼素材、排文本）可以离线测。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .config import Settings
from .markdown_store import demote_level2_headings
from .prompt import format_today, load_reviewer
from .session import RecentMessage, recent_messages
from .state import LAST_ERROR, LAST_REVIEW, update_state

# 一份素材最多多少字。一周的对话很容易上万字，得压。
MAX_MATERIAL_CHARS = 12000

# 默认回看几天
DEFAULT_DAYS = 7

# 素材里最多列几篇笔记
NOTES_IN_MATERIAL = 10


class ReviewError(RuntimeError):
    """可预期的失败：这段时间没聊过、或者复盘者没返回内容。"""


@dataclass(frozen=True)
class ReviewResult:
    markdown: str
    days: int
    message_count: int


def render_recent(
    messages: list[RecentMessage], *, max_chars: int = MAX_MATERIAL_CHARS
) -> str:
    """把最近的消息排成一段可读的文本。

    超长时保留最近的，并注明省略了多少条——和总结对话时同样的取舍：
    宁可少给一些，也不要让她拿半份材料当全份讲。
    """
    if not messages:
        return "（这段时间没有新的对话记录）"

    lines = []
    for message in messages:
        who = "你" if message.role == "user" else "Divana"
        # 把对话里自带的二级标题降级：素材本身用 `##` 分节，混起来复盘者会分不清
        # 哪是结构、哪是内容（她回答里经常有"## 结论"这种标题）
        lines.append(f"[{message.at}] {who}：{demote_level2_headings(message.text)}")

    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        if kept and used + len(line) > max_chars:
            omitted = len(lines) - len(kept)
            return f"（更早的 {omitted} 条已省略）\n\n" + "\n".join(reversed(kept))
        kept.append(line)
        used += len(line) + 1
    return "\n".join(reversed(kept))


def compose_material(
    *,
    days: int,
    today: str,
    plan_now: str,
    plan_next: str,
    roadmap: str,
    profile_goal: str,
    profile_level: str,
    profile_weak: str,
    notes: str,
    recent: str,
) -> str:
    """把复盘要看的材料拼成一段文本。

    全是字符串进、字符串出，不碰文件也不碰模型，所以能直接测。
    """
    return (
        f"# 这段时间的学习记录\n\n"
        f"今天是 {today}，下面这段记录覆盖最近 {days} 天。\n\n"
        "## 当前的学习计划\n\n"
        f"现在的位置：{plan_now or '（空）'}\n\n"
        f"下一步：{plan_next or '（空）'}\n\n"
        f"路线图：\n\n{roadmap or '（空）'}\n\n"
        "## 学习者画像\n\n"
        f"目标：{profile_goal or '（空）'}\n\n"
        f"当前水平：{profile_level or '（空）'}\n\n"
        f"薄弱点：{profile_weak or '（空）'}\n\n"
        "## 最近新增的笔记\n\n"
        f"{notes or '（还没有笔记）'}\n\n"
        "## 最近的对话\n\n"
        f"{recent}\n"
    )


async def run_review(
    settings: Settings,
    service,
    *,
    days: int = DEFAULT_DAYS,
    state_path: Path | None = None,
    today: date | None = None,
) -> ReviewResult:
    """跑一次复盘，并把结果追加到计划的"复盘记录"里。"""
    # 先做便宜的检查，再去装 agent 栈：没有素材时连导入都不必要
    recent = recent_messages(days)
    if not recent:
        raise ReviewError(f"最近 {days} 天没有对话记录，没什么可复盘的")

    # 放函数里：这样模块本身不依赖 agent 栈，纯函数可以离线测
    from agents import Agent, Runner

    store = service.context.plan
    notes = "\n".join(
        f"- {info.date or '无日期'}　{info.title}"
        for info in service.list_notes()[:NOTES_IN_MATERIAL]
    )

    material = compose_material(
        days=days,
        today=format_today(date.today()),
        plan_now=store.read_section("现在的位置"),
        plan_next=store.read_section("下一步"),
        roadmap=store.read_section("路线图"),
        profile_goal=service.context.profile.read_section("目标"),
        profile_level=service.context.profile.read_section("当前水平"),
        profile_weak=service.context.profile.read_section("薄弱点"),
        notes=notes,
        recent=render_recent(recent),
    )

    agent = Agent(
        name="Divana 复盘者",
        instructions=load_reviewer(),
        model=settings.model,
    )
    result = await Runner.run(agent, material)
    markdown = (result.final_output or "").strip()
    if not markdown:
        raise ReviewError("复盘者没有返回内容")

    store.append_to_section("复盘记录", markdown)
    # 成功的复盘在这里记账：定时任务靠它判断"该不该跑"，
    # 手动跑和自动跑共用这一处，就不会出现"刚手动复盘完，定时又跑一遍"。
    mark_review_done(today=today, state_path=state_path)
    return ReviewResult(markdown=markdown, days=days, message_count=len(recent))


def mark_review_done(*, today: date | None = None, state_path: Path | None = None) -> None:
    """记下"这次复盘成功了"。定时任务靠这个日期判断下次什么时候该跑。

    单独抽出来是为了能测：run_review 要调模型、测不了，
    而"记账"这件事本身很值得盯住（记错一次就是一周不再跑）。
    """
    moment = today or date.today()
    update_state(state_path, **{LAST_REVIEW: moment.isoformat(), LAST_ERROR: ""})


def main() -> None:
    """手动跑一次复盘：`python -m divana.review`。

    定时任务以后调用的也是 run_review，所以这条命令和自动跑走的是同一条路。
    出问题时可以先手动跑一遍，把"逻辑问题"和"调度问题"分开。
    """
    import argparse
    import asyncio

    from dotenv import load_dotenv

    from .config import setup_agents_sdk
    from .scheduler import is_due
    from .service import DivanaService

    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="python -m divana.review", description="跑一次复盘"
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS, help=f"回看几天，默认 {DEFAULT_DAYS}"
    )
    parser.add_argument(
        "--if-due",
        action="store_true",
        help="只在超过一周没复盘时才跑（给系统定时任务用，重复执行是安全的）",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    setup_agents_sdk(settings)

    if args.if_due and not is_due(interval_days=args.days):
        print(f"上次复盘还不到 {args.days} 天，跳过")
        return

    service = DivanaService(settings)
    try:
        try:
            result = asyncio.run(run_review(settings, service, days=args.days))
        except ReviewError as exc:
            print(f"跳过：{exc}")
            return
        print(result.markdown)
    finally:
        service.close()

    target: Path = service.context.plan.path
    print()
    print(f"已写入 {target}")


if __name__ == "__main__":
    main()
