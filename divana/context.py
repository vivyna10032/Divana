"""把工具要用到的外部资源打包成一个上下文对象。

`Runner.run(..., context=ctx)` 传进去，工具用 `RunContextWrapper[DivanaContext]`
取出来，各自挑自己要的那部分。这个对象**不会发给模型**，只在本地代码之间流动。

工具从一个长到三个之后，就得有个地方统一管理它们的依赖——否则每个工具都去
import 全局单例，测试时想换一个临时目录都换不了。
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .notes import NoteStore
from .plan import PlanStore
from .profile import ProfileStore
from .search import SearchClient


@dataclass
class DivanaContext:
    profile: ProfileStore
    notes: NoteStore
    plan: PlanStore
    search: SearchClient
    github_token: str = ""


def build_context(settings: Settings) -> DivanaContext:
    """按当前配置装配好所有资源。"""
    context = DivanaContext(
        profile=ProfileStore(),
        notes=NoteStore(),
        plan=PlanStore(),
        search=SearchClient(
            provider=settings.search_provider,
            api_key=settings.search_api_key,
            max_results=settings.search_max_results,
        ),
        github_token=settings.github_token,
    )
    context.profile.ensure_exists()
    context.plan.ensure_exists()
    return context
