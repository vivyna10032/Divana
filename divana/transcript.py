"""总结流程里的两段文本处理（都不碰模型，所以能离线测试）：

1. 会话记录 → 一段可读的对话文本，给总结者读
2. 总结者的输出 → （标题, 正文），好拿去落盘

为什么要这个模块：主 agent 的上下文只有最近若干轮，它**看不到自己的完整历史**。
要总结"这次聊了什么"，就得直接去读会话库（data/divana.db），把记录还原成文本。
这也是"总结对话"比"总结网页"麻烦的地方——网页是抓来的，对话是散在库里的。

第一段要这么绕，是因为主 agent 看不到自己的完整历史（上下文只有最近若干条），
只能直接去读会话库 data/divana.db，把记录还原成文本。
"""

from __future__ import annotations

from typing import Any

# 一次总结最多带多少字符的对话进模型。太长就只带最近的部分。
MAX_TRANSCRIPT_CHARS = 20000

# 工具调用的参数留多长（arguments 是 JSON 字符串）
MAX_TOOL_ARG_CHARS = 80

# 太短的对话不值得总结（比如打开就退出）
MIN_TRANSCRIPT_CHARS = 60

# 总结者没写标题时用的兜底标题
DEFAULT_TITLE = "对话总结"


def message_text(content: Any) -> str:
    """从一条消息的 content 里取出纯文本。

    真实数据里两种形状都有，这是查了 data/divana.db 才知道的：

    - 用户消息：`content` 直接是字符串
    - 助手消息：`content` 是部件列表，形如 [{"type": "output_text", "text": ...}]

    照直觉只处理一种的话，另一种会读出空字符串——总结就变成了半盲。
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            part["text"]
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        return "\n".join(parts).strip()
    return ""


def render_item(item: Any) -> str:
    """把一条记录变成一行文本。不认识的形状返回空串（跳过，不报错）。"""
    if not isinstance(item, dict):
        return ""

    kind = item.get("type")
    role = item.get("role")

    if kind == "function_call":
        name = item.get("name") or "未知工具"
        arguments = str(item.get("arguments") or "")
        if len(arguments) > MAX_TOOL_ARG_CHARS:
            arguments = arguments[:MAX_TOOL_ARG_CHARS] + "…"
        return f"[工具] {name}({arguments})"

    # 工具结果和模型思考过程都丢掉：前者是给模型看的原始数据，后者不是对话。
    # 它们占了那次真实会话 87 条记录里的 40 条，留着只会稀释材料。
    if kind in {"function_call_output", "reasoning"}:
        return ""

    if role == "user":
        text = message_text(item.get("content"))
        return f"你：{text}" if text else ""

    if role == "assistant":
        text = message_text(item.get("content"))
        return f"Divana：{text}" if text else ""

    return ""


def render_transcript(items: list[Any], *, max_chars: int = MAX_TRANSCRIPT_CHARS) -> str:
    """拼出对话文本。超长时**保留最近的**，并注明省略了多少。

    为什么从后往前留：近处的对话通常承接前面的结论；而且"他是谁"这类长期信息
    本来就存在学习者画像里，开头那段目标陈述不至于全丢。
    """
    lines = [line for line in (render_item(item) for item in items) if line]
    if not lines:
        return ""

    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        if kept and used + len(line) > max_chars:
            omitted = len(lines) - len(kept)
            head = f"（这次对话比较长，最早的 {omitted} 段已省略）\n\n"
            return head + "\n\n".join(reversed(kept))
        kept.append(line)
        used += len(line) + 2

    return "\n\n".join(reversed(kept))


def history_messages(items: list[Any]) -> list[tuple[str, str]]:
    """把会话记录变成 [(角色, 文本)]，给界面渲染历史。

    只保留"人话"：用户和助手说过的话。工具调用、工具结果、模型思考过程都跳过
    ——那些是给模型看的，不是对话内容（在界面上堆出来只会淹没正文）。

    角色用 "user" / "assistant"，正好对应网页里的左右两种气泡。
    """
    messages: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role in {"user", "assistant"}:
            text = message_text(item.get("content"))
            if text:
                messages.append((role, text))
    return messages


def split_title(markdown: str, *, default_title: str = DEFAULT_TITLE) -> tuple[str, str]:
    """把总结者的输出拆成标题和正文。

    约定是第一行写 `# 标题`，但**模型不一定会照做**，所以这里必须兜底：
    没写标题就用默认的，正文原样保留。程序里"约定了但别指望"的地方，
    都该有这样一个兜底。
    """
    text = markdown.strip()
    if not text:
        return default_title, ""

    lines = text.splitlines()
    first = lines[0].strip()
    if first.startswith("# "):
        title = first[2:].strip()
        if title:
            return title, "\n".join(lines[1:]).strip()
    return default_title, text
