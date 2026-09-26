"""环境自检：确认"跑的是哪个 Python、依赖装没装、配置齐不齐"。

用法：python -m scripts.check_env

只读不写、不联网，任何时候都能安全地跑。最后一行是结论，
退出码 0 表示环境没问题，1 表示有需要处理的地方。
"""

import json
import os
import sys
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = PROJECT_ROOT / ".venv"
PYVENV_CFG = VENV_DIR / "pyvenv.cfg"
ENV_FILE = PROJECT_ROOT / ".env"

# pip 里的包名 -> import 时写的名字
DEPENDENCIES = {
    "openai-agents": "agents",
    "python-dotenv": "dotenv",
}

# 跑下来发现的问题，最后统一列出来
problems: list[str] = []


def show(label: str, value: str) -> None:
    print(f"  {label}: {value}")


def path_state(path: Path) -> str:
    """返回 存在 / 不存在 / 读不到。

    直接调 exists() 有坑：碰到权限受限的目录会抛异常，
    而自检脚本崩掉比不检查更糟，所以这里把异常收成"读不到"。
    """
    try:
        path.stat()
    except FileNotFoundError:
        return "不存在"
    except OSError:
        return "读不到"
    return "存在"


def read_pyvenv_cfg(key: str) -> str:
    """从 .venv/pyvenv.cfg 里读一个字段，比如 home、version。"""
    try:
        content = PYVENV_CFG.read_text(encoding="utf-8")
    except OSError:
        return ""

    for raw in content.splitlines():
        name, sep, value = raw.partition("=")
        if sep and name.strip() == key:
            return value.strip()
    return ""


def check_interpreter() -> None:
    """最关键的一条：现在这个 python 是不是项目自己的。"""
    print("[1/5] 当前解释器")
    show("路径", sys.executable)
    show("版本", sys.version.split()[0])
    show("环境目录", sys.prefix)

    try:
        in_project_venv = Path(sys.prefix).resolve() == VENV_DIR.resolve()
    except OSError:
        in_project_venv = False

    if in_project_venv:
        print("  -> 就是本项目的 .venv，没问题")
    elif sys.prefix != sys.base_prefix:
        print("  -> 在虚拟环境里，但不是本项目的 .venv")
        problems.append(f"当前用的是别的虚拟环境（{sys.prefix}），不是本项目的 .venv")
    else:
        print("  -> 用的是系统里的 Python（可能是 Anaconda），不是本项目的 .venv")
        problems.append("当前用的不是本项目的 .venv，依赖大概率 import 不到")


def check_venv_origin() -> None:
    """venv 是"寄生"在某个基础解释器上的，那一位不见了 venv 就废了。"""
    print("\n[2/5] 本项目 .venv 的来历")
    cfg_state = path_state(PYVENV_CFG)
    if cfg_state != "存在":
        if cfg_state == "读不到":
            print("  -> 读不到 .venv/pyvenv.cfg，这项跳过")
        else:
            print("  -> 还没有 .venv")
            problems.append("缺少 .venv，先按 README 的快速开始建一个")
        return

    home = read_pyvenv_cfg("home")
    show("声明版本", read_pyvenv_cfg("version") or "未知")
    show("基础解释器", home or "未知")

    home_state = path_state(Path(home)) if home else "未知"
    if home_state == "不存在":
        print("  -> 基础解释器已经不在了，.venv 失效")
        problems.append(f".venv 依赖的 {home} 不存在，删掉 .venv 重建")
    elif home_state == "读不到":
        print("  -> 读不到这个目录（权限限制），这项跳过")


def check_dependencies() -> None:
    print("\n[3/5] 依赖")
    for package, module in DEPENDENCIES.items():
        try:
            found = find_spec(module) is not None
        except (ImportError, ValueError):
            found = False

        if not found:
            show(package, "没装")
            problems.append(f"缺少依赖 {package}，装一下：pip install -r requirements.txt")
            continue

        try:
            show(package, version(package))
        except PackageNotFoundError:
            show(package, "已装（版本读不到）")


def load_env() -> None:
    """把 .env 读进环境变量，让检查结果跟真正跑起来时一致。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        # dotenv 还没装时退一步手工扫一遍，至少能看出 key 写没写
        if path_state(ENV_FILE) != "存在":
            return
        try:
            content = ENV_FILE.read_text(encoding="utf-8")
        except OSError:
            return

        for raw in content.splitlines():
            name, sep, value = raw.strip().partition("=")
            if sep and not name.startswith("#"):
                os.environ.setdefault(name.strip(), value.strip())
        return

    load_dotenv(ENV_FILE)


def check_config() -> None:
    print("\n[4/5] 配置")
    env_state = path_state(ENV_FILE)
    if env_state == "不存在":
        show(".env", f"{ENV_FILE}（不存在）")
        problems.append("缺少 .env，把 .env.example 复制成 .env 再填 key")
    elif env_state == "读不到":
        show(".env", f"{ENV_FILE}（读不到）")
    else:
        # 把文件名和最后修改时间一起打出来：填了没生效，多半是改错文件或没保存
        try:
            stamp = datetime.fromtimestamp(ENV_FILE.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M"
            )
            show(".env", f"{ENV_FILE}（最后修改 {stamp}）")
        except OSError:
            show(".env", str(ENV_FILE))

    load_env()
    # 只报"有没有"，不打印 key 本身
    key = os.environ.get("DIVANA_API_KEY") or os.environ.get("OPENAI_API_KEY")
    show("API key", "已设置" if key else "没设置")
    if not key:
        problems.append("没有读到 DIVANA_API_KEY")

    show("模型", os.environ.get("DIVANA_MODEL", "(没设置，用 divana/config.py 的默认值)"))
    show("接口地址", os.environ.get("DIVANA_BASE_URL", "(没设置，用 divana/config.py 的默认值)"))

    provider = os.environ.get("DIVANA_SEARCH_PROVIDER", "tavily")
    has_search_key = bool(os.environ.get("DIVANA_SEARCH_API_KEY", "").strip())
    show(
        "联网搜索",
        f"{provider}，key {'已设置' if has_search_key else '没设置'}"
        + ("" if has_search_key else "（可选，不配只是不能联网查证）"),
    )

    # GitHub 匿名调 API 只有 60 次/小时，而且按出口 IP 算——走代理很容易被共享 IP 拖累
    has_github_token = bool(os.environ.get("DIVANA_GITHUB_TOKEN", "").strip())
    show(
        "GitHub",
        "已配 token（5000 次/小时）"
        if has_github_token
        else "匿名（60 次/小时，按出口 IP 算）",
    )


def check_tools() -> None:
    """工具定义能不能建起来——这一步会真的构造一遍工具。

    为什么值得单独检查：openai-agents 默认给工具参数开 **strict** JSON Schema，
    而 strict 不允许"开放对象"（`additionalProperties` 不是 false 就直接报错）。
    也就是说参数类型写得太自由（比如 `dict[str, str]`）会在**定义工具的那一刻**抛错
    ——平时改完代码看不出来，一跑就是"启动/评测报错"。

    2026-09-25 踩过一次：为了省一轮模型调用，把 update_plan 的参数改成 dict，
    评测一开就报 "additionalProperties should not be set for object types"。
    这个检查不联网、不花钱、一秒钟出结果——改完工具签名顺手跑一下。
    """
    print("\n[5/5] 工具定义")
    try:
        has_agents = find_spec("agents") is not None
    except (ImportError, ValueError):
        has_agents = False
    if not has_agents:
        print("  -> 没装 openai-agents，这项跳过（上面已经报过了）")
        return

    try:
        from divana.tools import build_tools

        tools = build_tools()
    except Exception as exc:  # noqa: BLE001 - 自检脚本要把异常收成结论，不能崩
        show("构造工具", f"失败：{type(exc).__name__}: {exc}")
        problems.append(
            "工具定义建不起来。最常见的原因是参数类型太自由——strict JSON Schema "
            "不允许开放对象（比如 dict），换成列表加模型就行；上面那行报错会指出是哪个工具"
        )
        return

    show("工具数", str(len(tools)))
    # 顺手报一下"每次调用都要重发"的固定开销：schema 和 docstring 都会进上下文，
    # 而且是**每一次模型调用**都重发一遍。削成本先削这里。
    total_chars = 0
    sizes: list[tuple[int, str]] = []
    for tool in tools:
        name = getattr(tool, "name", "?")
        schema = getattr(tool, "params_json_schema", None) or {}
        size = len(json.dumps(schema, ensure_ascii=False)) + len(
            getattr(tool, "description", "") or ""
        )
        total_chars += size
        sizes.append((size, name))

    show("固定开销", f"全部工具的 schema + 说明合计 {total_chars} 字符（每次调用都重发）")
    for size, name in sorted(sizes, reverse=True)[:3]:
        show(f"  最大：{name}", f"{size} 字符")

    for tool in tools:
        name = getattr(tool, "name", "?")
        schema = getattr(tool, "params_json_schema", None) or {}
        fields = sorted((schema.get("properties") or {}).keys())
        show(f"  {name}", "、".join(fields) if fields else "（无参数）")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:  # Windows 控制台默认不是 UTF-8，不改的话中文会花掉
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass

    print(f"项目根目录: {PROJECT_ROOT}\n")
    check_interpreter()
    check_venv_origin()
    check_dependencies()
    check_config()
    check_tools()

    print("\n" + "-" * 48)
    if not problems:
        print("结论：环境没问题。下一步跑 python -m scripts.smoke_test 确认模型通不通。")
        return 0

    print("结论：有下面这些问题要处理：")
    for index, problem in enumerate(problems, 1):
        print(f"  {index}. {problem}")

    if any("不是本项目的 .venv" in problem for problem in problems):
        print("\n多半是忘了激活环境，先执行这一句再重跑：")
        print("  .\\.venv\\Scripts\\Activate.ps1")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
