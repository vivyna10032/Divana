"""定时复盘：到点了就自动跑一次。

判定用的是**间隔**而不是"每周几"：读上次成功复盘的日期，超过 N 天就该跑。
好处是你不用记得上次是哪天跑的，服务也不会因为"今天不是周日"而白等一周。

运行方式是网页服务启动时挂一个后台任务，每隔一段时间看一眼该不该跑。
所以它**只在服务开着的时候**才触发——本地工具的现实就是这样，不假装自己是
7x24 的服务器。真需要"电脑关了也跑"，得挂系统级定时任务，那时候用
`python -m divana.review --if-due`（同一条判定逻辑，跑起来是幂等的）。

后台任务失败最容易静默，所以这里的规矩是：**失败也要留痕**——写进状态文件，
界面上能看到"上次自动复盘失败了"。
"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

from .config import Settings
from .review import ReviewResult, run_review
from .state import LAST_ERROR, LAST_REVIEW, last_review_date, read_state, update_state

# 超过多少天没复盘就该跑了
REVIEW_INTERVAL_DAYS = 7

# 后台多久检查一次。检查本身只是读一个 json，很便宜。
CHECK_INTERVAL_MINUTES = 30


def is_due(
    *,
    interval_days: int = REVIEW_INTERVAL_DAYS,
    today: date | None = None,
    state_path: Path | None = None,
) -> bool:
    """该不该复盘了。从没复盘过也算该跑（第一次打开就能看到一段回顾）。"""
    moment = today or date.today()
    last = last_review_date(state_path)
    if last is None:
        return True
    return (moment - last).days >= interval_days


def review_status(
    *,
    interval_days: int = REVIEW_INTERVAL_DAYS,
    today: date | None = None,
    state_path: Path | None = None,
) -> dict:
    """给界面看的一句话状态：上次什么时候、下次什么时候、上次失败了吗。"""
    moment = today or date.today()
    last = last_review_date(state_path)
    state = read_state(state_path)

    if last is None:
        return {
            "last": "",
            "days_since": -1,
            "due": True,
            "due_in": 0,
            "last_error": str(state.get(LAST_ERROR, "")),
        }

    passed = (moment - last).days
    return {
        "last": last.isoformat(),
        "days_since": passed,
        "due": passed >= interval_days,
        "due_in": max(0, interval_days - passed),
        "last_error": str(state.get(LAST_ERROR, "")),
    }


async def run_due_review(
    settings: Settings,
    service,
    *,
    interval_days: int = REVIEW_INTERVAL_DAYS,
    today: date | None = None,
    state_path: Path | None = None,
) -> ReviewResult | None:
    """到点了就跑一次复盘，没到点什么都不做。

    失败**不往外抛**（后台任务抛异常会死掉循环），而是记进状态文件，
    界面上能看到。返回 None 表示"没跑"或"跑失败了"。
    """
    if not is_due(interval_days=interval_days, today=today, state_path=state_path):
        return None

    try:
        result = await run_review(
            settings, service, days=interval_days, state_path=state_path, today=today
        )
    except Exception as exc:
        update_state(state_path, **{LAST_ERROR: f"{type(exc).__name__}: {exc}"})
        return None

    # 记账在 run_review 里做（手动跑、自动跑共用那一处）
    return result


async def scheduler_loop(
    service,
    *,
    interval_minutes: int = CHECK_INTERVAL_MINUTES,
    interval_days: int = REVIEW_INTERVAL_DAYS,
    state_path: Path | None = None,
) -> None:
    """后台循环：每隔一段时间看一眼该不该复盘。

    启动时先检查一次（补上服务没开的那几天），然后进循环。
    """
    while True:
        try:
            result = await run_due_review(
                service.settings, service, interval_days=interval_days, state_path=state_path
            )
            if result is not None:
                print(
                    f"[定时复盘] 跑了最近 {result.days} 天的复盘，"
                    f"用了 {result.message_count} 条对话记录"
                )
        except asyncio.CancelledError:
            raise  # 关服务时的正常取消，别吞掉
        except Exception as exc:  # 兜底：循环绝不能因为一次意外就死掉
            print(f"[定时复盘] 意外错误：{type(exc).__name__}: {exc}")

        await asyncio.sleep(interval_minutes * 60)
