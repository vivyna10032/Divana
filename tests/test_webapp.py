"""网页层测试：用一个假 service 把 HTTP 和 SSE 真的跑起来。

这一层平时测不到——它要起服务器、要 agent 栈。但**事件名是前后端的口头约定**，
光靠肉眼看很容易对不上（前端等 delta，后端发 text_delta，界面上就永远没字）。
所以这里塞一个假 service，只测"HTTP 进、SSE 出"这一段。

没装 starlette 就整体跳过（`python -m unittest discover -s tests` 不会报错）。

用法：python -m unittest tests.test_webapp -v
"""

from __future__ import annotations

import json
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace

from divana.contracts import Reply, Summary, TextDelta, ToolCall, ToolCalled
from divana.notes import Note
from pathlib import Path

try:
    import starlette  # noqa: F401
    import uvicorn

    HAVE_WEB = True
except ImportError:  # pragma: no cover - 只在缺依赖时走
    HAVE_WEB = False

if HAVE_WEB:
    from divana.webapp import create_app


class _FakeStore:
    """假的画像/计划：只实现网页会调的那一个方法。"""

    def __init__(self, label: str) -> None:
        self.label = label

    def read_section(self, section: str) -> str:
        return f"{self.label}的「{section}」"


class _FakeService:
    """假的服务层。真实那套要 agent 栈，这里只关心接口形状。"""

    session_id = "test-session"

    def __init__(self) -> None:
        self.context = SimpleNamespace(
            profile=_FakeStore("画像"),
            plan=_FakeStore("计划"),
        )
        self.closed = False

    def list_notes(self) -> list:
        return [
            SimpleNamespace(title="注意力机制", date="2026-09-15", tags=("transformer",)),
            SimpleNamespace(title="梯度下降", date="2026-09-10", tags=()),
        ]

    async def ask(self, text: str, *, on_event=None) -> Reply:
        if on_event is not None:
            on_event(ToolCalled(ToolCall("search_web", '{"query": "x"}')))
            on_event(TextDelta("第一段"))
            on_event(TextDelta("第二段"))
        return Reply(
            text="第一段第二段",
            tool_calls=[ToolCall("search_web", '{"query": "x"}')],
        )

    async def summarize(self) -> Summary:
        return Summary(
            markdown="# 测试标题\n\n正文",
            note=Note(path=Path("vault/notes/x.md"), title="测试标题", tags=()),
        )

    def close(self) -> None:
        self.closed = True


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@unittest.skipUnless(HAVE_WEB, "没有 starlette / uvicorn，跳过网页层测试")
class WebAppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service = _FakeService()
        cls.port = _free_port()
        app = create_app(cls.service)
        config = uvicorn.Config(
            app, host="127.0.0.1", port=cls.port, log_level="error"
        )
        cls.server = uvicorn.Server(config)
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()

        deadline = time.time() + 15
        while not cls.server.started and time.time() < deadline:
            time.sleep(0.05)
        if not cls.server.started:
            raise RuntimeError("测试服务器没起来")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.should_exit = True
        cls.thread.join(timeout=10)

    # ------------------------------------------------------------------ 工具

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str):
        return urllib.request.urlopen(self.url(path), timeout=20)

    def post(self, path: str, payload: dict | None = None):
        body = json.dumps(payload or {}).encode()
        request = urllib.request.Request(
            self.url(path), data=body, headers={"Content-Type": "application/json"}
        )
        return urllib.request.urlopen(request, timeout=20)

    # ------------------------------------------------------------------ 用例

    def test_index_page_is_served(self) -> None:
        with self.get("/") as response:
            html = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn("Divana", html)
        self.assertIn("/api/ask", html)  # 页面确实连到了后端

    def test_state_returns_the_sidebar_data(self) -> None:
        with self.get("/api/state") as response:
            data = json.loads(response.read())

        self.assertEqual(data["goal"], "画像的「目标」")
        self.assertEqual(data["plan_now"], "计划的「现在的位置」")
        self.assertEqual(data["plan_next"], "计划的「下一步」")
        self.assertEqual(len(data["notes"]), 2)
        self.assertEqual(data["note_count"], 2)
        self.assertEqual(data["notes"][0]["title"], "注意力机制")

    def test_ask_streams_tool_then_deltas_then_done(self) -> None:
        """顺序很重要：工具调用在正文之前，前端照着这个顺序画。"""
        with self.post("/api/ask", {"text": "你好"}) as response:
            # Starlette 会补上 charset，所以只断言前缀
            self.assertTrue(
                response.headers["content-type"].startswith("text/event-stream")
            )
            body = response.read().decode()

        names = [
            line[7:].strip()
            for line in body.splitlines()
            if line.startswith("event: ")
        ]
        self.assertEqual(names, ["tool", "delta", "delta", "done"])

    def test_delta_frames_carry_the_text(self) -> None:
        with self.post("/api/ask", {"text": "你好"}) as response:
            body = response.read().decode()

        payloads = [
            json.loads(line[6:])
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        self.assertEqual(payloads[0]["name"], "search_web")
        self.assertEqual([p["text"] for p in payloads[1:3]], ["第一段", "第二段"])
        self.assertEqual(payloads[3]["text"], "第一段第二段")

    def test_empty_question_is_rejected(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/ask", {"text": "   "})
        self.assertEqual(ctx.exception.code, 400)

    def test_summary_returns_markdown_and_path(self) -> None:
        with self.post("/api/summary") as response:
            data = json.loads(response.read())
        self.assertIn("测试标题", data["markdown"])
        self.assertIn("x.md", data["path"])

    def test_unknown_path_is_404(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/nope")
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
