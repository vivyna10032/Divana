"""Divana 的 agent 定义。"""

from agents import Agent

from .config import Settings
from .context import DivanaContext
from .prompt import compose_instructions, load_persona
from .tools import build_tools


def build_agent(settings: Settings, context: DivanaContext) -> Agent[DivanaContext]:
    """按当前配置创建一个 Divana 实例。

    两件事在这里汇合：人格 + 画像拼成 instructions，工具列表挂到 agent 上。
    类型参数写成 Agent[DivanaContext]，是为了和工具声明的
    RunContextWrapper[DivanaContext] 对上——上下文类型必须一致，
    SDK 才认得出该把哪个对象递给工具。
    """
    return Agent(
        name="Divana",
        instructions=compose_instructions(
            load_persona(), context.profile.read()
        ),
        model=settings.model,
        tools=build_tools(),
    )
