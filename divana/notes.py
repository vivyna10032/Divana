"""学习笔记落盘：把值得留下的东西写成 markdown，放进 vault/notes/。

这里有一条贯穿全项目的规矩：**路径由程序决定，不由模型决定**。
模型只提供标题、正文、标签；文件名和目录由下面的 slugify/唯一化逻辑生成。
把"能写到哪"交给模型，等于把路径穿越、覆盖别人文件这些坑一起请进门。

只做文件读写，不碰模型，所以可以离线测试（见 tests/test_notes.py）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .storage import atomic_write

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_NOTES_DIR = PROJECT_ROOT / "vault" / "notes"

MAX_TITLE_CHARS = 80
MAX_BODY_CHARS = 8000
MAX_SLUG_CHARS = 40
MAX_TAGS = 6

# Windows 文件名里不能出现的字符，加上控制字符
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Windows 保留设备名。叫 CON.md 的文件在某些路径下会直接建不出来
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class NoteError(ValueError):
    """笔记写入时的可预期错误。工具会把它翻译成给模型看的话。"""


@dataclass(frozen=True)
class Note:
    """一篇刚写好的笔记，留个记录方便工具回话。"""

    path: Path
    title: str
    tags: tuple[str, ...]


def slugify(title: str) -> str:
    """把标题变成能当文件名用的片段。

    中文原样保留——NTFS 和 Obsidian 都没问题，硬转成拼音或哈希反而没法看。
    只处理真正会出问题的东西：非法字符、连续空白、首尾的点号。
    """
    cleaned = _FORBIDDEN.sub(" ", title)
    cleaned = re.sub(r"\s+", "-", cleaned.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    cleaned = cleaned[:MAX_SLUG_CHARS].strip("-. ")

    if not cleaned or cleaned.upper() in _RESERVED:
        return "note"
    return cleaned


def normalize_tags(tags: list[str] | None) -> tuple[str, ...]:
    """去空白、去重、限量，保持用户给的顺序。"""
    seen: list[str] = []
    for raw in tags or []:
        tag = raw.strip().lstrip("#")
        if tag and tag not in seen:
            seen.append(tag)
    return tuple(seen[:MAX_TAGS])


def render_note(title: str, body: str, tags: tuple[str, ...], day: date) -> str:
    """拼成带 front-matter 的 markdown，Obsidian 能直接认出标题和标签。"""
    tag_line = "[" + ", ".join(tags) + "]" if tags else "[]"
    return (
        "---\n"
        f"title: {title}\n"
        f"date: {day.isoformat()}\n"
        f"tags: {tag_line}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{body}\n"
    )


class NoteStore:
    """往 vault/notes/ 里写笔记。"""

    def __init__(self, notes_dir: Path | None = None) -> None:
        self.notes_dir = Path(notes_dir) if notes_dir is not None else DEFAULT_NOTES_DIR

    def save(
        self,
        title: str,
        body: str,
        tags: list[str] | None = None,
        *,
        today: date | None = None,
    ) -> Note:
        """存一篇笔记，返回写好的记录（含路径）。

        文件名是 日期-slug.md；重名不会覆盖，会往后加 -2、-3。
        """
        title = title.strip()
        body = body.strip()
        if not title:
            raise NoteError("标题不能为空")
        if len(title) > MAX_TITLE_CHARS:
            raise NoteError(f"标题太长了（{len(title)} 字符，上限 {MAX_TITLE_CHARS}）")
        if not body:
            raise NoteError("正文不能为空")
        if len(body) > MAX_BODY_CHARS:
            raise NoteError(
                f"正文太长了（{len(body)} 字符，上限 {MAX_BODY_CHARS}）。"
                "一篇笔记只讲一个概念，拆成几篇更好用。"
            )

        clean_tags = normalize_tags(tags)
        day = today or date.today()
        path = self._unique_path(day, slugify(title))
        atomic_write(path, render_note(title, body, clean_tags, day))
        return Note(path=path, title=title, tags=clean_tags)

    def _unique_path(self, day: date, slug: str) -> Path:
        """找一个还没被占用的文件名。绝不覆盖已有笔记。"""
        base = f"{day.isoformat()}-{slug}"
        for suffix in range(1, 100):
            name = f"{base}.md" if suffix == 1 else f"{base}-{suffix}.md"
            path = self.notes_dir / name
            if not path.exists():
                return path
        raise NoteError(f"同名笔记太多了：{base}")
