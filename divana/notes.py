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


@dataclass(frozen=True)
class NoteInfo:
    """从文件里读回来的笔记：元信息 + 正文。"""

    path: Path
    title: str
    date: str
    tags: tuple[str, ...]
    body: str


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


_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)


def _parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """拆出 front-matter 和正文。

    解析得"能认就认、认不出就跳过"——笔记是用户也会手改的文件，
    头部写坏了不该导致整篇笔记读不出来。
    """
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text.strip()

    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip().lower()] = value.strip()
    return fields, text[match.end() :].strip()


def _parse_tag_list(raw: str) -> tuple[str, ...]:
    """把 front-matter 里的 `tags: [a, b]` 拆成元组。"""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return tuple(tag.strip().strip("'\"") for tag in raw.split(",") if tag.strip())


def _first_heading(body: str) -> str:
    """没有 front-matter 时，拿第一个标题当名字。"""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _date_from_name(path: Path) -> str:
    """文件名开头的 2026-09-13 就是日期，front-matter 丢了也能用。"""
    match = re.match(r"(\d{4}-\d{2}-\d{2})", path.name)
    return match.group(1) if match else ""


def _match_score(info: NoteInfo, keyword: str) -> int | None:
    """命中得分：标题 3 分、标签 2 分、正文 1 分。没命中返回 None。"""
    if keyword in info.title.lower():
        return 3
    if any(keyword in tag.lower() for tag in info.tags):
        return 2
    if keyword in info.body.lower():
        return 1
    return None


def _snippet(info: NoteInfo, keyword: str, chars: int = 160) -> str:
    """给个能看的摘要：有关键词就给包含它的那一行，否则给正文第一行。"""
    lines = [
        line.strip()
        for line in info.body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        return "（这篇笔记还没有正文）"
    if keyword:
        for line in lines:
            if keyword in line.lower():
                return line[:chars]
    return lines[0][:chars]


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

    def list_all(self) -> list[NoteInfo]:
        """列出所有笔记。文件名以日期开头，所以倒序就是新的在前。"""
        if not self.notes_dir.is_dir():
            return []
        return [self._load(path) for path in sorted(self.notes_dir.glob("*.md"), reverse=True)]

    def search(
        self, query: str, limit: int = 5, *, tag: str = ""
    ) -> list[tuple[NoteInfo, str]]:
        """找笔记，返回 (笔记, 摘要)。

        query 传 "*" 或空字符串表示"列出最近的几篇"。
        tag 给了就只在这个标签里找——网页上的标签筛选走这条路，
        不能用关键词凑合（搜 "transformer" 会把正文里提过它的笔记也捞出来）。
        """
        query = query.strip()
        list_mode = query in {"", "*"}
        keyword = query.lower()

        hits: list[tuple[int, NoteInfo, str]] = []
        for info in self.list_all():
            if tag and tag not in info.tags:
                continue
            if list_mode:
                hits.append((0, info, _snippet(info, "")))
                continue
            score = _match_score(info, keyword)
            if score is not None:
                hits.append((score, info, _snippet(info, keyword)))

        # 稳定排序：同分的保持 list_all 给的顺序（新的在前）
        hits.sort(key=lambda item: item[0], reverse=True)
        return [(info, snippet) for _, info, snippet in hits[:limit]]

    def read(self, name: str) -> NoteInfo:
        """按文件名或标题读一篇笔记。

        这里有一条要紧的规矩：`name` 只用来"在已经枚举出来的文件里做匹配"，
        从来不参与路径拼接。所以传 "../../.env" 这种输入，天然不可能读到
        笔记目录外面去——它只是匹配不到任何一篇而已。
        """
        wanted = name.strip()
        if not wanted:
            raise NoteError("要读哪一篇？给我文件名或标题")

        notes = self.list_all()
        if not notes:
            raise NoteError("笔记目录里还没有任何笔记")

        lowered = wanted.lower()

        for info in notes:
            if info.path.name.lower() == lowered or info.path.stem.lower() == lowered:
                return info

        same_title = [info for info in notes if info.title.lower() == lowered]
        if len(same_title) == 1:
            return same_title[0]

        partial = [
            info
            for info in notes
            if lowered in info.title.lower() or lowered in info.path.name.lower()
        ]
        if len(partial) == 1:
            return partial[0]
        if not partial:
            raise NoteError(f"没找到「{name}」。先用 search_notes 看看有哪些笔记。")

        names = "、".join(info.path.name for info in partial[:5])
        raise NoteError(f"「{name}」匹配到多篇：{names}。用完整文件名再试一次。")

    def _load(self, path: Path) -> NoteInfo:
        """读一个文件并尽力还原它的元信息，任何字段缺失都有兜底。"""
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""

        fields, body = _parse_front_matter(text)
        return NoteInfo(
            path=path,
            title=fields.get("title", "").strip() or _first_heading(body) or path.stem,
            date=fields.get("date", "").strip() or _date_from_name(path),
            tags=_parse_tag_list(fields.get("tags", "")),
            body=body,
        )
