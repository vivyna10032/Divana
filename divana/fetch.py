"""读取外部材料：网页、GitHub 仓库、arXiv 论文。

"总结一个项目 / 一篇论文"的前提是**能把它读进来**。这个模块只负责读取和清洗，
不负责总结——总结交给模型，它本来就擅长；我们要保证的是材料干净、长度可控。

只用标准库：urllib 发请求、html.parser 去标签、xml.etree 解析 arXiv 的 Atom。

两个已知边界（都是刻意的取舍，不是没做完）：

- JS 渲染的页面（React/Vue 那种）抓下来几乎是空的，所以 GitHub 走官方 API
  而不是抓网页——这就是 read_github_repo 单独存在的原因。
- 正文提取是"够用版"：去掉脚本和样式、按块级标签换行，不做到"正文识别"。
  对技术文档和博客足够，对排版复杂的新闻站会带一些杂音。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser

TIMEOUT_SECONDS = 25

# 一份材料最多给模型多少字符。抓回来的东西也是上下文，不设上限的话
# 一个长 README 就能把这一轮撑爆。
MAX_TEXT_CHARS = 12000

USER_AGENT = "Divana/0.4 (personal learning companion)"
GITHUB_API = "https://api.github.com"
ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_HTML = "https://arxiv.org/html"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"


class FetchError(RuntimeError):
    """读取外部材料时的可预期错误：地址不对、连不上、返回看不懂。"""


@dataclass(frozen=True)
class Document:
    """一份读进来的材料。三个工具都返回这个形状，模型看到的东西就统一了。"""

    source: str
    title: str
    text: str
    truncated: bool = False

    def render(self) -> str:
        head = f"来源：{self.source}\n标题：{self.title}\n\n"
        body = self.text
        if self.truncated:
            body += "\n\n…（内容太长，后面被截断了）"
        return head + body


# ---------------------------------------------------------------- HTTP


def check_url(url: str) -> str:
    """只允许 http/https。

    这条挡的不是"打错字"，是 `file:///C:/Users/你/.env` 这种输入。模型一旦被
    网页内容带着走（prompt injection），最危险的就是让它去读本地文件。

    注意：这里没拦内网地址（127.0.0.1、192.168.x.x）。对个人本地工具来说，
    "能读自己起的服务"是有用的；如果你以后把它部署成多人用的服务，就必须补上
    这一段（这类攻击叫 SSRF）。
    """
    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise FetchError(
            f"只支持 http/https 地址，收到的是「{parsed.scheme or '空'}」。本地文件读不了。"
        )
    if not parsed.netloc:
        raise FetchError("地址里没有域名，检查一下是不是漏了 https://")
    return url


def describe_http_error(url: str, code: int, headers: object) -> str:
    """把 HTTP 错误翻译成能指导下一步动作的话。

    这里最值得说清的是 GitHub 的 403：匿名调 API 每小时只有 60 次，而且是
    **按出口 IP 算的**。所以如果你走代理，那个 IP 是共享的，很容易被别人用光——
    光看一句"被限流了"根本不知道该等还是该换办法。
    """
    get = getattr(headers, "get", None)
    remaining = get("x-ratelimit-remaining") if callable(get) else None
    reset = get("x-ratelimit-reset") if callable(get) else None

    if code in (403, 429) and remaining == "0" and reset:
        try:
            minutes = max(1, (int(reset) - int(time.time())) // 60)
        except (TypeError, ValueError):
            minutes = None
        wait = f"大约 {minutes} 分钟后恢复" if minutes else "等一会儿再试"
        return (
            f"GitHub 的匿名配额用完了（每小时 60 次，{wait}）：{url}。"
            "这个配额按你的出口 IP 算——走代理的话那个 IP 是共享的，容易被别人用光。"
            "在 .env 里填 DIVANA_GITHUB_TOKEN 可以把上限提到 5000 次/小时，"
            "而且按 token 算，不受共享 IP 影响。"
        )
    if code in (403, 429):
        return (
            f"被拒绝（{code}）：{url}。如果这是私有仓库，需要配 DIVANA_GITHUB_TOKEN。"
        )
    if code == 404:
        return f"地址不存在（404）：{url}。私有仓库在匿名状态下也会返回 404。"
    return f"请求失败（{code}）：{url}"


def http_get(url: str, *, accept: str | None = None, token: str = "") -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise FetchError(describe_http_error(url, exc.code, exc.headers)) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"连不上：{url}（{exc}）") from exc


# ---------------------------------------------------------------- 网页正文


# 这些标签里的东西是代码和装饰，不是给人读的正文。
# nav/aside/form/button 这几个是拿真实的 arXiv 页面测出来的：不滤掉的话
# 正文前面会先来一段"返回上一页 / 下载 PDF / 提交"的导航噪音。
_SKIP_TAGS = {
    "script", "style", "noscript", "svg", "head", "template",
    "nav", "aside", "form", "button", "select", "option", "iframe", "dialog",
}
# 这些标签天然是分段的，遇到就换行，免得整页挤成一行
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "td", "section", "article", "header", "footer",
    "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol",
}


class _TextExtractor(HTMLParser):
    """把 HTML 拆成纯文本。够用就行，别指望它认得"正文区"在哪。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def html_to_text(html: str) -> str:
    """HTML -> 纯文本：去掉脚本样式，按块级标签换行，压掉多余空行。"""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()

    kept = [line.strip() for line in parser.text().splitlines() if line.strip()]
    return "\n".join(kept)


# ---------------------------------------------------------------- 组装材料


def build_document(source: str, title: str, body: str) -> Document:
    """统一在这里限长——三个工具都走这条路，就不会有人漏掉。"""
    body = body.strip()
    if len(body) <= MAX_TEXT_CHARS:
        return Document(source=source, title=title, text=body)
    return Document(source=source, title=title, text=body[:MAX_TEXT_CHARS], truncated=True)


# ---------------------------------------------------------------- GitHub


_REPO_IN_URL = re.compile(r"github\.com[/:]([^/\s]+)/([^/#?\s]+)", re.IGNORECASE)
_SLUG = re.compile(r"^([^/\s]+)/([^/\s]+)$")


def normalize_repo(repo: str) -> str:
    """把各种写法归一成 owner/repo。

    支持 `owner/repo`、`https://github.com/owner/repo`、带 `/tree/main`、
    带 `#readme`、带 `.git` 的写法。
    """
    repo = repo.strip()
    match = _REPO_IN_URL.search(repo) or _SLUG.match(repo)
    if not match:
        raise FetchError(
            f"看不懂这个仓库地址：{repo}。要 owner/repo，或者 github.com/owner/repo"
        )
    owner, name = match.group(1), match.group(2)
    return f"{owner}/{name.removesuffix('.git')}"


def parse_github_payload(meta: dict, readme: str = "") -> Document:
    """把 GitHub API 的返回拼成一份材料（纯函数，不联网，方便测试）。"""
    slug = meta.get("full_name") or "未知仓库"
    lines = [
        f"仓库：{slug}",
        f"描述：{meta.get('description') or '（作者没有写描述）'}",
        f"主语言：{meta.get('language') or '未知'}",
        (
            f"Star：{meta.get('stargazers_count', 0)}　"
            f"Fork：{meta.get('forks_count', 0)}　"
            f"开放 issue：{meta.get('open_issues_count', 0)}"
        ),
    ]
    if topics := meta.get("topics"):
        lines.append("Topics：" + "、".join(topics))
    if spdx := (meta.get("license") or {}).get("spdx_id"):
        lines.append(f"License：{spdx}")
    if pushed := meta.get("pushed_at"):
        lines.append(f"最近推送：{str(pushed)[:10]}")
    if meta.get("archived"):
        lines.append("注意：这个仓库已经归档，不再维护了")
    if homepage := meta.get("homepage"):
        lines.append(f"主页：{homepage}")
    lines.append(f"地址：{meta.get('html_url', '')}")
    if readme.strip():
        lines.extend(["", "--- README ---", "", readme.strip()])
    return build_document(source=f"GitHub: {slug}", title=slug, body="\n".join(lines))


# README 的常见文件名。用 raw 域名去试，试中就不用花 API 配额。
_README_NAMES = ("README.md", "readme.md", "README.rst", "README.txt", "README")
_RAW_GITHUB = "https://raw.githubusercontent.com"


def _readme_via_raw(slug: str, branch: str, token: str) -> str:
    """从 raw.githubusercontent.com 拿 README——**这个域名不计入 API 配额**。"""
    for name in _README_NAMES:
        try:
            raw = http_get(f"{_RAW_GITHUB}/{slug}/{branch}/{name}", token=token)
        except FetchError:
            continue
        return raw.decode("utf-8", errors="replace")
    return ""


def _readme_via_api(slug: str, token: str) -> str:
    """兜底：文件名不标准时（比如 README.zh-CN.md），只有 API 端点能找到。"""
    try:
        raw = http_get(
            f"{GITHUB_API}/repos/{slug}/readme",
            accept="application/vnd.github.raw",
            token=token,
        )
    except FetchError:
        return ""
    return raw.decode("utf-8", errors="replace")


def read_github_repo(repo: str, *, token: str = "") -> Document:
    """读一个 GitHub 仓库：元信息 + README。

    走 API 而不是抓网页，因为仓库页面是 JS 渲染的，直接抓 HTML 只能拿到一堆脚本。

    README **优先走 raw 域名**：那里不计入 API 配额，能把每次读仓库的 API 调用
    从 2 次压到 1 次。匿名配额只有 60 次/小时且按出口 IP 算，这一半省下来很值。
    """
    slug = normalize_repo(repo)
    try:
        meta = json.loads(http_get(f"{GITHUB_API}/repos/{slug}", token=token))
    except json.JSONDecodeError as exc:
        raise FetchError(f"GitHub 的返回看不懂：{exc}") from exc
    if not isinstance(meta, dict):
        raise FetchError(f"GitHub 的返回不是仓库信息：{slug}")

    branch = str(meta.get("default_branch") or "main")
    readme = _readme_via_raw(slug, branch, token) or _readme_via_api(slug, token)
    return parse_github_payload(meta, readme)


# ---------------------------------------------------------------- arXiv


def _entry_text(entry: ET.Element, tag: str) -> str:
    """取一个子标签的文本并压掉换行（arXiv 的标题和摘要里换行很多）。"""
    return " ".join((entry.findtext(tag) or "").split())


def parse_arxiv_feed(payload: bytes) -> Document:
    """解析 arXiv 的 Atom 返回（纯函数，不联网，方便测试）。"""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise FetchError(f"arXiv 的返回看不懂：{exc}") from exc

    entry = root.find(f"{ATOM}entry")
    if entry is None:
        raise FetchError("arXiv 没返回任何条目，检查一下论文编号")

    title = _entry_text(entry, f"{ATOM}title")
    abstract = _entry_text(entry, f"{ATOM}summary")
    published = _entry_text(entry, f"{ATOM}published")[:10]
    link = _entry_text(entry, f"{ATOM}id")
    authors = [
        (author.findtext(f"{ATOM}name") or "").strip()
        for author in entry.findall(f"{ATOM}author")
    ]
    categories = [c.get("term", "") for c in entry.findall(f"{ATOM}category")]
    comment = _entry_text(entry, f"{ARXIV_NS}comment")

    lines = [
        f"标题：{title}",
        f"作者：{'、'.join(filter(None, authors)) or '未知'}",
        f"提交日期：{published}",
        f"分类：{'、'.join(filter(None, categories)) or '未知'}",
    ]
    if comment:
        lines.append(f"备注：{comment}")
    lines.extend(["", "摘要：", abstract, "", f"链接：{link}"])
    return build_document(
        source=f"arXiv: {link.rsplit('/', 1)[-1]}", title=title, body="\n".join(lines)
    )


_ARXIV_ID = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


def normalize_arxiv_id(identifier: str) -> str:
    """从各种写法里取出论文编号：2401.12345、arXiv:2401.12345、abs/pdf 链接。

    版本号（`v7` 这种）保留——用户贴的链接可能就指向某个特定版本。
    """
    match = _ARXIV_ID.search(identifier)
    if not match:
        raise FetchError(
            f"看不懂这个论文编号：{identifier}。要 2401.12345 这样的形式，"
            "或者 arxiv.org/abs/2401.12345 链接"
        )
    return match.group(0)


def read_arxiv(identifier: str, *, with_full_text: bool = True) -> Document:
    """读一篇 arXiv 论文：元信息 + 摘要（能拿到正文就一起带上）。

    摘要足够做一份"这篇在讲什么"的速览。想要更深的方法细节就得有正文——
    arXiv 近几年给不少论文出了 HTML 版，能抓就抓，抓不到就只用摘要。
    """
    paper_id = normalize_arxiv_id(identifier)
    document = parse_arxiv_feed(http_get(f"{ARXIV_API}?id_list={paper_id}"))

    if not with_full_text:
        return document

    try:
        html = http_get(f"{ARXIV_HTML}/{paper_id}").decode("utf-8", errors="replace")
    except FetchError:
        # 老论文没有 HTML 版，很正常
        return document

    full_text = html_to_text(html)
    if len(full_text) < 500:
        # 抓到的基本是"这篇没有 HTML 版"的提示页，没用
        return document

    return build_document(
        source=document.source,
        title=document.title,
        body=f"{document.text}\n\n--- 正文（HTML 版）---\n\n{full_text}",
    )


def read_url(url: str) -> Document:
    """读一个普通网页，抽成纯文本。"""
    url = check_url(url)
    html = http_get(url).decode("utf-8", errors="replace")
    text = html_to_text(html)
    if not text.strip():
        raise FetchError(
            "这个页面抓下来是空的。多半是 JS 渲染的页面——换成它的 API 或 "
            "README 试试，或者直接告诉我你想知道什么。"
        )
    return build_document(source=url, title=url, body=text)
