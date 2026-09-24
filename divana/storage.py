"""落盘时的小工具。

两个人（画像、笔记）都要写文件，写法还必须是"安全"的那种，所以抽出来共用，
免得哪天改了一处忘了另一处。
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
import threading
import time
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

# 替换目标文件时最多重试几次。Windows 上 os.replace 会因为"目标正被占用"失败
# （WinError 5 / 32），而占用通常是短暂的——重试就行。
REPLACE_ATTEMPTS = 5
REPLACE_BACKOFF_SECONDS = 0.02

# 本进程内的写串行化。SDK 把同步工具丢进线程池（`asyncio.to_thread`），所以
# "两个工具同时改同一个文件"是真会发生的事；只有重试还不够稳（两个线程可以
# 一直互相顶），加一把写锁最省心。写很少，这点串行化没有代价。
_WRITE_LOCK = threading.Lock()


def _replace_with_retry(tmp: Path, path: Path) -> None:
    """把临时文件换成目标文件；被占用就退避重试。

    占用者可能是另一个线程（同时有两个工具在写），也可能是外部程序——
    Obsidian、编辑器、杀毒软件都会短暂持有文件。真过不去才报错，而且报错时
    目标文件仍然是**完整的旧版本**（临时文件另写一份，没动过目标）。
    """
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_BACKOFF_SECONDS * (attempt + 1))


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

    **临时文件名必须唯一。** SDK 会把同步工具丢进线程池执行
    （`agents/tool.py` 里是 `asyncio.to_thread`），所以同一次响应里的多个工具调用
    是**真并发**。固定用 `xxx.md.tmp` 的话两个写者会互相踩：一个刚改完 tmp，
    另一个 os.replace 时文件已经不在（Windows 上直接 WinError 32），
    目标文件还可能被写成半截——2026-09-24 实测 300 轮并发写丢掉若干轮，
    还读到过 UnicodeDecodeError。唯一名字 + 写锁 + 重试，三层都要有。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp"
    )
    tmp = Path(raw)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        with _WRITE_LOCK:
            _replace_with_retry(tmp, path)
    except BaseException:
        # 写失败了就把临时文件收掉，别在目录里留垃圾
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
