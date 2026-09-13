"""落盘时的小工具。

两个人（画像、笔记）都要写文件，写法还必须是"安全"的那种，所以抽出来共用，
免得哪天改了一处忘了另一处。
"""

from __future__ import annotations

import os
from pathlib import Path


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
