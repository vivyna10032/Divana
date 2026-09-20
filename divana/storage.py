"""落盘时的小工具。

两个人（画像、笔记）都要写文件，写法还必须是"安全"的那种，所以抽出来共用，
免得哪天改了一处忘了另一处。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# 文件名里不能出现的字符（Windows），加上控制字符
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# Windows 保留设备名。叫 CON.md 的文件在某些路径下会直接建不出来
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

MAX_SLUG_CHARS = 40


def slugify(title: str) -> str:
    """把标题变成能当文件名用的片段。

    中文原样保留——NTFS 和 Obsidian 都没问题，硬转成拼音或哈希反而没法看。
    只处理真正会出问题的东西：非法字符、连续空白、首尾的点号。

    放在这里而不是 notes.py：笔记和回收站都要用它，而 notes 那边会因为
    import 回收站而形成循环依赖。
    """
    cleaned = _FORBIDDEN.sub(" ", title)
    cleaned = re.sub(r"\s+", "-", cleaned.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    cleaned = cleaned[:MAX_SLUG_CHARS].strip("-. ")

    if not cleaned or cleaned.upper() in _RESERVED:
        return "note"
    return cleaned


def atomic_write(path: Path, text: str) -> None:
    """先写临时文件、再原子替换。

    直接 write_text 覆盖原文件的话，写到一半崩了（断电、被 kill、磁盘满），
    留下的就是半截内容。先写 .tmp 再 os.replace 的话，目标文件要么是旧的
    完整版、要么是新的完整版，不会有中间状态。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
