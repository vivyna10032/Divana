"""回收站：删掉的东西先挪到这儿，不真的抹掉。

为什么非要这一层：删除是这个项目里**唯一不可逆**的操作。其它地方我们一直在防
"错误静默发生"，而"点错一下东西就没了"是最严重的那种。所以规矩是：

- 删文件（笔记）→ **移动**，不是 unlink
- 删数据库里的东西（一个会话）→ 先把内容导出成文件，再删行
- 删文件里的段落（一次复盘、一个阶段）→ 先把那段原文存成文件，再从文件里移除

回收站**不自动清理**：你自己去看一眼，确认不要了再删。攒不下多少空间，
但省下的可能是你半个月的学习记录。
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from .storage import atomic_write, slugify

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRASH_DIR = PROJECT_ROOT / "data" / "trash"


def _resolve_dir(trash_dir: Path | None) -> Path:
    target = Path(trash_dir) if trash_dir is not None else TRASH_DIR
    target.mkdir(parents=True, exist_ok=True)
    return target


def _stamp(now: datetime | None) -> str:
    return (now or datetime.now()).strftime("%Y%m%d-%H%M%S")


def _unique(path: Path) -> Path:
    """同一秒删两个同名东西时别互相覆盖。"""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(2, 100):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"回收站里同名文件太多了：{path.name}")


def trash_file(
    source: Path, kind: str, *, now: datetime | None = None, trash_dir: Path | None = None
) -> Path:
    """把一个文件移进回收站（原名保留，前面加时间戳）。用于笔记。"""
    source = Path(source)
    target = _resolve_dir(trash_dir) / f"{_stamp(now)}-{kind}-{source.name}"
    final = _unique(target)
    shutil.move(str(source), str(final))
    return final


def trash_text(
    kind: str,
    name: str,
    text: str,
    *,
    now: datetime | None = None,
    trash_dir: Path | None = None,
) -> Path:
    """把一段文字存进回收站。会话内容、复盘条目、阶段都走这个。

    开头写一行来源信息：以后在回收站里翻的时候，能知道这是从哪儿删的。
    """
    moment = now or datetime.now()
    safe_name = slugify(name) or kind
    target = _resolve_dir(trash_dir) / f"{_stamp(now)}-{kind}-{safe_name}.md"
    body = (
        f"# 回收站：{name}\n\n"
        f"- 类型：{kind}\n"
        f"- 删除时间：{moment.strftime('%Y-%m-%d %H:%M:%S')}\n"
        "- 说明：这只是备份，原来的位置已经删掉了。确认不要了可以直接删这个文件。\n\n"
        "---\n\n"
        f"{text.strip()}\n"
    )
    target = _unique(target)
    atomic_write(target, body)
    return target
