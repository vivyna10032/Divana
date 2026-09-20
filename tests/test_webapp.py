"""网页层测试：用一个假 service 把 HTTP 和 SSE 真的跑起来。

这一层平时测不到——它要起服务器、要 agent 栈。但**事件名是前后端的口头约定**，
光靠肉眼看很容易对不上（前端等 delta，后端发 text_delta，界面上就永远没字）。
所以这里塞一个假 service，只测"HTTP 进、SSE 出"这一段。

没装 starlette 就整体跳过（`python -m unittest discover -s tests` 不会报错）。

用法：python -m unittest tests.test_webapp -v
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from types import SimpleNamespace

from divana.contracts import Reply, Summary, TextDelta, ToolCall, ToolCalled
from divana.notes import Note, NoteError, NoteInfo
from divana.plan import Milestone, PlanProgress, Stage
from divana.session import SessionInfo
from pathlib import Path

try:
    import starlette  # noqa: F401
    import uvicorn

    HAVE_WEB = True
except ImportError:  # pragma: no cover - 只在缺依赖时走
    HAVE_WEB = False

if HAVE_WEB:
    from divana.webapp import create_app


WEB_DIR = Path(__file__).resolve().parent.parent / "web"
NODE = shutil.which("node")


def run_markdown(text: str) -> str:
    """在 node 里跑一遍 renderMarkdown，把结果拿回来。

    markdown.js 不碰 DOM，所以能这么测——这也是当初把它从 app.js 里拆出来的原因。
    """
    script = (
        f"const m = require({json.dumps((WEB_DIR / 'markdown.js').as_posix())});"
        "process.stdout.write(m.renderMarkdown(process.argv[1]));"
    )
    result = subprocess.run(
        [NODE, "-e", script, text],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout


class WebAssetTest(unittest.TestCase):
    """不需要起服务器，也不需要 node。"""

    def test_index_loads_markdown_before_app(self) -> None:
        # app.js 直接用 renderMarkdown，顺序反了就整页报错
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index("markdown.js"), html.index("app.js"))


@unittest.skipUnless(NODE, "需要 node 才能检查 / 运行前端脚本")
class FrontendScriptTest(unittest.TestCase):
    """用 node 跑前端脚本。没有 node 就整体跳过。"""

    def test_syntax_is_valid(self) -> None:
        for name in ("markdown.js", "app.js"):
            result = subprocess.run(
                [NODE, "--check", str(WEB_DIR / name)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, f"{name} 语法错误：{result.stderr}")

    def test_renders_a_table(self) -> None:
        html = run_markdown("| 项目 | Star |\n|---|---|\n| smolagents | 29.3k |")
        self.assertIn("<table>", html)
        self.assertIn("<th>项目</th>", html)
        self.assertIn("<td>29.3k</td>", html)
        self.assertNotIn("|---|", html)  # 分隔行不该出现在结果里

    def test_renders_a_code_block(self) -> None:
        html = run_markdown("说明：\n\n```python\nprint(1)\n```\n")
        self.assertIn("<pre", html)
        self.assertIn('data-lang="python"', html)
        self.assertIn("print(1)", html)

    def test_escapes_html_from_the_model(self) -> None:
        """模型输出里的标签必须被转义——这是唯一一条安全相关的断言。"""
        html = run_markdown("<script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_renders_bold_and_links(self) -> None:
        html = run_markdown("**粗体** 和 [链接](https://example.com)")
        self.assertIn("<strong>粗体</strong>", html)
        self.assertIn('href="https://example.com"', html)

    def test_line_without_divider_is_not_a_table(self) -> None:
        html = run_markdown("| 这个只是竖线 | 不是表格 |")
        self.assertNotIn("<table>", html)


class _FakeStore:
    """假的画像/计划：只实现网页会调的那一个方法。"""

    def __init__(self, label: str) -> None:
        self.label = label
        self.path = Path("vault") / f"{label}.md"

    def read_section(self, section: str) -> str:
        return f"{self.label}的「{section}」"

    def progress(self) -> PlanProgress:
        return PlanProgress(
            stages=(
                Stage(
                    name="阶段一",
                    milestones=(Milestone("做完 A", True), Milestone("做完 B", False)),
                ),
            ),
            done=1,
            total=2,
        )

    def updated_at(self) -> str:
        return "2026-09-20 10:23"


class _FakeService:
    """假的服务层。真实那套要 agent 栈，这里只关心接口形状。"""

    session_id = "test-session"

    def __init__(self) -> None:
        self.context = SimpleNamespace(
            profile=_FakeStore("画像"),
            plan=_FakeStore("计划"),
        )
        self.closed = False
        self._notes = [
            NoteInfo(
                path=Path("vault/notes/2026-09-15-注意力机制.md"),
                title="注意力机制",
                date="2026-09-15",
                tags=("transformer",),
                body="# 注意力机制\n\n让模型自己决定看哪里。",
            ),
            NoteInfo(
                path=Path("vault/notes/2026-09-10-梯度下降.md"),
                title="梯度下降",
                date="2026-09-10",
                tags=("optimizer",),
                body="# 梯度下降\n\n沿着梯度反方向走一步。",
            ),
        ]

    def list_notes(self) -> list:
        return list(self._notes)

    def search_notes(self, query: str, limit: int = 5, *, tag: str = "") -> list:
        """形状照真实实现来：返回 (笔记, 摘要)。匹配规则简化了——
        真正的匹配逻辑在 test_notes.py 里测。"""
        picked = [n for n in self._notes if not tag or tag in n.tags]
        if query not in {"", "*"}:
            key = query.lower()
            picked = [
                n for n in picked
                if key in n.title.lower()
                or key in n.body.lower()
                or any(key in t.lower() for t in n.tags)
            ]
        return [(n, n.body.split("\n\n")[-1]) for n in picked[:limit]]

    def read_note(self, name: str) -> NoteInfo:
        for info in self._notes:
            if info.path.name == name:
                return info
        raise NoteError(f"没找到「{name}」")

    def plan_progress(self) -> PlanProgress:
        return self.context.plan.progress()

    def list_sessions(self) -> list:
        return [
            SessionInfo(
                session_id="test-session",
                created_at="2026-09-17 02:00",
                updated_at="2026-09-17 03:00",
                message_count=12,
                title="帮我看看这个项目",
            ),
            SessionInfo(
                session_id="chat-20260916-200000",
                created_at="2026-09-16 12:00",
                updated_at="2026-09-16 12:30",
                message_count=4,
                title="旧对话",
            ),
        ]

    def switch_session(self, session_id: str) -> None:
        self.session_id = session_id

    async def history(self, limit: int = 40) -> list:
        return [("user", "问一句"), ("assistant", "答一句")]

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
        # 页面把样式和脚本拆出去了，两个引用都得在
        self.assertIn("/static/style.css", html)
        self.assertIn("/static/app.js", html)

    def test_static_files_are_mounted(self) -> None:
        with self.get("/static/app.js") as response:
            js = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn("/api/ask", js)  # 前端确实连到了后端

        with self.get("/static/style.css") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("--green", response.read().decode())

    def test_notes_list_returns_entries_and_all_tags(self) -> None:
        with self.get("/api/notes") as response:
            data = json.loads(response.read())

        self.assertEqual(len(data["notes"]), 2)
        # tags 是"所有笔记出现过的标签"，和筛选无关，否则点完筛选标签就消失了
        self.assertEqual(data["tags"], ["optimizer", "transformer"])
        self.assertEqual(data["notes"][0]["name"], "2026-09-15-注意力机制.md")
        self.assertIn("snippet", data["notes"][0])

    def test_notes_search_by_keyword(self) -> None:
        # URL 里不能直接放中文，得先编码——浏览器那边由 URLSearchParams 代劳
        with self.get(f"/api/notes?q={urllib.parse.quote('梯度')}") as response:
            data = json.loads(response.read())
        self.assertEqual([n["title"] for n in data["notes"]], ["梯度下降"])

    def test_notes_filter_by_tag(self) -> None:
        with self.get("/api/notes?tag=transformer") as response:
            data = json.loads(response.read())
        self.assertEqual([n["title"] for n in data["notes"]], ["注意力机制"])

    def test_note_detail_returns_body(self) -> None:
        name = urllib.parse.quote("2026-09-15-注意力机制.md")
        with self.get(f"/api/note?name={name}") as response:
            data = json.loads(response.read())
        self.assertEqual(data["title"], "注意力机制")
        self.assertIn("让模型自己决定看哪里", data["body"])
        self.assertEqual(data["tags"], ["transformer"])

    def test_plan_returns_progress_and_stages(self) -> None:
        with self.get("/api/plan") as response:
            data = json.loads(response.read())

        self.assertEqual(data["now"], "计划的「现在的位置」")
        self.assertEqual(data["next"], "计划的「下一步」")
        self.assertEqual((data["done"], data["total"], data["percent"]), (1, 2, 50))
        self.assertEqual(data["stages"][0]["name"], "阶段一")
        self.assertTrue(data["stages"][0]["milestones"][0]["done"])
        self.assertFalse(data["stages"][0]["milestones"][1]["done"])

    def test_profile_returns_all_sections(self) -> None:
        with self.get("/api/profile") as response:
            data = json.loads(response.read())

        self.assertEqual(data["goal"], "画像的「目标」")
        self.assertEqual(data["level"], "画像的「当前水平」")
        self.assertEqual(data["known"], "画像的「已掌握」")
        self.assertEqual(data["weak"], "画像的「薄弱点」")
        self.assertEqual(data["habits"], "画像的「学习习惯」")
        self.assertEqual(data["updated_at"], "2026-09-20 10:23")
        self.assertEqual(data["note_count"], 2)
        self.assertEqual(data["percent"], 50)
        self.assertTrue(data["path"].endswith(".md"))

    def test_sessions_list_marks_the_current_one(self) -> None:
        with self.get("/api/sessions") as response:
            data = json.loads(response.read())

        self.assertEqual(data["current"], "test-session")
        self.assertEqual(len(data["sessions"]), 2)
        self.assertEqual(data["sessions"][0]["title"], "帮我看看这个项目")
        self.assertEqual(data["sessions"][0]["message_count"], 12)

    def test_switch_session_changes_the_current_one(self) -> None:
        with self.get("/api/sessions") as response:
            original = json.loads(response.read())["current"]
        try:
            with self.post("/api/sessions/switch", {"session_id": "another"}) as res:
                self.assertEqual(json.loads(res.read())["id"], "another")
            with self.get("/api/sessions") as response:
                self.assertEqual(json.loads(response.read())["current"], "another")
        finally:
            self.post("/api/sessions/switch", {"session_id": original})

    def test_switch_without_id_is_rejected(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/sessions/switch", {})
        self.assertEqual(ctx.exception.code, 400)

    def test_new_session_switches_to_a_fresh_id(self) -> None:
        with self.get("/api/sessions") as response:
            original = json.loads(response.read())["current"]
        try:
            with self.post("/api/sessions/new") as response:
                new_id = json.loads(response.read())["id"]
            self.assertTrue(new_id.startswith("chat-"))
            with self.get("/api/sessions") as response:
                self.assertEqual(json.loads(response.read())["current"], new_id)
        finally:
            self.post("/api/sessions/switch", {"session_id": original})

    def test_history_returns_displayable_messages(self) -> None:
        with self.get("/api/history") as response:
            data = json.loads(response.read())
        self.assertEqual(
            data["messages"],
            [{"role": "user", "text": "问一句"}, {"role": "assistant", "text": "答一句"}],
        )

    def test_note_detail_without_name_is_400(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/note")
        self.assertEqual(ctx.exception.code, 400)

    def test_unknown_note_is_404(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/api/note?name=nope.md")
        self.assertEqual(ctx.exception.code, 404)

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
