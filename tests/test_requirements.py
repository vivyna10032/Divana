"""直接 import 的第三方包，必须在 requirements.txt 里声明。

这条规矩 README 里写着（"**直接 import 的包就该写在自己的清单里**，否则哪天上游不再
需要它，你的项目会在某次重装环境后突然起不来，而原因很难查"），但规矩靠记性是靠不住的。

2026-10-16 就翻过一次车：新测试里图省事写了 `import httpx`——本地跑得好好的，因为这台
机器的 Anaconda 里恰好有 httpx；推到 CI 的**干净环境**立刻 import 失败（这个项目装的是
openai 3.13，它用的是 `httpx2`）。一次 red CI 换一条教训：**声明过的依赖，才能在干净
环境里重现；没声明的，"我这儿能跑"只是巧合。**

所以这条规矩现在由测试来执行。没装第三方依赖也能跑（它只读源码）。

用法：python -m unittest tests.test_requirements -v
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: 只扫这几个地方。docs/handbook 用运行时 Python 单独生成 PDF，不参与这里的约束。
SCANNED = ("divana", "tests", "scripts")

#: 本项目自己的顶层模块（不算第三方）。
LOCAL = {"divana", "tests", "scripts", "content", "content_evals", "build_handbook"}

#: 模块名 ≠ 包名的那几个。左边是 `import` 用的名字，右边是 requirements.txt 里的名字。
MODULE_TO_PACKAGE = {
    "yaml": "pyyaml",
    "dotenv": "python-dotenv",
    "agents": "openai-agents",
    "mcp": "mcp",
}


def declared_packages() -> set[str]:
    """requirements.txt 里声明的包名（规范化成小写、连字符）。"""
    text = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
    names: set[str] = set()
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name = line.split("==")[0].split("[")[0].strip()
        names.add(name.lower().replace("_", "-"))
    return names


def imported_modules() -> set[str]:
    """扫出来的第三方顶层模块名。"""
    found: set[str] = set()
    for folder in SCANNED:
        for path in sorted((PROJECT_ROOT / folder).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        found.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.level:      # 相对 import（from .x import y）不算
                        continue
                    if node.module:
                        found.add(node.module.split(".")[0])
    return found


class DeclaredImportsTest(unittest.TestCase):
    def test_every_third_party_import_is_declared(self) -> None:
        declared = declared_packages()
        offenders: list[str] = []
        for module in sorted(imported_modules()):
            if module in sys.stdlib_module_names or module in LOCAL:
                continue
            package = MODULE_TO_PACKAGE.get(module, module)
            if package.lower().replace("_", "-") not in declared:
                offenders.append(f"{module}（该在 requirements.txt 里写成 {package}）")

        self.assertEqual(
            offenders,
            [],
            "这些包被直接 import 了，但 requirements.txt 里没有：\n  "
            + "\n  ".join(offenders),
        )

    def test_the_scanner_actually_sees_something(self) -> None:
        """防止这个测试哪天变成空转（扫不到文件就永远绿）。"""
        seen = imported_modules()
        self.assertIn("agents", seen)
        self.assertIn("openai", seen)

    def test_requirements_has_no_unused_entries(self) -> None:
        """反向也要查：声明了却一行都没 import 的包，是"忘了删"或者"写错了名字"。"""
        declared = declared_packages()
        used = {
            MODULE_TO_PACKAGE.get(module, module).lower().replace("_", "-")
            for module in imported_modules()
        }
        unused = sorted(declared - used)
        self.assertEqual(unused, [], f"requirements.txt 里这些包没人 import：{unused}")


if __name__ == "__main__":
    unittest.main()
