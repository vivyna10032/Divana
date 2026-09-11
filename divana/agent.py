"""Divana 的 agent 定义。"""

from pathlib import Path

from agents import Agent

from .config import Settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = PROJECT_ROOT / "prompts" / "divana.md"


def load_instructions() -> str:
    """读取人格定义文件。改 prompts/divana.md 就能调她的风格。"""
    return PROMPT_PATH.read_text(encoding="utf-8")


def build_agent(settings: Settings) -> Agent:
    """按当前配置创建一个 Divana 实例。"""
    return Agent(
        name="Divana",
        instructions=load_instructions(),
        model=settings.model,
    )