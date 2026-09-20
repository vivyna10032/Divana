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
from datetime import datetime
from pathlib import Path

from .storage import atomic_write

_HEADING = re.compile(r"^##\s+(?P<name>\S.*?)\s*$")
# 正好两个 # 的标题（### 不算）
_LEVEL2_HEADING = re.compile(r"^##(?!#)\s*", re.MULTILINE)
# 模板里的占位文字，形如"（还没记录。）"
_PLACEHOLDER = re.compile(r"^（[^（）]*）$")


class MarkdownStoreError(ValueError):
    """可预期的读写错误：章节名不对、内容为空或太长。"""


def demote_level2_headings(text: str) -> str:
    """把内容里"正好两级"的标题降成三级。

    这个文件的结构是靠 `## ` 标题切分的。如果写进去的内容自带 `## 某标题`，
    它就会被当成一个新章节——后果不是报错，而是**静默的结构损坏**：

    - 追加复盘时，下一次会插到它前面，日记顺序反过来
    - 写画像时，模型随手写的 `## 补充` 会把这一节切断

    所以写入前一律降级。`###` 已经是三级的不动。
    """
    return _LEVEL2_HEADING.sub("### ", text)


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

    def updated_at(self) -> str:
        """文件最后修改时间（本地时区）。没建过就返回空串。

        `stat().st_mtime` 是 Unix 时间戳，`fromtimestamp` 会按本地时区还原
        ——和 session 那边不一样（那边的字符串本身是 UTC，得手动转）。
        """
        try:
            stamp = self.path.stat().st_mtime
        except OSError:
            return ""
        return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")

    def write_section(self, section: str, content: str) -> None:
        """把某一节的正文整体换掉，其余部分原样保留。

        用"按行切片再拼回去"，而不是"解析成数据结构再重新生成文件"：
        你在文件里手写的备注、自己加的章节都不会被程序弄丢。
        """
        if section not in self.sections:
            raise self.error_cls(
                f"「{section}」不是可写的章节，只能是：{'、'.join(self.sections)}"
            )

        content = demote_level2_headings(content.strip())
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

    def append_to_section(self, section: str, text: str) -> None:
        """在某一节末尾追加内容，不动已有的东西。

        和 write_section 的分工是刻意的：

        - `write_section` 是**整体替换**，适合画像、计划这种"提炼出来的视图"
          （原始材料还在别处，重写不会真的丢东西）
        - `append_to_section` 是**只增不改**，适合复盘记录这种日记。
          用替换的语义去写日记，会把历史一次冲掉。

        另外有个小规矩：如果这一节现在只有模板占位文字（"（还没记录。）"这种），
        就把它替换掉，而不是追加在它后面——否则占位符会永远留在文件里。
        """
        if section not in self.sections:
            raise self.error_cls(
                f"「{section}」不是可写的章节，只能是：{'、'.join(self.sections)}"
            )

        text = demote_level2_headings(text.strip())
        if not text:
            raise self.error_cls("内容不能为空")

        self.ensure_exists()
        lines = self.read().splitlines()
        for name, start, end in iter_sections(lines):
            if name != section:
                continue

            body = "\n".join(lines[start + 1 : end]).strip()
            if not body or _PLACEHOLDER.match(body):
                self.write_section(section, text)  # 占位符/空节：直接替换
                return

            # 找到这一节最后一个非空行，插在它后面
            insert_at = start + 1
            for index in range(end - 1, start, -1):
                if lines[index].strip():
                    insert_at = index + 1
                    break
            lines[insert_at:insert_at] = ["", *text.splitlines()]
            atomic_write(self.path, "\n".join(lines) + "\n")
            return

        raise self.error_cls(f"文件里没有「{section}」这一节")
