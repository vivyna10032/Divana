"""从回收站把东西捞回来。

删除的对面。**没有它，回收站就是个死胡同**——东西还在，但你没有路走回去，
那"先移进回收站"就只是把损失延后了而已。

能恢复两种：

- 笔记：文件移回 `vault/notes/`
- 会话：把导出的对话重新写回会话库

（复盘条目和阶段的恢复是"插回 plan.md"，位置语义更复杂，暂时没做——
从回收站文件里手动复制粘贴即可。）

一个已知的信息损失：会话导出时的时刻只精确到分钟（`to_local_time` 丢掉了秒），
所以恢复回去的时间戳会和原来差几秒。对话内容不受影响。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .notes import DEFAULT_NOTES_DIR
from .session import MESSAGES_TABLE, SESSIONS_TABLE, SESSION_DB
from .trash import TRASH_DIR

_NAME = re.compile(r"^#\s*回收站：(?P<name>.*?)\s*$")
_KIND = re.compile(r"^-\s*类型：(?P<kind>\S+)\s*$")
_MESSAGE = re.compile(r"^\[(?P<at>[^\]]+)\]\s*(?P<who>你|Divana)：(?P<text>.*)$")
# 回收站文件名：20260920-194947-note-原文件名.md
_FILENAME = re.compile(r"^\d{8}-\d{6}-(?P<kind>[a-z]+)-(?P<rest>.+)$")


class RestoreError(RuntimeError):
    """可预期的失败：文件看不懂、类型不支持。"""


@dataclass(frozen=True)
class TrashItem:
    path: Path
    kind: str
    name: str
    body: str


def parse_trash(path: Path) -> TrashItem:
    """读一个回收站文件，把头部信息（类型、名字）和正文拆开。"""
    source = Path(path)
    if not source.exists():
        raise RestoreError(f"没有这个文件：{source}")

    text = source.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines:
        raise RestoreError(f"文件是空的：{source.name}")

    # 类型先从**文件名**读：笔记是"移动"进回收站的（原文件不带头部），
    # 只有会话、复盘那种才是"导出成带头部的新文件"。文件名里两种都有类型。
    kind = ""
    rest = ""
    if match := _FILENAME.match(source.name):
        kind = match.group("kind")
        rest = match.group("rest")
        if rest.endswith(source.suffix):
            rest = rest[: -len(source.suffix)]

    header_name = ""
    if match := _NAME.match(lines[0]):
        header_name = match.group("name").strip()
    if not kind:
        for line in lines[1:6]:
            if match := _KIND.match(line.strip()):
                kind = match.group("kind").strip()
                break

    body = text.split("\n---\n", 1)[1] if "\n---\n" in text else ""
    return TrashItem(
        path=source,
        kind=kind,
        name=header_name or rest or source.stem,
        body=body.strip(),
    )


def list_trash(trash_dir: Path | None = None) -> list[TrashItem]:
    """列出回收站里的东西，新的在前。"""
    target = Path(trash_dir) if trash_dir is not None else TRASH_DIR
    if not target.is_dir():
        return []

    items: list[TrashItem] = []
    for path in sorted(target.iterdir(), reverse=True):
        if path.is_file() and path.suffix == ".md":
            try:
                items.append(parse_trash(path))
            except RestoreError:
                continue
    return items


def _original_name(item: TrashItem) -> str:
    """从 `20260920-174947-note-原文件名.md` 里把原文件名抠出来。"""
    marker = f"-{item.kind}-"
    if marker in item.path.name:
        return item.path.name.split(marker, 1)[1]
    return f"{item.name}.md"


def restore_note(item: TrashItem, *, notes_dir: Path | None = None) -> Path:
    """把笔记移回笔记目录。同名已存在时加后缀，不覆盖。"""
    if item.kind != "note":
        raise RestoreError(f"这不是笔记：{item.path.name}（类型是 {item.kind}）")

    target_dir = Path(notes_dir) if notes_dir is not None else DEFAULT_NOTES_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / _original_name(item)
    for index in range(2, 100):
        if not target.exists():
            break
        target = target_dir / f"{Path(_original_name(item)).stem}-{index}.md"

    item.path.replace(target)
    return target


def _to_utc(stamp: str) -> str:
    """回收站里记的是本地时间，而库里存的是 UTC——写回去时要转一下。"""
    try:
        moment = datetime.strptime(stamp.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def restore_session(
    item: TrashItem,
    *,
    db_path: Path | None = None,
    session_id: str | None = None,
) -> tuple[str, int]:
    """把导出的对话写回会话库，返回 (会话名, 恢复了几条)。

    助手消息按普通字符串写回去（原来是部件列表）——回放给模型时两种都能吃，
    但形状确实变了，所以这里说清楚。
    """
    if item.kind != "session":
        raise RestoreError(f"这不是会话：{item.path.name}（类型是 {item.kind}）")

    parsed: list[tuple[str, str, str]] = []
    for line in item.body.splitlines():
        if match := _MESSAGE.match(line.strip()):
            role = "user" if match.group("who") == "你" else "assistant"
            parsed.append((match.group("at"), role, match.group("text").strip()))
    if not parsed:
        raise RestoreError("这个备份里没有对话内容，恢复不出东西")

    target_id = (session_id or item.name).strip() or "restored"
    path = Path(db_path) if db_path is not None else SESSION_DB
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            f"INSERT OR IGNORE INTO {SESSIONS_TABLE} (session_id) VALUES (?)",
            (target_id,),
        )
        for at, role, text in parsed:
            item_data = json.dumps({"role": role, "content": text}, ensure_ascii=False)
            conn.execute(
                f"INSERT INTO {MESSAGES_TABLE} (session_id, message_data, created_at) "
                "VALUES (?, ?, ?)",
                (target_id, item_data, _to_utc(at)),
            )
        conn.execute(
            f"UPDATE {SESSIONS_TABLE} SET updated_at = CURRENT_TIMESTAMP "
            "WHERE session_id = ?",
            (target_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # 恢复成功后，把回收站里那份挪进 restored/ 子目录：列表里不再出现（免得
    # 恢复两次变成两份），但也没删掉（万一恢复得不满意，原件还在）。
    # 笔记那边是"移动"，到这里语义就一致了。
    done_dir = item.path.parent / "restored"
    done_dir.mkdir(parents=True, exist_ok=True)
    item.path.replace(done_dir / item.path.name)
    return target_id, len(parsed)


def restore(item: TrashItem, *, notes_dir=None, db_path=None, session_id=None):
    """按类型分发。加新类型时只改这里。"""
    if item.kind == "note":
        return restore_note(item, notes_dir=notes_dir)
    if item.kind == "session":
        return restore_session(item, db_path=db_path, session_id=session_id)
    raise RestoreError(
        f"「{item.kind}」这种还没做恢复。文件在 {item.path}，"
        "里面的原文可以直接复制粘贴回去。"
    )


def main() -> None:
    """列出回收站、或者恢复一个文件：

        python -m divana.restore
        python -m divana.restore 20260920-201627-session-default.md
    """
    parser = argparse.ArgumentParser(
        prog="python -m divana.restore", description="看看回收站里有什么，或者恢复一个"
    )
    parser.add_argument("file", nargs="?", default="", help="要恢复的文件名（不填就只列出来）")
    args = parser.parse_args()

    items = list_trash()
    if not items:
        print(f"回收站是空的：{TRASH_DIR}")
        return

    if not args.file:
        print(f"回收站（{TRASH_DIR}）：\n")
        for item in items:
            size = item.path.stat().st_size
            print(f"  {item.path.name}")
            print(f"      类型 {item.kind}　名字 {item.name}　{size} 字节")
        print("\n恢复：python -m divana.restore 文件名")
        return

    wanted = args.file.strip().lower()
    hit = next(
        (item for item in items if wanted in {item.path.name.lower(), item.path.stem.lower()}),
        None,
    )
    if hit is None:
        raise SystemExit(f"回收站里没有「{args.file}」")

    result = restore(hit)
    if isinstance(result, Path):
        print(f"笔记已恢复：{result}")
    else:
        session, count = result
        print(f"会话已恢复：{session}（{count} 条对话）")


if __name__ == "__main__":
    main()
