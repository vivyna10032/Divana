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
from contextlib import asynccontextmanager, suppress
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
from .digest import DigestError, run_digest
from .markdown_store import MarkdownStoreError
from .notes import NoteError
from .review import DEFAULT_DAYS, ReviewError, run_review
from .scheduler import digest_status, review_status, scheduler_loop
from .session import new_session_id

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


async def plan(request: Request) -> Response:
    """计划页要的全部内容。进度是从 markdown 的 checkbox 里真数出来的，不是估的。"""
    service = service_of(request)
    store = service.context.plan
    progress = service.plan_progress()

    return JSONResponse(
        {
            "now": store.read_section("现在的位置"),
            "next": store.read_section("下一步"),
            "retro": store.read_section("复盘记录"),
            "done": progress.done,
            "total": progress.total,
            "percent": progress.percent,
            # 复盘的状态：上次什么时候、下次什么时候、上次失败了吗
            "review": review_status(),
            "stages": [
                {
                    "name": stage.name,
                    "milestones": [
                        {"text": m.text, "done": m.done} for m in stage.milestones
                    ],
                }
                for stage in progress.stages
            ],
            # 复盘记录按条给（新的在前），界面上每条都能单独删
            "reviews": [
                {"title": title, "body": body} for title, body in service.list_reviews()
            ],
        }
    )


async def delete(request: Request) -> Response:
    """删除入口。**全都进回收站**，不是真删——见 divana/trash.py 的说明。

    四种东西走同一个接口，参数是 (kind, id)：
    session（会话）/ note（笔记）/ review（一次复盘）/ stage（一个阶段）。
    """
    service = service_of(request)
    payload = await request.json()
    kind = str(payload.get("kind", "")).strip()
    target = str(payload.get("id", "")).strip()
    if not kind or not target:
        return JSONResponse({"error": "需要 kind 和 id"}, status_code=400)

    try:
        if kind == "session":
            saved = service.delete_session(target)
        elif kind == "note":
            saved = service.delete_note(target)
        elif kind == "review":
            saved = service.delete_review(target)
        elif kind == "stage":
            saved = service.delete_stage(target)
        else:
            return JSONResponse({"error": f"不认识要删什么：{kind}"}, status_code=400)
    except (NoteError, MarkdownStoreError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        return JSONResponse(
            {"error": f"{type(exc).__name__}: {exc}"}, status_code=500
        )

    return JSONResponse({"saved": str(saved)})


async def profile(request: Request) -> Response:
    """画像页要的全部内容：五节正文 + 文件信息 + 一点交叉数据。"""
    service = service_of(request)
    store = service.context.profile
    progress = service.plan_progress()

    return JSONResponse(
        {
            "goal": store.read_section("目标"),
            "level": store.read_section("当前水平"),
            "known": store.read_section("已掌握"),
            "weak": store.read_section("薄弱点"),
            "habits": store.read_section("学习习惯"),
            "path": str(store.path),
            "updated_at": store.updated_at(),
            "note_count": len(service.list_notes()),
            "done": progress.done,
            "total": progress.total,
            "percent": progress.percent,
        }
    )


async def review(request: Request) -> Response:
    """手动跑一次复盘。以后定时任务调的是同一个函数，所以手动跑通了，定时只是换个触发点。"""
    service = service_of(request)
    payload = await request.json() if await request.body() else {}

    try:
        days = int(payload.get("days", DEFAULT_DAYS))
    except (TypeError, ValueError):
        return JSONResponse({"error": "days 得是整数"}, status_code=400)

    try:
        result = await run_review(service.settings, service, days=days)
    except ReviewError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse(
            {"error": f"{type(exc).__name__}: {exc}"}, status_code=500
        )

    return JSONResponse(
        {
            "markdown": result.markdown,
            "days": result.days,
            "message_count": result.message_count,
        }
    )


async def digests(request: Request) -> Response:
    """早报列表 + 状态。"""
    service = service_of(request)
    return JSONResponse(
        {
            "digests": [
                {"date": info.date, "title": info.title, "name": info.path.name}
                for info in service.list_digests()
            ],
            "status": digest_status(),
        }
    )


async def digest(request: Request) -> Response:
    """读一期早报的正文。"""
    service = service_of(request)
    name = request.query_params.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "缺少 name"}, status_code=400)
    try:
        info, body = service.read_digest(name)
    except DigestError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    return JSONResponse({"date": info.date, "title": info.title, "body": body})


async def make_digest(request: Request) -> Response:
    """手动出一期早报。定时任务调的是同一个函数。"""
    service = service_of(request)
    try:
        result = await run_digest(service.settings, service)
    except DigestError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse(
            {"error": f"{type(exc).__name__}: {exc}"}, status_code=500
        )
    return JSONResponse({"markdown": result.markdown, "path": str(result.path)})


async def sessions(request: Request) -> Response:
    """会话列表。current 标出当前正在聊的那个。"""
    service = service_of(request)
    return JSONResponse(
        {
            "current": service.session_id,
            "sessions": [
                {
                    "id": info.session_id,
                    "title": info.title,
                    "updated_at": info.updated_at,
                    "message_count": info.message_count,
                }
                for info in service.list_sessions()
            ],
        }
    )


async def new_session(request: Request) -> Response:
    """开一段新对话。"""
    service = service_of(request)
    session_id = new_session_id()
    service.switch_session(session_id)
    return JSONResponse({"id": session_id})


async def switch_session(request: Request) -> Response:
    payload = await request.json()
    session_id = str(payload.get("session_id", "")).strip()
    if not session_id:
        return JSONResponse({"error": "缺少 session_id"}, status_code=400)
    service_of(request).switch_session(session_id)
    return JSONResponse({"id": session_id})


async def history(request: Request) -> Response:
    """当前会话的对话历史，给界面渲染。"""
    service = service_of(request)
    messages = await service.history()
    return JSONResponse(
        {"messages": [{"role": role, "text": text} for role, text in messages]}
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


def create_app(
    service: "DivanaService", *, start_scheduler: bool = True
) -> Starlette:
    """把 service 挂到 app 上。单独抽成函数是为了能用假 service 测这一层。

    `start_scheduler=False` 给测试用：后台任务会真读数据库、真写状态文件，
    测试里不该发生这些。
    """

    @asynccontextmanager
    async def lifespan(app: Starlette):
        task = asyncio.create_task(scheduler_loop(service)) if start_scheduler else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = Starlette(
        routes=[
            Route("/", index),
            Mount("/static", app=StaticFiles(directory=WEB_DIR), name="static"),
            Route("/api/state", state),
            Route("/api/notes", notes),
            Route("/api/note", note),
            Route("/api/plan", plan),
            Route("/api/profile", profile),
            Route("/api/review", review, methods=["POST"]),
            Route("/api/delete", delete, methods=["POST"]),
            Route("/api/digests", digests),
            Route("/api/digest", digest),
            Route("/api/digest/new", make_digest, methods=["POST"]),
            Route("/api/sessions", sessions),
            Route("/api/sessions/new", new_session, methods=["POST"]),
            Route("/api/sessions/switch", switch_session, methods=["POST"]),
            Route("/api/history", history),
            Route("/api/ask", ask, methods=["POST"]),
            Route("/api/summary", summary, methods=["POST"]),
        ],
        lifespan=lifespan,
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
