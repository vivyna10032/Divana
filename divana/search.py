"""联网搜索：把一个搜索 API 包成 agent 能调的工具。

为什么不用 SDK 自带的 WebSearchTool？因为它是 **hosted tool**——只支持 OpenAI
模型走 Responses API。我们走的是 DeepSeek 的 chat completions，SDK 在那条路径上
会直接抛 "Hosted tools are not supported with the ChatCompletions API"。
所以搜索得自己接：自己发 HTTP、自己解析、自己决定给模型看什么。

只用标准库的 urllib，不引入新依赖。换服务商就是加一段适配器。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

TIMEOUT_SECONDS = 20

# 单个结果摘要、以及整段结果的总长度上限。
# 工具的输出也是要进上下文的，不设限的话一次搜索就能吃掉好几千 token。
SNIPPET_CHARS = 500
TOTAL_CHARS = 3000


class SearchError(RuntimeError):
    """搜索相关的可预期错误：没配置、连不上、返回格式看不懂。"""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class Provider:
    """一个搜索服务商 = 怎么发请求 + 怎么解析返回。"""

    name: str
    build_request: Callable[[str, str, int], urllib.request.Request]
    parse: Callable[[bytes], list[SearchResult]]


def _tavily_request(key: str, query: str, limit: int) -> urllib.request.Request:
    body = json.dumps(
        {"query": query, "max_results": limit, "search_depth": "basic"}
    ).encode("utf-8")
    return urllib.request.Request(
        "https://api.tavily.com/search",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )


def _tavily_parse(payload: bytes) -> list[SearchResult]:
    data = json.loads(payload)
    return [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=item.get("content", ""),
        )
        for item in data.get("results", [])
    ]


def _brave_request(key: str, query: str, limit: int) -> urllib.request.Request:
    params = urllib.parse.urlencode({"q": query, "count": limit})
    return urllib.request.Request(
        f"https://api.search.brave.com/res/v1/web/search?{params}",
        headers={"Accept": "application/json", "X-Subscription-Token": key},
    )


def _brave_parse(payload: bytes) -> list[SearchResult]:
    data = json.loads(payload)
    return [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=item.get("description", ""),
        )
        for item in data.get("web", {}).get("results", [])
    ]


def _serper_request(key: str, query: str, limit: int) -> urllib.request.Request:
    body = json.dumps({"q": query, "num": limit}).encode("utf-8")
    return urllib.request.Request(
        "https://google.serper.dev/search",
        data=body,
        headers={"Content-Type": "application/json", "X-API-KEY": key},
        method="POST",
    )


def _serper_parse(payload: bytes) -> list[SearchResult]:
    data = json.loads(payload)
    return [
        SearchResult(
            title=item.get("title", ""),
            url=item.get("link", ""),
            snippet=item.get("snippet", ""),
        )
        for item in data.get("organic", [])
    ]


PROVIDERS: dict[str, Provider] = {
    "tavily": Provider("tavily", _tavily_request, _tavily_parse),
    "brave": Provider("brave", _brave_request, _brave_parse),
    "serper": Provider("serper", _serper_request, _serper_parse),
}


def render(results: list[SearchResult]) -> str:
    """把结果排成给模型看的文本，顺手把长度压住。"""
    if not results:
        return "没有搜到结果。可以换个说法或换更具体的关键词再试一次。"

    blocks: list[str] = []
    used = 0
    for index, item in enumerate(results, 1):
        snippet = " ".join(item.snippet.split())
        if len(snippet) > SNIPPET_CHARS:
            snippet = snippet[:SNIPPET_CHARS] + "…"
        block = f"[{index}] {item.title}\n    {item.url}\n    {snippet}"
        if used + len(block) > TOTAL_CHARS:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)


@dataclass(frozen=True)
class SearchClient:
    """按配置发一次搜索。没配 key 就不会联网，直接给一句人话。"""

    provider: str
    api_key: str
    max_results: int = 5

    @property
    def configured(self) -> bool:
        return bool(self.api_key) and self.provider in PROVIDERS

    def search(self, query: str) -> list[SearchResult]:
        query = query.strip()
        if not query:
            raise SearchError("搜索词不能为空")
        if not self.api_key:
            raise SearchError(
                "还没有配置搜索 key。告诉用户联网查证暂时不可用，"
                "并提醒他在 .env 里填 DIVANA_SEARCH_API_KEY。"
            )
        if self.provider not in PROVIDERS:
            known = "、".join(PROVIDERS)
            raise SearchError(
                f"不认识的搜索服务商「{self.provider}」，可选：{known}"
            )

        provider = PROVIDERS[self.provider]
        request = provider.build_request(self.api_key, query, self.max_results)

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            raise SearchError(f"搜索接口返回 {exc.code} {exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SearchError(f"连不上搜索接口：{exc}") from exc

        try:
            return provider.parse(payload)
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            raise SearchError(f"看不懂搜索接口的返回：{exc}") from exc
