"""学习者画像：Divana 对"你是谁、学到哪了"的长期记忆。

画像是一份给人看的 markdown，放在 vault/profile.md。它每轮对话都会被拼进
模型的 instructions，所以必须保持短小——这也是为什么写入要限制长度。

读写的通用机制在 markdown_store.py 里（画像和计划共用），这里只负责"画像
是哪几节、长什么样"。不碰模型，所以可以离线测试（见 tests/test_profile.py）。
"""

from __future__ import annotations

from pathlib import Path

from .markdown_store import MarkdownStoreError, SectionedMarkdown

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

class ProfileError(MarkdownStoreError):
    """画像写入时的可预期错误。工具会把它翻译成给模型看的话，而不是抛出去。"""


class ProfileStore(SectionedMarkdown):
    """读写 vault/profile.md。具体机制都在 SectionedMarkdown 里。"""

    error_cls = ProfileError

    def __init__(self, path: Path | None = None) -> None:
        super().__init__(
            path if path is not None else DEFAULT_PROFILE_PATH,
            sections=SECTIONS,
            template=TEMPLATE,
            max_section_chars=MAX_SECTION_CHARS,
        )
