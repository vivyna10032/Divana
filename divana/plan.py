"""学习计划：vault/plan.md。

和画像的区别值得说清楚：

- 画像是"你是谁、学到哪了"——短、稳定、**每轮都要在眼前**，所以进 instructions
- 计划是"接下来怎么走"——会长、会改、**只在聊到方向时才用得上**，所以不进
  instructions，靠工具按需读

同一份"上下文预算"的取舍，这是第三次应用（第一次是画像 vs 笔记，第二次是
笔记的摘要 vs 全文）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .markdown_store import MarkdownStoreError, SectionedMarkdown

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PLAN_PATH = PROJECT_ROOT / "vault" / "plan.md"

# 可写的章节。写死是有意的：模型只能改这几节，你自己加的章节它碰不到。
SECTIONS = ("现在的位置", "下一步", "路线图", "复盘记录")

# 单节长度上限。路线图那节会长一些，所以比画像宽松。
MAX_SECTION_CHARS = 2500

TEMPLATE = """# 学习计划

> 这份计划由 Divana 维护，你可以随时手改——她下次启动会读到最新版本。
>
> 在终端里输入 /plan 可以随时查看。

## 现在的位置

（还没定。跟她说说你想从哪儿开始。）

## 下一步

（还没定。方向定下来后，这里会放一两件马上能做的事。）

## 路线图

（还没有阶段。聊清楚方向之后，你们可以一起把路线拆出来。）

## 复盘记录

（还没有复盘。每次回顾的结论会记在这里。）
"""


class PlanError(MarkdownStoreError):
    """计划写入时的可预期错误。工具会把它翻译成给模型看的话。"""


# `- [x] 内容` / `* [ ] 内容` 这几种写法都认；括号里允许带空格（[ x ] 也认）
_CHECKBOX = re.compile(r"^\s*[-*]\s*\[\s*(?P<mark>[ xX✓✔])\s*\]\s*(?P<text>.+?)\s*$")
# 阶段标题：## / ### / ####
_STAGE_HEADING = re.compile(r"^\s*#{2,4}\s+(?P<name>.+?)\s*$")

_DONE_MARKS = {"x", "X", "✓", "✔"}

# 里程碑前面没有任何标题时，归到这个阶段名下
UNGROUPED = "未分组"


@dataclass(frozen=True)
class Milestone:
    text: str
    done: bool


@dataclass(frozen=True)
class Stage:
    name: str
    milestones: tuple[Milestone, ...]


@dataclass(frozen=True)
class PlanProgress:
    stages: tuple[Stage, ...]
    done: int
    total: int

    @property
    def percent(self) -> int:
        """完成度百分比。一个里程碑都没有时返回 0（而不是除以零）。"""
        if not self.total:
            return 0
        return round(self.done * 100 / self.total)


def parse_milestones(text: str) -> PlanProgress:
    """从 markdown 里数出里程碑。

    这里的关键是**容错**：checkbox 藏在自由格式的 markdown 里，是模型写出来的，
    格式不可能百分百稳定。所以：

    - 认 `- [ ]`、`- [x]`、`- [X]`、`* [ ]` 这些写法
    - 写坏的（比如 `- []` 或 `- [x` 少了右括号）当成普通文字跳过，不进计数
    - 阶段标题认 ## 到 ####；标题之前的里程碑归到"未分组"
    - 其它文字（说明、空行）一律忽略

    数错一个的代价只是进度条不准，但**崩溃就不行**，所以宁可少认不可报错。
    """
    stages: list[Stage] = []
    current_name = UNGROUPED
    current: list[Milestone] = []
    done = 0
    total = 0

    def flush() -> None:
        if current:
            stages.append(Stage(name=current_name, milestones=tuple(current)))

    for raw in text.splitlines():
        heading = _STAGE_HEADING.match(raw)
        if heading:
            flush()
            current = []
            current_name = heading.group("name")
            continue

        checkbox = _CHECKBOX.match(raw)
        if checkbox:
            is_done = checkbox.group("mark") in _DONE_MARKS
            current.append(Milestone(text=checkbox.group("text"), done=is_done))
            total += 1
            done += 1 if is_done else 0

    flush()
    return PlanProgress(stages=tuple(stages), done=done, total=total)


class PlanStore(SectionedMarkdown):
    """读写 vault/plan.md。具体机制都在 SectionedMarkdown 里。"""

    error_cls = PlanError

    def __init__(self, path: Path | None = None) -> None:
        super().__init__(
            path if path is not None else DEFAULT_PLAN_PATH,
            sections=SECTIONS,
            template=TEMPLATE,
            max_section_chars=MAX_SECTION_CHARS,
        )

    def progress(self) -> PlanProgress:
        """数一数路线图里的里程碑。

        只看"路线图"这一节——"下一步"那节里也可能有 `- [ ]`，
        混进来会让进度虚高。
        """
        return parse_milestones(self.read_section("路线图"))
