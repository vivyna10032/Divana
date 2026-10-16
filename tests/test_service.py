"""服务层的两条边：**失败时界面上看到什么**、以及每一笔有没有记账。

为什么值得单独盯：

- 失败原来是一个异常一路飙到网页上（"连接断了：…"）。现在把两类**可预期**的失败
  翻成人话（绕圈绕到上限、接口没通），其余照旧往外抛——401 和参数写错必须响，
  不能让一句"抱歉我出错了"把它们盖住。这条线很容易在以后重构时被划歪。
- 用量台账（usage.py）只有在**每个到模型的入口**都记一笔时才是完整的。漏一个，
  你不会发现，只会觉得"怎么这么便宜"。

没装 agents 就整体跳过（`python -m unittest discover -s tests` 不会报错）。

用法：python -m unittest tests.test_service -v
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

try:
    import agents  # noqa: F401

    HAVE_AGENTS = True
except ImportError:  # pragma: no cover - 只在缺依赖时走
    HAVE_AGENTS = False

if HAVE_AGENTS:
    from agents import MaxTurnsExceeded
    from openai import APIConnectionError, APITimeoutError

    from divana import usage
    from divana.config import Settings
    from divana.service import DivanaService
    from divana.usage import UsageStore


def fake_settings() -> "Settings":
    """最小的 Settings：不读 .env，也不连任何地方。"""
    return Settings(
        api_key="sk-test",
        base_url="https://example.invalid",
        model="deepseek-test",
        search_provider="tavily",
        search_api_key="",
        search_max_results=5,
        github_token="",
        timeout_seconds=120,
        max_retries=2,
        max_turns=3,
    )


def fake_result(text: str = "答一句") -> SimpleNamespace:
    """装成 Runner 的结果：只带上 to_reply / entry_from 会去摸的字段。"""
    return SimpleNamespace(
        final_output=text,
        new_items=[],
        context_wrapper=SimpleNamespace(
            usage=SimpleNamespace(
                requests=2,
                input_tokens=8000,
                output_tokens=400,
                input_tokens_details=SimpleNamespace(cached_tokens=7000),
                output_tokens_details=SimpleNamespace(reasoning_tokens=100),
            )
        ),
    )


class FakeStream:
    """装成 run_streamed 的返回值（空的流就够——这些用例不验流的内容）。"""

    def __init__(self, result: SimpleNamespace) -> None:
        self._result = result
        self.final_output = result.final_output
        self.new_items = result.new_items
        self.context_wrapper = result.context_wrapper

    async def stream_events(self):
        for event in ():
            yield event


@unittest.skipUnless(HAVE_AGENTS, "没有 agents 栈，跳过服务层测试")
class ServiceTestBase(unittest.TestCase):
    def setUp(self) -> None:
        # 只造一个"壳"：真正的 agent、会话、上下文在这几个用例里都用不到，
        # 要验的是**调用之后发生了什么**（以及传进去了什么）。
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.usage_path = Path(tmp.name) / "usage.jsonl"

        for target, value in (
            ("divana.service.build_agent", object()),
            ("divana.service.build_context", SimpleNamespace()),
            ("divana.service.open_session", SimpleNamespace(close=lambda: None)),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.service = DivanaService(fake_settings(), session_id="test")
        self.service.usage = UsageStore(self.usage_path)

    def entries(self):
        return UsageStore(self.usage_path).entries()

    def _ask(self, text: str, *, stream: bool = False):
        """同步地跑一次 ask：测试里不想要 async 样板。stream=True 走流式那条路。"""
        return asyncio.run(
            self.service.ask(text, on_event=(lambda event: None) if stream else None)
        )


@unittest.skipUnless(HAVE_AGENTS, "没有 agents 栈，跳过服务层测试")
class WiringTest(unittest.TestCase):
    """三个刹车是不是真的接上了——这部分以前全靠 SDK 默认，谁也没验过。"""

    def test_the_openai_client_accepts_timeout_and_retries(self) -> None:
        """先证明这几个参数是被 SDK 接受的（写错了会在这里就炸，而不是等你真发请求）。"""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key="sk-test",
            base_url="https://example.invalid",
            timeout=120,
            max_retries=2,
        )
        self.assertEqual(client.max_retries, 2)
        self.assertIsNotNone(client.timeout)

    def test_setup_installs_the_retry_counter(self) -> None:
        """setup_agents_sdk 顺便把"数重试"的钩子装上（重试只出现在日志里）。"""
        from divana.config import setup_agents_sdk

        setup_agents_sdk(fake_settings())
        self.assertIn(usage.watch_retries(), logging.getLogger("openai").handlers)


@unittest.skipUnless(HAVE_AGENTS, "没有 agents 栈，跳过服务层测试")
class AskTest(ServiceTestBase):
    def test_it_passes_the_turn_limit(self) -> None:
        """刹车真的接上了吗——不看代码，看传进去的参数。"""
        seen: dict = {}

        async def fake_run(*args, **kwargs):
            seen.update(kwargs)
            return fake_result()

        with patch("divana.service.Runner.run", new=fake_run):
            self._ask("在吗")
        self.assertEqual(seen["max_turns"], 3)
        self.assertEqual(seen["session"], self.service.session)

    def test_a_normal_answer_is_recorded(self) -> None:
        async def fake_run(*args, **kwargs):
            return fake_result("在的，你说")

        with patch("divana.service.Runner.run", new=fake_run):
            reply = self._ask("在吗")

        self.assertEqual(reply.text, "在的，你说")
        entry = self.entries()[0]
        self.assertEqual((entry.kind, entry.calls), ("chat", 2))
        self.assertEqual(entry.input_tokens, 8000)
        self.assertEqual(entry.error, "")

    def test_it_times_the_turn(self) -> None:
        """耗时记的是"从问出到答完"这一整段，不是某一次调用的时间。"""

        async def slow_run(*args, **kwargs):
            await asyncio.sleep(0.15)
            return fake_result()

        with patch("divana.service.Runner.run", new=slow_run):
            self._ask("在吗")

        self.assertGreaterEqual(self.entries()[0].seconds, 0.1)

    def test_the_streaming_path_records_too(self) -> None:
        """网页走的是流式那条路——记漏了就等于白记（你天天用的就是它）。"""
        with patch(
            "divana.service.Runner.run_streamed",
            new=lambda *args, **kwargs: FakeStream(fake_result("流式的答案")),
        ):
            reply = self._ask("在吗", stream=True)

        self.assertEqual(reply.text, "流式的答案")
        self.assertEqual(self.entries()[0].kind, "chat")

    def test_hitting_the_turn_limit_becomes_a_human_message(self) -> None:
        async def fake_run(*args, **kwargs):
            raise MaxTurnsExceeded("max turns exceeded")

        with patch("divana.service.Runner.run", new=fake_run):
            reply = self._ask("帮我把整本书讲一遍")

        self.assertIn("绕了 3 步", reply.text)
        self.assertIn("拆小", reply.text)
        entry = self.entries()[0]
        self.assertEqual(entry.error, "turns")
        self.assertEqual(entry.calls, 0)     # 没跑完，没有 token

    def test_a_dead_connection_becomes_a_human_message(self) -> None:
        request = httpx.Request("POST", "https://example.invalid/chat/completions")

        async def fake_run(*args, **kwargs):
            raise APITimeoutError(request=request)

        with patch("divana.service.Runner.run", new=fake_run):
            reply = self._ask("在吗")

        self.assertIn("试了 3 次", reply.text)   # 1 次 + 2 次重试
        self.assertEqual(self.entries()[0].error, "network")

    def test_connection_errors_are_covered_as_well(self) -> None:
        request = httpx.Request("POST", "https://example.invalid/chat/completions")

        async def fake_run(*args, **kwargs):
            raise APIConnectionError(request=request)

        with patch("divana.service.Runner.run", new=fake_run):
            reply = self._ask("在吗")
        self.assertEqual(self.entries()[0].error, "network")

    def test_unexpected_errors_are_not_swallowed(self) -> None:
        """401、参数写错这类必须响——不能被一句人话盖住，那才是真正的隐患。"""

        async def fake_run(*args, **kwargs):
            raise ValueError("程序自己的 bug")

        with patch("divana.service.Runner.run", new=fake_run):
            with self.assertRaises(ValueError):
                self._ask("在吗")
        self.assertEqual(self.entries(), [])


if __name__ == "__main__":
    unittest.main()
