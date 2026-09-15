"""读取外部材料的离线测试：只测解析和防护，不发任何网络请求。

用法：python -m unittest tests.test_fetch -v
"""

from __future__ import annotations

import time
import unittest
from unittest import mock

from divana.fetch import (
    MAX_TEXT_CHARS,
    Document,
    FetchError,
    build_document,
    check_url,
    describe_http_error,
    html_to_text,
    http_get,
    normalize_arxiv_id,
    normalize_repo,
    parse_arxiv_feed,
    parse_github_payload,
)

GITHUB_FIXTURE = {
    "full_name": "openai/openai-agents-python",
    "description": "A lightweight, powerful framework for multi-agent workflows.",
    "language": "Python",
    "stargazers_count": 12345,
    "forks_count": 678,
    "open_issues_count": 90,
    "topics": ["agents", "llm", "framework"],
    "license": {"spdx_id": "MIT"},
    "pushed_at": "2026-09-01T12:00:00Z",
    "archived": False,
    "homepage": "https://openai.github.io/openai-agents-python/",
    "html_url": "https://github.com/openai/openai-agents-python",
}

ARXIV_FIXTURE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <published>2017-06-12T17:57:34Z</published>
    <title>Attention Is All You
      Need</title>
    <summary>  The dominant sequence transduction models are based on
      complex recurrent or convolutional neural networks.
    </summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <arxiv:comment>Accepted at NeurIPS 2017</arxiv:comment>
    <category term="cs.CL" />
    <category term="cs.LG" />
  </entry>
</feed>
"""


class HtmlToTextTest(unittest.TestCase):
    def test_keeps_visible_text(self) -> None:
        self.assertEqual(html_to_text("<p>你好</p>"), "你好")

    def test_drops_script_and_style(self) -> None:
        html = "<p>正文</p><script>var a = 1;</script><style>p{color:red}</style>"
        self.assertEqual(html_to_text(html), "正文")

    def test_block_tags_become_line_breaks(self) -> None:
        self.assertEqual(html_to_text("<p>一</p><p>二</p>"), "一\n二")

    def test_collapses_blank_lines(self) -> None:
        self.assertEqual(html_to_text("<div>一</div>\n\n\n<div>二</div>"), "一\n二")

    def test_decodes_entities(self) -> None:
        self.assertIn("A & B", html_to_text("<p>A &amp; B</p>"))

    def test_empty_input(self) -> None:
        self.assertEqual(html_to_text(""), "")

    def test_nested_script_is_fully_skipped(self) -> None:
        html = "<script><script>var x = 1;</script></script><p>正文</p>"
        self.assertEqual(html_to_text(html), "正文")


class CheckUrlTest(unittest.TestCase):
    def test_accepts_http_and_https(self) -> None:
        self.assertEqual(check_url("https://example.com"), "https://example.com")
        self.assertEqual(check_url(" http://example.com "), "http://example.com")

    def test_rejects_local_files(self) -> None:
        """这是最重要的一条：不能让它去读本地文件。"""
        for bad in ("file:///C:/Users/HS/.env", "file:///etc/passwd"):
            with self.assertRaises(FetchError):
                check_url(bad)

    def test_rejects_other_schemes(self) -> None:
        for bad in ("ftp://example.com", "javascript:alert(1)", "data:text/html,x"):
            with self.assertRaises(FetchError):
                check_url(bad)

    def test_rejects_url_without_host(self) -> None:
        with self.assertRaises(FetchError):
            check_url("https:///missing-host")


class NormalizeRepoTest(unittest.TestCase):
    def test_bare_slug(self) -> None:
        self.assertEqual(normalize_repo("openai/openai-agents-python"), "openai/openai-agents-python")

    def test_full_url(self) -> None:
        self.assertEqual(
            normalize_repo("https://github.com/openai/openai-agents-python"),
            "openai/openai-agents-python",
        )

    def test_url_with_tree_and_anchor(self) -> None:
        self.assertEqual(
            normalize_repo("https://github.com/openai/openai-agents-python/tree/main#readme"),
            "openai/openai-agents-python",
        )

    def test_url_with_git_suffix(self) -> None:
        self.assertEqual(normalize_repo("https://github.com/foo/bar.git"), "foo/bar")

    def test_ssh_style(self) -> None:
        self.assertEqual(normalize_repo("git@github.com:foo/bar.git"), "foo/bar")

    def test_rejects_garbage(self) -> None:
        with self.assertRaises(FetchError):
            normalize_repo("这不是一个仓库")


class NormalizeArxivIdTest(unittest.TestCase):
    def test_bare_id(self) -> None:
        self.assertEqual(normalize_arxiv_id("1706.03762"), "1706.03762")

    def test_prefixed_id(self) -> None:
        self.assertEqual(normalize_arxiv_id("arXiv:2401.12345"), "2401.12345")

    def test_url(self) -> None:
        self.assertEqual(
            normalize_arxiv_id("https://arxiv.org/abs/1706.03762v7"),
            "1706.03762v7",
        )

    def test_pdf_url(self) -> None:
        self.assertEqual(
            normalize_arxiv_id("https://arxiv.org/pdf/2401.12345.pdf"), "2401.12345"
        )

    def test_rejects_garbage(self) -> None:
        with self.assertRaises(FetchError):
            normalize_arxiv_id("Attention Is All You Need")


class ParseGithubPayloadTest(unittest.TestCase):
    def doc(self, **overrides: object) -> Document:
        payload = {**GITHUB_FIXTURE, **overrides}
        return parse_github_payload(payload, "# 标题\n\n这是 README。")

    def test_keeps_key_metadata(self) -> None:
        text = self.doc().text
        self.assertIn("openai/openai-agents-python", text)
        self.assertIn("A lightweight, powerful framework", text)
        self.assertIn("Python", text)
        self.assertIn("12345", text)
        self.assertIn("agents、llm、framework", text)
        self.assertIn("MIT", text)
        self.assertIn("2026-09-01", text)

    def test_includes_readme(self) -> None:
        self.assertIn("这是 README。", self.doc().text)

    def test_warns_about_archived_repo(self) -> None:
        self.assertIn("已经归档", self.doc(archived=True).text)

    def test_survives_missing_fields(self) -> None:
        document = parse_github_payload({})
        self.assertIn("（作者没有写描述）", document.text)
        self.assertIn("未知", document.text)

    def test_source_names_the_repo(self) -> None:
        self.assertEqual(self.doc().source, "GitHub: openai/openai-agents-python")


class ParseArxivFeedTest(unittest.TestCase):
    def test_parses_metadata(self) -> None:
        document = parse_arxiv_feed(ARXIV_FIXTURE)
        self.assertEqual(document.title, "Attention Is All You Need")
        self.assertIn("Ashish Vaswani", document.text)
        self.assertIn("Noam Shazeer", document.text)
        self.assertIn("2017-06-12", document.text)
        self.assertIn("cs.CL", document.text)
        self.assertIn("Accepted at NeurIPS 2017", document.text)

    def test_collapses_newlines_in_abstract(self) -> None:
        text = parse_arxiv_feed(ARXIV_FIXTURE).text
        self.assertIn("The dominant sequence transduction models are based on complex", text)

    def test_source_uses_the_arxiv_id(self) -> None:
        self.assertEqual(parse_arxiv_feed(ARXIV_FIXTURE).source, "arXiv: 1706.03762v7")

    def test_broken_xml_is_reported(self) -> None:
        with self.assertRaises(FetchError):
            parse_arxiv_feed(b"<feed><entry>")

    def test_empty_feed_is_reported(self) -> None:
        empty = (
            b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        )
        with self.assertRaises(FetchError):
            parse_arxiv_feed(empty)


class BuildDocumentTest(unittest.TestCase):
    def test_short_text_is_untouched(self) -> None:
        document = build_document("src", "标题", "  短文本  ")
        self.assertEqual(document.text, "短文本")
        self.assertFalse(document.truncated)

    def test_long_text_is_truncated(self) -> None:
        document = build_document("src", "标题", "字" * (MAX_TEXT_CHARS + 500))
        self.assertEqual(len(document.text), MAX_TEXT_CHARS)
        self.assertTrue(document.truncated)

    def test_render_shows_source_and_title(self) -> None:
        text = build_document("https://x.example", "标题甲", "正文").render()
        self.assertIn("来源：https://x.example", text)
        self.assertIn("标题：标题甲", text)
        self.assertIn("正文", text)

    def test_render_marks_truncation(self) -> None:
        document = build_document("src", "标题", "字" * (MAX_TEXT_CHARS + 1))
        self.assertIn("被截断", document.render())


class DescribeHttpErrorTest(unittest.TestCase):
    """403 的提示必须能指导下一步动作——这是真踩过的坑（共享 IP 配额被用光）。"""

    url = "https://api.github.com/repos/someone/somerepo"

    def test_rate_limited_says_how_long_and_how_to_fix(self) -> None:
        reset = int(time.time()) + 600  # 10 分钟后恢复
        message = describe_http_error(
            self.url,
            403,
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)},
        )
        self.assertIn("配额", message)
        self.assertIn("DIVANA_GITHUB_TOKEN", message)
        self.assertIn("10 分钟", message)

    def test_429_is_treated_like_rate_limiting(self) -> None:
        message = describe_http_error(self.url, 429, {"x-ratelimit-remaining": "0"})
        self.assertIn("429", message)

    def test_plain_403_points_at_private_repo(self) -> None:
        self.assertIn("私有仓库", describe_http_error(self.url, 403, {}))

    def test_404_explains_private_repos_look_the_same(self) -> None:
        message = describe_http_error(self.url, 404, {})
        self.assertIn("404", message)
        self.assertIn("私有仓库", message)

    def test_survives_missing_headers(self) -> None:
        self.assertIn("403", describe_http_error(self.url, 403, None))

    def test_survives_unparsable_reset(self) -> None:
        message = describe_http_error(
            self.url, 403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "abc"}
        )
        self.assertIn("等一会儿", message)


class HttpGetTest(unittest.TestCase):
    """不发真请求，只看构造出来的 request 对不对。"""

    def call(self, **kwargs: object) -> object:
        with mock.patch("divana.fetch.urllib.request.urlopen") as fake:
            fake.return_value.__enter__.return_value.read.return_value = b"ok"
            http_get("https://api.github.com/repos/a/b", **kwargs)
            return fake.call_args[0][0]

    def test_sends_bearer_token_when_given(self) -> None:
        request = self.call(token="ghp_test")
        self.assertEqual(request.headers.get("Authorization"), "Bearer ghp_test")

    def test_no_authorization_header_without_token(self) -> None:
        request = self.call()
        self.assertIsNone(request.headers.get("Authorization"))

    def test_always_sends_user_agent(self) -> None:
        self.assertIn("Divana", self.call().headers.get("User-agent", ""))

    def test_accept_header_is_passed_through(self) -> None:
        request = self.call(accept="application/vnd.github.raw")
        self.assertEqual(request.headers.get("Accept"), "application/vnd.github.raw")


if __name__ == "__main__":
    unittest.main()
