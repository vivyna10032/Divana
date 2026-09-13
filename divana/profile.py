"""学习者画像：Divana 对"你是谁、学到哪了"的长期记忆。

画像是一份给人看的 markdown，放在 vault/profile.md。它每轮对话都会被拼进
模型的 instructions，所以必须保持短小——这也是为什么写入要限制长度。

这个模块只做文件读写，不碰模型，所以可以离线测试（见 tests/test_profile.py）。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from .storage import atomic_write

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE_PATH = PROJECT_ROOT / "vault" / "profile.md"

# 允许写入的章节。写死是有意的：模型只能改这几节，
# 你在文件里自己加的其它章节它碰不到。
SECTIONS = ("目标", "当前水平", "已掌握", "薄弱点", "学习习惯")

# 单节长度上限（字符）。画像每轮都要带进上下文，不能让它无限膨胀。
MAX_SECTION_CHARS = 1200

TEMPLATE = """# 学习者画像

> 这是 Divana 对你的长期记忆，由她自己维护。你可以随时手动改，
> 她下次启动会读到最新版本。
>
> 在终端里输入 /profile 可以随时查看当前内容。

## 目标

（还没记录。跟她说说你想学什么、为什么学。）

## 当前水平

（还没记录。）

## 已掌握

（还没记录。）

## 薄弱点

（还没记录。）

## 学习习惯

（还没记录。）
"""

_HEADING = re.compile(r"^##\s+(?P<name>\S.*?)\s*$")


class ProfileError(ValueError):
    """画像写入时的可预期错误。工具会把它翻译成给模型看的话，而不是抛出去。"""


def iter_sections(lines: list[str]) -> Iterator[tuple[str, int, int]]:
    """把行列表按二级标题切开，产出 (标题, 起始行号, 结束行号)。

    结束行号不含在正文里——也就是下一个二级标题所在的行。
    """
    headings = [
        (index, match.group("name"))
        for index, line in enumerate(lines)
        if (match := _HEADING.match(line))
    ]
    for position, (start, name) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        yield name, start, end


class ProfileStore:
    """读写 vault/profile.md。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_PROFILE_PATH

    def ensure_exists(self) -> None:
        """文件不在就按模板建一个，父目录也一起建。"""
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(TEMPLATE, encoding="utf-8")

    def read(self) -> str:
        """读整份画像。文件还没建就返回模板，不落盘。"""
        try:
            return self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return TEMPLATE

    def write_section(self, section: str, content: str) -> None:
        """把某一节的正文整体换掉，其余部分原样保留。

        这里用"按行切片再拼回去"，而不是"解析成数据结构再重新生成文件"：
        你在文件里手写的备注、自己加的章节都不会被程序弄丢。
        """
        if section not in SECTIONS:
            raise ProfileError(
                f"「{section}」不是可写的章节，只能是：{'、'.join(SECTIONS)}"
            )

        content = content.strip()
        if not content:
            raise ProfileError("内容不能为空")
        if len(content) > MAX_SECTION_CHARS:
            raise ProfileError(
                f"「{section}」太长了（{len(content)} 字符，上限 {MAX_SECTION_CHARS}）。"
                "画像是每轮都要读的，请提炼成几句结论。"
            )

        self.ensure_exists()
        lines = self.read().splitlines()
        for name, start, end in iter_sections(lines):
            if name == section:
                # 标题下面统一留一个空行 + 正文 + 一个空行，保证和下一个标题隔开
                lines[start + 1 : end] = ["", *content.splitlines(), ""]
                self._write("\n".join(lines) + "\n")
                return

        raise ProfileError(f"画像里没有「{section}」这一节")

    def _write(self, text: str) -> None:
        """先写临时文件再原子替换：中途失败也不会把画像写坏。"""
        atomic_write(self.path, text)
