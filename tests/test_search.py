"""搜索模块的离线测试：只测解析和拼装，不发任何网络请求。

用法：python -m unittest tests.test_search -v
"""

from __future__ import annotations

import json
import unittest

from divana.search import (
    PROVIDERS,
    SNIPPET_CHARS,
    SearchClient,
    SearchError,
    SearchResult,
    render,
)

TAVILY_PAYLOAD = {
    "results": [
        {
            "title": "DeepSeek API 文档",
            "url": "https://api-docs.deepseek.com/",
            "content": "支持 function calling。",
        }
    ]
}

BRAVE_PAYLOAD = {
    "web": {
        "results": [
            {
                "title": "OpenAI Agents SDK",
                "url": "https://openai.github.io/openai-agents-python/",
                "description": "A lightweight framework for multi-agent workflows.",
            }
        ]
    }
}

SERPER_PAYLOAD = {
    "organic": [
        {
            "title": "SQLite WAL",
            "link": "https://sqlite.org/wal.html",
            "snippet": "Write-Ahead Logging.",
        }
    ]
}


class ParseTest(unittest.TestCase):
    def assert_parsed(self, provider: str, payload: dict, expected_url: str) -> None:
        results = PROVIDERS[provider].parse(json.dumps(payload).encode("utf-8"))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, expected_url)

    def test_tavily(self) -> None:
        self.assert_parsed("tavily", TAVILY_PAYLOAD, "https://api-docs.deepseek.com/")

    def test_brave(self) -> None:
        self.assert_parsed(
            "brave", BRAVE_PAYLOAD, "https://openai.github.io/openai-agents-python/"
        )

    def test_serper(self) -> None:
        self.assert_parsed("serper", SERPER_PAYLOAD, "https://sqlite.org/wal.html")

    def test_empty_payload_is_not_an_error(self) -> None:
        self.assertEqual(PROVIDERS["tavily"].parse(b"{}"), [])


class RenderTest(unittest.TestCase):
    def test_numbers_results_and_keeps_urls(self) -> None:
        text = render(
            [
                SearchResult("标题一", "https://a.example", "摘要一"),
                SearchResult("标题二", "https://b.example", "摘要二"),
            ]
        )

        self.assertIn("[1] 标题一", text)
        self.assertIn("https://a.example", text)
        self.assertIn("[2] 标题二", text)

    def test_truncates_long_snippets(self) -> None:
        text = render([SearchResult("标题", "https://a.example", "很" * 2000)])
        self.assertLess(len(text), 2000)
        self.assertIn("…", text)

    def test_empty_results_get_a_hint(self) -> None:
        self.assertIn("没有搜到结果", render([]))

    def test_snippet_limit_is_respected(self) -> None:
        text = render([SearchResult("标题", "https://a.example", "字" * (SNIPPET_CHARS * 2))])
        self.assertLess(len(text), SNIPPET_CHARS * 2)


class SearchClientTest(unittest.TestCase):
    def test_not_configured_without_key(self) -> None:
        self.assertFalse(SearchClient("tavily", "").configured)

    def test_configured_with_key_and_known_provider(self) -> None:
        self.assertTrue(SearchClient("tavily", "sk-test").configured)

    def test_unknown_provider_is_not_configured(self) -> None:
        self.assertFalse(SearchClient("baidu", "sk-test").configured)

    def test_missing_key_raises_without_touching_network(self) -> None:
        with self.assertRaises(SearchError):
            SearchClient("tavily", "").search("DeepSeek")

    def test_unknown_provider_raises_before_request(self) -> None:
        with self.assertRaises(SearchError):
            SearchClient("baidu", "sk-test").search("DeepSeek")

    def test_empty_query_is_rejected(self) -> None:
        with self.assertRaises(SearchError):
            SearchClient("tavily", "sk-test").search("   ")


class StatusTest(unittest.TestCase):
    """启动信息里那一行状态。它必须能区分"没配 key"和"服务商写错了"。"""

    def test_reports_unknown_provider(self) -> None:
        status = SearchClient("baidu", "sk-test").status()
        self.assertIn("baidu", status)
        self.assertIn("不认识", status)

    def test_reports_missing_key(self) -> None:
        status = SearchClient("tavily", "").status()
        self.assertIn("没配 key", status)

    def test_reports_configured(self) -> None:
        status = SearchClient("tavily", "sk-test", 7).status()
        self.assertIn("已配置", status)
        self.assertIn("7", status)


if __name__ == "__main__":
    unittest.main()
