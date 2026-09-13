"""会话持久化：把每轮对话写进 SQLite，关掉窗口再打开还能接着聊。

只做一件事——造一个 SQLiteSession 并告诉它数据放哪。
剩下的事（读历史、拼进这次请求、把新的一轮写回去）由 Runner 自动完成。
"""

from __future__ import annotations

from pathlib import Path

from agents import SessionSettings, SQLiteSession

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


def open_session(session_id: str = DEFAULT_SESSION_ID) -> SQLiteSession:
    """打开（或新建）一个会话。数据库文件不存在会自动创建。

    session_id 相同就是同一段记忆；换个名字相当于开一段新的对话。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return SQLiteSession(
        session_id,
        db_path=SESSION_DB,
        session_settings=SessionSettings(limit=HISTORY_LIMIT),
    )
