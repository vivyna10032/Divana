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


if __name__ == "__main__":
    unittest.main()
