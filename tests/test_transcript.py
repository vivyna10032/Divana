"""总结流程里的文本处理测试。不联网、不碰模型。

下面的样例形状是照着 data/divana.db 里的真实记录写的——用户消息的 content
是字符串，助手消息的 content 是部件列表。这一点是查了真实数据才知道的。

用法：python -m unittest tests.test_transcript -v
"""

from __future__ import annotations

import unittest

from divana.transcript import (
    DEFAULT_TITLE,
    history_messages,
    message_text,
    render_item,
    render_transcript,
    split_title,
)


def user_msg(text: str) -> dict:
    return {"role": "user", "content": text}


def assistant_msg(text: str) -> dict:
    return {
        "type": "message",
        "role": "assistant",
        "id": "msg_1",
        "status": "completed",
        "content": [
            {"type": "output_text", "text": text, "annotations": [], "logprobs": None}
        ],
    }


def tool_call(name: str, arguments: str = '{"query": "x"}') -> dict:
    return {
        "type": "function_call",
        "name": name,
        "arguments": arguments,
        "call_id": "call_1",
        "id": "fc_1",
    }


def tool_output(output: str) -> dict:
    return {"type": "function_call_output", "call_id": "call_1", "output": output}


def reasoning(text: str) -> dict:
    return {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": text}]}


class MessageTextTest(unittest.TestCase):
    def test_plain_string(self) -> None:
        self.assertEqual(message_text("  你好  "), "你好")

    def test_part_list(self) -> None:
        self.assertEqual(message_text([{"type": "output_text", "text": "回答"}]), "回答")

    def test_multiple_parts_are_joined(self) -> None:
        self.assertEqual(
            message_text([{"text": "第一段"}, {"text": "第二段"}]), "第一段\n第二段"
        )

    def test_unknown_shapes_return_empty(self) -> None:
        for value in (None, 42, {"text": "x"}, [{"没有 text": 1}], ["裸字符串"]):
            self.assertEqual(message_text(value), "")


class RenderItemTest(unittest.TestCase):
    def test_user_and_assistant(self) -> None:
        self.assertEqual(render_item(user_msg("问题")), "你：问题")
        self.assertEqual(render_item(assistant_msg("回答")), "Divana：回答")

    def test_tool_call_becomes_a_marker(self) -> None:
        line = render_item(tool_call("search_web"))
        self.assertIn("[工具] search_web", line)

    def test_long_tool_arguments_are_truncated(self) -> None:
        line = render_item(tool_call("search_web", "字" * 500))
        self.assertLess(len(line), 120)
        self.assertTrue(line.endswith(")"))
        self.assertIn("…", line)

    def test_tool_output_and_reasoning_are_dropped(self) -> None:
        self.assertEqual(render_item(tool_output("一大堆原始数据")), "")
        self.assertEqual(render_item(reasoning("模型在想什么")), "")

    def test_empty_content_is_dropped(self) -> None:
        self.assertEqual(render_item(user_msg("   ")), "")
        self.assertEqual(render_item(assistant_msg("")), "")

    def test_unknown_item_returns_empty(self) -> None:
        self.assertEqual(render_item(None), "")
        self.assertEqual(render_item({"foo": "bar"}), "")


class RenderTranscriptTest(unittest.TestCase):
    def test_keeps_conversation_in_order(self) -> None:
        text = render_transcript([user_msg("一"), assistant_msg("二"), user_msg("三")])
        self.assertLess(text.index("你：一"), text.index("Divana：二"))
        self.assertLess(text.index("Divana：二"), text.index("你：三"))

    def test_skips_noise(self) -> None:
        text = render_transcript(
            [
                user_msg("问题"),
                tool_call("search_web"),
                tool_output("搜索结果原文"),
                reasoning("思考过程"),
                assistant_msg("回答"),
            ]
        )
        self.assertIn("问题", text)
        self.assertIn("回答", text)
        self.assertIn("[工具] search_web", text)
        self.assertNotIn("搜索结果原文", text)
        self.assertNotIn("思考过程", text)

    def test_empty_input(self) -> None:
        self.assertEqual(render_transcript([]), "")

    def test_all_noise_gives_empty(self) -> None:
        self.assertEqual(render_transcript([tool_output("x"), reasoning("y")]), "")

    def test_keeps_the_most_recent_when_too_long(self) -> None:
        items: list[dict] = []
        for index in range(30):
            items.append(user_msg(f"第{index}条" + "字" * 200))
        text = render_transcript(items, max_chars=800)

        self.assertTrue(text.startswith("（这次对话比较长"))
        self.assertIn("第29条", text)
        self.assertNotIn("第0条", text)

    def test_a_single_huge_message_is_still_kept(self) -> None:
        text = render_transcript([user_msg("字" * 5000)], max_chars=100)
        self.assertIn("字" * 100, text)

    def test_short_conversation_has_no_omission_notice(self) -> None:
        text = render_transcript([user_msg("一"), assistant_msg("二")])
        self.assertNotIn("省略", text)


class SplitTitleTest(unittest.TestCase):
    def test_extracts_heading(self) -> None:
        title, body = split_title("# Agent 的记忆分层\n\n## 关键结论\n\n正文")
        self.assertEqual(title, "Agent 的记忆分层")
        self.assertEqual(body, "## 关键结论\n\n正文")

    def test_falls_back_when_model_forgets_the_title(self) -> None:
        title, body = split_title("## 关键结论\n\n正文")
        self.assertEqual(title, DEFAULT_TITLE)
        self.assertIn("正文", body)

    def test_title_only(self) -> None:
        self.assertEqual(split_title("# 标题"), ("标题", ""))

    def test_empty_hash_is_ignored(self) -> None:
        title, body = split_title("#\n正文")
        self.assertEqual(title, DEFAULT_TITLE)

    def test_empty_output(self) -> None:
        self.assertEqual(split_title("   "), (DEFAULT_TITLE, ""))

    def test_leading_blank_lines_are_trimmed(self) -> None:
        title, _ = split_title("\n\n# 标题\n正文")
        self.assertEqual(title, "标题")


class HistoryMessagesTest(unittest.TestCase):
    """给界面渲染历史用：只保留"人话"，工具和思考过程都跳过。"""

    def test_keeps_only_user_and_assistant(self) -> None:
        items = [
            user_msg("问"),
            tool_call("search_web"),
            tool_output("一大段原始数据"),
            reasoning("模型在想什么"),
            assistant_msg("答"),
        ]
        self.assertEqual(
            history_messages(items), [("user", "问"), ("assistant", "答")]
        )

    def test_preserves_order(self) -> None:
        items = [user_msg("一"), assistant_msg("二"), user_msg("三")]
        self.assertEqual([role for role, _ in history_messages(items)], ["user", "assistant", "user"])

    def test_skips_empty_text(self) -> None:
        self.assertEqual(history_messages([user_msg("   "), assistant_msg("")]), [])

    def test_ignores_unknown_items(self) -> None:
        self.assertEqual(history_messages([None, "字符串", {"foo": "bar"}]), [])

    def test_empty_input(self) -> None:
        self.assertEqual(history_messages([]), [])


if __name__ == "__main__":
    unittest.main()
