"""学习计划：vault/plan.md。

和画像的区别值得说清楚：

- 画像是"你是谁、学到哪了"——短、稳定、**每轮都要在眼前**，所以进 instructions
- 计划是"接下来怎么走"——会长、会改、**只在聊到方向时才用得上**，所以不进
  instructions，靠工具按需读

同一份"上下文预算"的取舍，这是第三次应用（第一次是画像 vs 笔记，第二次是
笔记的摘要 vs 全文）。
"""

from __future__ import annotations

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
