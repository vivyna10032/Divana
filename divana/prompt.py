"""拼 instructions：人格 + 今天的日期 + 学习者画像。

放在这里而不是 agent.py，是为了能离线测试——这个模块不 import agents，
所以"日期怎么算、怎么拼"这部分纯逻辑可以单独跑测试。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PERSONA_PATH = PROJECT_ROOT / "prompts" / "divana.md"
SUMMARIZER_PATH = PROJECT_ROOT / "prompts" / "summarizer.md"
REVIEWER_PATH = PROJECT_ROOT / "prompts" / "reviewer.md"

_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def load_persona() -> str:
    """读取人格定义文件。改 prompts/divana.md 就能调她的风格。"""
    return PERSONA_PATH.read_text(encoding="utf-8")


def load_summarizer() -> str:
    """读取总结者的指令。改 prompts/summarizer.md 就能调总结的详略和结构。"""
    return SUMMARIZER_PATH.read_text(encoding="utf-8")


def load_reviewer() -> str:
    """读取复盘者的指令。改 prompts/reviewer.md 就能调复盘的角度。"""
    return REVIEWER_PATH.read_text(encoding="utf-8")


def format_today(today: date) -> str:
    """给人看的中文日期，带星期——说"下周三"的时候模型用得上。"""
    return f"{today.isoformat()}（{_WEEKDAYS[today.weekday()]}）"


def compose_instructions(
    persona: str, profile_text: str, today: date | None = None
) -> str:
    """把人格、日期、画像拼成完整的 instructions。"""
    today = today or date.today()
    return (
        f"{persona.rstrip()}\n\n"
        "---\n\n"
        "# 现在的时间\n\n"
        f"今天是 {format_today(today)}。这是你启动时的日期。\n"
        "你**看不到时钟**。凡是你要写日期（笔记、记录、对比时间），都以这里为准，"
        "不要凭印象填一个看起来合理的值。用户说“今天”“上周”这类相对时间，"
        "也以这个日期为基准换算。\n\n"
        "---\n\n"
        "# 当前的学习者画像\n\n"
        "以下是你对这位学习者的长期记忆，每次对话开始时都会重新读一遍。"
        "如果画像和这次对话里说的对不上，以他这次说的为准，并顺手更新画像。\n\n"
        f"{profile_text.strip()}\n"
    )
