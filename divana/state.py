"""跨运行的一点点状态：目前只记"上次复盘是什么时候、有没有失败过"。

为什么不从 plan.md 里解析复盘日期？因为那是**猜**——模型可能没写日期、
可能换格式、可能写错。而"上次什么时候跑的"是程序自己做过的事，程序自己记最可靠。

放在 `data/` 下（已经在 .gitignore 里），和会话库作伴。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .storage import atomic_write

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
STATE_PATH = DATA_DIR / "state.json"

LAST_REVIEW = "last_review"
LAST_ERROR = "last_error"


def read_state(path: Path | None = None) -> dict:
    """读状态文件。没有、坏了、不是对象，一律当空状态——状态丢了不该让程序起不来。"""
    target = Path(path) if path is not None else STATE_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(values: dict, path: Path | None = None) -> None:
    target = Path(path) if path is not None else STATE_PATH
    atomic_write(target, json.dumps(values, ensure_ascii=False, indent=2) + "\n")


def update_state(path: Path | None = None, **changes: object) -> dict:
    """读、改、写一步做完。

    分开写很容易变成"两处各读一次、各写一次"，后写的那个把前面的改动盖掉。
    """
    state = read_state(path)
    state.update(changes)
    write_state(state, path)
    return state


def last_review_date(path: Path | None = None) -> date | None:
    """上次成功复盘的日期。没有或格式不对就返回 None。"""
    raw = read_state(path).get(LAST_REVIEW, "")
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None
