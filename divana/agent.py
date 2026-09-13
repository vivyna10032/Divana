"""Divana 的 agent 定义。"""

from pathlib import Path

from agents import Agent

from .config import Settings
from .context import DivanaContext
from .tools import build_tools

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = PROJECT_ROOT / "prompts" / "divana.md"


def load_instructions() -> str:
    """读取人格定义文件。改 prompts/divana.md 就能调她的风格。"""
    return PROMPT_PATH.read_text(encoding="utf-8")


def compose_instructions(persona: str, profile_text: str) -> str:
    """把人格和当前的学习者画像拼成完整的 instructions。

    画像是"每轮都要在眼前"的东西，所以走 instructions。
    以后那些长的资料（笔记、搜索结果）不能往这里塞——它们应该做成工具
    让模型按需去查。这是上下文工程里最基本的一条取舍。
    """
    return (
        f"{persona.rstrip()}\n\n"
        "---\n\n"
        "# 当前的学习者画像\n\n"
        "以下是你对这位学习者的长期记忆，每次对话开始时都会重新读一遍。"
        "如果画像和这次对话里说的对不上，以他这次说的为准，并顺手更新画像。\n\n"
        f"{profile_text.strip()}\n"
    )


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
            load_instructions(), context.profile.read()
        ),
        model=settings.model,
        tools=build_tools(),
    )
