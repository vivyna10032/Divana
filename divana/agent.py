"""Divana 的 agent 定义。"""

from pathlib import Path

from agents import Agent

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = PROJECT_ROOT / "prompts" / "divana.md"

# 日常对话用均衡档模型。想省钱可以换成更轻的，难题再换更强的。
MODEL = "gpt-5.6-terra"


def load_instructions() -> str:
    """读取人格定义文件。改 prompts/divana.md 就能调她的风格。"""
    return PROMPT_PATH.read_text(encoding="utf-8")


def build_agent() -> Agent:
    """创建一个 Divana 实例。"""
    return Agent(
        name="Divana",
        instructions=load_instructions(),
        model=MODEL,
    )
