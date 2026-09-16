"""最小可用的本地网页：左边聊天（流式），右边看一眼积累的东西。

四个接口：

- `GET  /`            单页（web/index.html）
- `GET  /api/state`   画像的目标/水平 + 计划的"现在的位置/下一步" + 最近几篇笔记
- `POST /api/ask`     问一句，用 SSE 把过程流回来（文字片段 + 工具调用）
- `POST /api/summary` 把这次对话整理成笔记

为什么用 Starlette 而不是 FastAPI：环境里本来就有（openai-agents 通过 mcp 带进来的），
不用装任何东西；而且 Starlette 就是 FastAPI 底下那一层。想换 FastAPI 的话，
路由写法几乎一样。

这里**只负责 HTTP**：把请求转成对 service 的调用，再把结果编码成 JSON / SSE。
业务逻辑一个字都不在这——这正是上一轮抽服务层换来的。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .config import Settings, setup_agents_sdk
from .contracts import AskEvent, SummarizeError, TextDelta, ToolCalled
from .notes import NoteError

if TYPE_CHECKING:  # 只为类型标注：运行时不 import，这样这个模块能脱离 agent 栈单独测
    from .service import DivanaService

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
INDEX_PATH = WEB_DIR / "index.html"

# 只监听本机。**别改成 0.0.0.0**——这个服务能读你本地的文件、用你的 API key，
# 暴露到局域网就等于把它们交出去。
HOST = "127.0.0.1"
PORT = 8765

# 侧边栏放几篇最近的笔记
SIDEBAR_NOTES = 5

# 知识库页面一次最多列几篇
NOTES_LIMIT = 200


def service_of(request: Request) -> "DivanaService":
    return request.app.state.service


def sse(event: str, data: dict[str, Any]) -> str:
    """拼一帧 SSE。

    格式就这么简单：`event: 名字` 一行，`data: JSON` 一行，再空一行表示这帧结束。
    知道这个之后，你就明白 SSE 其实只是"服务器慢慢吐的纯文本"。
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def index(request: Request) -> Response:
    if not INDEX_PATH.exists():
        return JSONResponse(
            {"error": f"找不到页面文件：{INDEX_PATH}"}, status_code=500
        )
    return FileResponse(INDEX_PATH)


async def state(request: Request) -> Response:
    """侧边栏要的全部内容，一次给完。"""
    service = service_of(request)
    profile = service.context.profile
    plan = service.context.plan
    notes = service.list_notes()

    return JSONResponse(
        {
            "goal": profile.read_section("目标"),
            "level": profile.read_section("当前水平"),
            "plan_now": plan.read_section("现在的位置"),
            "plan_next": plan.read_section("下一步"),
            "note_count": len(notes),
            "notes": [
                {"title": info.title, "date": info.date, "tags": list(info.tags)}
                for info in notes[:SIDEBAR_NOTES]
            ],
        }
    )


async def ask(request: Request) -> Response:
    service = service_of(request)
    payload = await request.json()
    text = str(payload.get("text", "")).strip()
    if not text:
        return JSONResponse({"error": "问题是空的"}, status_code=400)

    queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    def on_event(event: AskEvent) -> None:
        """service 的回调是同步的，直接往队列里塞——它和请求在同一个事件循环里。"""
        if isinstance(event, TextDelta):
            queue.put_nowait(("delta", {"text": event.text}))
        elif isinstance(event, ToolCalled):
            queue.put_nowait(
                ("tool", {"name": event.call.name, "arguments": event.call.arguments})
            )

    async def pump() -> None:
        """把一次提问跑完，结束时往队列里放一个终止帧。"""
        try:
            reply = await service.ask(text, on_event=on_event)
            queue.put_nowait(("done", {"text": reply.text}))
        except Exception as exc:
            # 网络抖动、限流、超时都走这里。错误也是流里的一帧，前端照着显示就行。
            queue.put_nowait(
                ("error", {"message": f"{type(exc).__name__}: {exc}"})
            )

    async def frames():
        task = asyncio.create_task(pump())
        try:
            while True:
                name, data = await queue.get()
                yield sse(name, data)
                if name in {"done", "error"}:
                    break
        finally:
            # 客户端提前断开时，别让后台任务继续跑
            if not task.done():
                task.cancel()

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def notes(request: Request) -> Response:
    """笔记列表。`q` 是关键词，`tag` 是标签筛选。"""
    service = service_of(request)
    query = request.query_params.get("q", "").strip()
    tag = request.query_params.get("tag", "").strip()

    all_notes = service.list_notes()
    tags = sorted({one for info in all_notes for one in info.tags})
    # query 为空时传 "*"：那是 notes.search 里"列出全部"的约定
    entries = service.search_notes(query or "*", limit=NOTES_LIMIT, tag=tag)

    return JSONResponse(
        {
            "tags": tags,
            "notes": [
                {
                    "name": info.path.name,
                    "title": info.title,
                    "date": info.date,
                    "tags": list(info.tags),
                    "snippet": snippet,
                }
                for info, snippet in entries
            ],
        }
    )


async def note(request: Request) -> Response:
    """单篇笔记的全文。用查询参数而不是路径参数——中文文件名放路径里容易踩编码。"""
    service = service_of(request)
    name = request.query_params.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "缺少 name 参数"}, status_code=400)

    try:
        info = service.read_note(name)
    except NoteError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)

    return JSONResponse(
        {
            "name": info.path.name,
            "title": info.title,
            "date": info.date,
            "tags": list(info.tags),
            "body": info.body,
            "path": str(info.path),
        }
    )


async def summary(request: Request) -> Response:
    service = service_of(request)
    try:
        result = await service.summarize()
    except SummarizeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse(
            {"error": f"{type(exc).__name__}: {exc}"}, status_code=500
        )
    return JSONResponse({"markdown": result.markdown, "path": str(result.note.path)})


def create_app(service: "DivanaService") -> Starlette:
    """把 service 挂到 app 上。单独抽成函数是为了能用假 service 测这一层。"""
    app = Starlette(
        routes=[
            Route("/", index),
            Mount("/static", app=StaticFiles(directory=WEB_DIR), name="static"),
            Route("/api/state", state),
            Route("/api/notes", notes),
            Route("/api/note", note),
            Route("/api/ask", ask, methods=["POST"]),
            Route("/api/summary", summary, methods=["POST"]),
        ]
    )
    app.state.service = service
    return app


def main() -> None:
    # 这几个都放函数里：只有真要跑网页时才需要 agent 栈，
    # 而这个模块本身（路由和 SSE 那部分）不该被它绑住。
    import uvicorn

    from .service import DivanaService
    from .session import DEFAULT_SESSION_ID

    load_dotenv()
    settings = Settings.from_env()
    setup_agents_sdk(settings)

    service = DivanaService(settings, DEFAULT_SESSION_ID)
    app = create_app(service)

    print(f"Divana 网页版： http://{HOST}:{PORT}")
    print(f"  会话: {service.session_id}　画像: {service.context.profile.path}")
    print("  Ctrl+C 停止\n")

    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    finally:
        service.close()


if __name__ == "__main__":
    main()
