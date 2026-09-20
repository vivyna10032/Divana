"""会话持久化：把每轮对话写进 SQLite，关掉窗口再打开还能接着聊。

两件事：

1. 造一个 SQLiteSession（剩下读历史、拼请求、写回都由 Runner 自动完成）
2. **列出所有会话**——SDK 的 Session 只按 id 读写，没有"列出全部"这个接口，
   所以这一段是直接查 SQLite 的。表是 SDK 建的，名字用它的默认值。

agents 的导入放在函数里：这样这个模块不装 agent 栈也能导入，
列会话和建新 id 这两段纯逻辑就能离线测。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agents import SQLiteSession

from .transcript import message_text
from .trash import trash_text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SESSION_DB = DATA_DIR / "divana.db"

DEFAULT_SESSION_ID = "default"

# 每次最多把最近多少条历史送进模型（一问一答大约 2 条）。
#
# 注意这只是"读出来多少"：数据库里始终是全量记录，什么都没丢，
# 换个大一点的数字、或者以后写个回溯命令，历史还在那儿。
# 不设上限的话，聊得越久每轮请求越大，迟早又贵又慢（甚至超上下文）。
HISTORY_LIMIT = 40

# 会话标题最多取多少字
TITLE_CHARS = 24

# SDK 建的默认表名（open_session 用默认值开会话，所以这里也按默认名查）
SESSIONS_TABLE = "agent_sessions"
MESSAGES_TABLE = "agent_messages"


@dataclass(frozen=True)
class SessionInfo:
    """一个会话的元信息，给界面列表用。"""

    session_id: str
    created_at: str
    updated_at: str
    message_count: int
    title: str


@dataclass(frozen=True)
class RecentMessage:
    """最近聊过的一句话，复盘要用。"""

    session_id: str
    at: str  # 本地时间
    role: str  # user / assistant
    text: str


def to_local_time(stamp: str) -> str:
    """把数据库里的时间戳转成本地时间。

    SQLite 的 CURRENT_TIMESTAMP 存的是 **UTC**。直接显示的话，
    东八区的人会看到"早了 8 小时"的时间——晚上八点聊的天显示成中午十二点。
    """
    try:
        moment = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return stamp or ""
    return moment.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def title_from_item(item: object) -> str:
    """从一条记录里取个标题：只有用户说的话才配当标题。"""
    if not isinstance(item, dict) or item.get("role") != "user":
        return ""

    text = " ".join(message_text(item.get("content")).split())
    if len(text) > TITLE_CHARS:
        text = text[:TITLE_CHARS] + "…"
    return text


def recent_messages(
    days: int = 7,
    *,
    db_path: Path | None = None,
    now: datetime | None = None,
    limit: int = 400,
) -> list[RecentMessage]:
    """最近几天聊过的话（跨所有会话），按时间正序。

    复盘要用它。两个细节：

    - 数据库里的时间戳是 UTC，所以比较的基准也要用 UTC（`datetime.now(timezone.utc)`），
      否则"最近 7 天"会整体偏 8 小时。
    - 条数上限是从**最新**往前取的（ORDER BY DESC 再反转），
      一周聊了几百条时，丢掉的是最老的那些。
    """
    path = Path(db_path) if db_path is not None else SESSION_DB
    if not path.exists():
        return []

    moment = now or datetime.now(timezone.utc)
    cutoff = (moment - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            f"""
            SELECT session_id, created_at, message_data FROM {MESSAGES_TABLE}
            WHERE created_at >= ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (cutoff, limit),
        ).fetchall()
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()

    return _messages_from_rows(reversed(rows))  # 反转回时间正序


def _messages_from_rows(rows) -> list[RecentMessage]:
    """把 (session_id, created_at, message_data) 这些行变成消息列表。

    工具调用、模型思考过程都跳过——那些是给模型看的，不是"对话"。
    """
    messages: list[RecentMessage] = []
    for session_id, created_at, data in rows:
        try:
            item = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            continue
        text = message_text(item.get("content"))
        if text:
            messages.append(
                RecentMessage(
                    session_id=session_id,
                    at=to_local_time(created_at),
                    role=item["role"],
                    text=text,
                )
            )
    return messages


def session_messages(session_id: str, *, db_path: Path | None = None) -> list[RecentMessage]:
    """某个会话里的全部对话（只要人话）。删除前导出用。"""
    path = Path(db_path) if db_path is not None else SESSION_DB
    if not path.exists():
        return []

    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            f"""
            SELECT session_id, created_at, message_data FROM {MESSAGES_TABLE}
            WHERE session_id = ? ORDER BY id
            """,
            (session_id,),
        ).fetchall()
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()
    return _messages_from_rows(rows)


def delete_session(
    session_id: str, *, db_path: Path | None = None, trash_dir: Path | None = None
) -> Path:
    """删掉一个会话，但**先把内容导出到回收站**，返回备份文件的位置。

    顺序不能反：先导出、再删行。反过来的话，导出一旦失败，记录已经没了。
    """
    messages = session_messages(session_id, db_path=db_path)
    text = "\n".join(
        f"[{message.at}] {'你' if message.role == 'user' else 'Divana'}：{message.text}"
        for message in messages
    )
    saved = trash_text(
        "session", session_id, text or "（这个会话里没有对话内容）", trash_dir=trash_dir
    )

    path = Path(db_path) if db_path is not None else SESSION_DB
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            f"DELETE FROM {MESSAGES_TABLE} WHERE session_id = ?", (session_id,)
        )
        conn.execute(
            f"DELETE FROM {SESSIONS_TABLE} WHERE session_id = ?", (session_id,)
        )
        conn.commit()
    finally:
        conn.close()
    return saved


def list_sessions(db_path: Path | None = None) -> list[SessionInfo]:
    """列出所有会话，最近用过的在前。

    数据库不存在或表还没建时返回空列表，不抛异常——界面第一次打开时就是这样。
    """
    path = Path(db_path) if db_path is not None else SESSION_DB
    if not path.exists():
        return []

    conn = sqlite3.connect(str(path))
    try:
        rows = conn.execute(
            f"""
            SELECT s.session_id, s.created_at, s.updated_at,
                   (SELECT COUNT(*) FROM {MESSAGES_TABLE} m WHERE m.session_id = s.session_id)
            FROM {SESSIONS_TABLE} s
            ORDER BY s.updated_at DESC
            """
        ).fetchall()

        sessions: list[SessionInfo] = []
        for session_id, created_at, updated_at, count in rows:
            first = conn.execute(
                f"""
                SELECT message_data FROM {MESSAGES_TABLE}
                WHERE session_id = ? ORDER BY id LIMIT 1
                """,
                (session_id,),
            ).fetchone()

            title = ""
            if first:
                try:
                    title = title_from_item(json.loads(first[0]))
                except (json.JSONDecodeError, TypeError):
                    title = ""

            sessions.append(
                SessionInfo(
                    session_id=session_id,
                    created_at=to_local_time(created_at),
                    updated_at=to_local_time(updated_at),
                    message_count=count,
                    title=title or session_id,
                )
            )
        return sessions
    except sqlite3.DatabaseError:
        # 表还没建、或者文件不是数据库：当成"还没有会话"
        return []
    finally:
        conn.close()


def new_session_id(now: datetime | None = None) -> str:
    """给新会话起个名字。用时间戳，顺手也就有了排序依据。"""
    moment = now or datetime.now()
    return f"chat-{moment.strftime('%Y%m%d-%H%M%S')}"


def open_session(session_id: str = DEFAULT_SESSION_ID) -> "SQLiteSession":
    """打开（或新建）一个会话。数据库文件不存在会自动创建。

    session_id 相同就是同一段记忆；换个名字相当于开一段新的对话。
    """
    from agents import SessionSettings, SQLiteSession  # 只有真要开会话时才需要

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return SQLiteSession(
        session_id,
        db_path=SESSION_DB,
        session_settings=SessionSettings(limit=HISTORY_LIMIT),
    )
