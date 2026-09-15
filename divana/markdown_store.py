"""带固定章节的 markdown 文件——画像和计划都是这个形状。

共同点：模型只能改**给定章节的正文**；标题、说明文字、以及你在文件里自己加的
章节，全部由程序原样保留。写入走原子替换，长度有上限（因为它们都是要进上下文
或者被模型读的东西，不能无限膨胀）。

这个抽象是"长出来"的，不是一开始设计出来的：先有画像，写计划时发现逻辑几乎
一模一样，才抽出来。**两个调用方是抽取的信号**——一个的时候抽是猜，两个的
时候抽才是归纳。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from .storage import atomic_write

_HEADING = re.compile(r"^##\s+(?P<name>\S.*?)\s*$")


class MarkdownStoreError(ValueError):
    """可预期的读写错误：章节名不对、内容为空或太长。"""


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


class SectionedMarkdown:
    """子类只要给 path / sections / template / max_section_chars 就能用。"""

    error_cls: type[MarkdownStoreError] = MarkdownStoreError

    def __init__(
        self,
        path: Path,
        *,
        sections: tuple[str, ...],
        template: str,
        max_section_chars: int,
    ) -> None:
        self.path = Path(path)
        self.sections = tuple(sections)
        self.template = template
        self.max_section_chars = max_section_chars

    def ensure_exists(self) -> None:
        """文件不在就按模板建一个，父目录也一起建。"""
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.template, encoding="utf-8")

    def read(self) -> str:
        """读全文。文件还没建就返回模板，不落盘。"""
        try:
            return self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self.template

    def read_section(self, section: str) -> str:
        """读某一节的正文（不含标题行和首尾空行）。

        页面侧边栏要单独显示"现在的位置""目标"这种小节，就得按节取。
        章节名写错会报错（那是我自己代码的问题，该早发现）；但**文件里被手删掉
        了那一节就返回空串**——读操作不该因为文件被改过就炸。
        """
        if section not in self.sections:
            raise self.error_cls(
                f"「{section}」不是这个文件的章节，只能是：{'、'.join(self.sections)}"
            )
        lines = self.read().splitlines()
        for name, start, end in iter_sections(lines):
            if name == section:
                return "\n".join(lines[start + 1 : end]).strip()
        return ""

    def write_section(self, section: str, content: str) -> None:
        """把某一节的正文整体换掉，其余部分原样保留。

        用"按行切片再拼回去"，而不是"解析成数据结构再重新生成文件"：
        你在文件里手写的备注、自己加的章节都不会被程序弄丢。
        """
        if section not in self.sections:
            raise self.error_cls(
                f"「{section}」不是可写的章节，只能是：{'、'.join(self.sections)}"
            )

        content = content.strip()
        if not content:
            raise self.error_cls("内容不能为空")
        if len(content) > self.max_section_chars:
            raise self.error_cls(
                f"「{section}」太长了（{len(content)} 字符，上限 {self.max_section_chars}）。"
                "这一节是要经常读的，请提炼成几句结论。"
            )

        self.ensure_exists()
        lines = self.read().splitlines()
        for name, start, end in iter_sections(lines):
            if name == section:
                # 标题下面统一留一个空行 + 正文 + 一个空行，和下一个标题隔开
                lines[start + 1 : end] = ["", *content.splitlines(), ""]
                atomic_write(self.path, "\n".join(lines) + "\n")
                return

        raise self.error_cls(f"文件里没有「{section}」这一节")
