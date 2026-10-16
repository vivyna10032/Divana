"""配置解析的离线测试。不联网、不碰模型。

用法：python -m unittest tests.test_config -v
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from divana.config import Settings, env_file


class EnvFileTest(unittest.TestCase):
    def test_returns_a_dotenv_path(self) -> None:
        # 找不到 .env 时会退回项目根目录下的 .env，所以这里永远该是这个名字
        self.assertEqual(env_file().name, ".env")


class SettingsTest(unittest.TestCase):
    def parse(self, env: dict[str, str]) -> Settings:
        with patch.dict(os.environ, env, clear=True):
            return Settings.from_env()

    def test_missing_api_key_exits_with_hint(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.parse({})
        self.assertIn("DIVANA_API_KEY", str(ctx.exception))

    def test_search_is_optional(self) -> None:
        settings = self.parse({"DIVANA_API_KEY": "sk-test"})

        self.assertEqual(settings.search_provider, "tavily")
        self.assertEqual(settings.search_max_results, 5)
        self.assertEqual(settings.search_api_key, "")

    def test_search_values_are_normalized(self) -> None:
        settings = self.parse(
            {
                "DIVANA_API_KEY": "sk-test",
                "DIVANA_SEARCH_PROVIDER": "  Brave ",
                "DIVANA_SEARCH_API_KEY": " key-with-spaces ",
                "DIVANA_SEARCH_MAX_RESULTS": "8",
            }
        )

        self.assertEqual(settings.search_provider, "brave")
        self.assertEqual(settings.search_api_key, "key-with-spaces")
        self.assertEqual(settings.search_max_results, 8)

    def test_base_url_trailing_slash_is_stripped(self) -> None:
        settings = self.parse(
            {"DIVANA_API_KEY": "sk-test", "DIVANA_BASE_URL": "https://api.example.com/"}
        )
        self.assertEqual(settings.base_url, "https://api.example.com")

    def test_non_numeric_search_max_results_exits_with_hint(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self.parse({"DIVANA_API_KEY": "sk-test", "DIVANA_SEARCH_MAX_RESULTS": "abc"})
        self.assertIn("DIVANA_SEARCH_MAX_RESULTS", str(ctx.exception))

    def test_zero_search_max_results_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            self.parse({"DIVANA_API_KEY": "sk-test", "DIVANA_SEARCH_MAX_RESULTS": "0"})

    def test_brakes_have_defaults(self) -> None:
        """三个刹车（超时 / 重试 / 最多几圈）以前全靠 SDK 默认，现在是我们显式定的。

        默认值本身就是"设计决定"，所以值得钉住：超时要宽到正常回答打不断它，
        重试要只覆盖"可能自己好"的失败，圈数要跟 SDK 原来的默认值一致。
        """
        settings = self.parse({"DIVANA_API_KEY": "sk-test"})
        self.assertEqual(settings.timeout_seconds, 120)
        self.assertEqual(settings.max_retries, 2)
        self.assertEqual(settings.max_turns, 10)

    def test_brakes_can_be_tuned_from_env(self) -> None:
        settings = self.parse(
            {
                "DIVANA_API_KEY": "sk-test",
                "DIVANA_TIMEOUT_SECONDS": "45",
                "DIVANA_MAX_RETRIES": "1",
                "DIVANA_MAX_TURNS": "6",
            }
        )
        self.assertEqual(settings.timeout_seconds, 45)
        self.assertEqual(settings.max_retries, 1)
        self.assertEqual(settings.max_turns, 6)

    def test_zero_retries_is_allowed(self) -> None:
        """"不重试"是个合法选择（比如你就在排查失败本身，想让它立刻报错）。"""
        settings = self.parse({"DIVANA_API_KEY": "sk-test", "DIVANA_MAX_RETRIES": "0"})
        self.assertEqual(settings.max_retries, 0)

    def test_bad_brake_values_are_rejected_loudly(self) -> None:
        """写错了要说清是哪一项——静默退回默认值会让你以为配置生效了。"""
        for name, value in (
            ("DIVANA_TIMEOUT_SECONDS", "abc"),
            ("DIVANA_TIMEOUT_SECONDS", "0"),
            ("DIVANA_MAX_RETRIES", "-1"),
            ("DIVANA_MAX_TURNS", "0"),
        ):
            with self.assertRaises(SystemExit) as ctx:
                self.parse({"DIVANA_API_KEY": "sk-test", name: value})
            self.assertIn(name, str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
