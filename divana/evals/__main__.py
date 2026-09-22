"""跑评测：`python -m divana.evals`

第一次跑：
    python -m divana.evals --save     # 把这次结果存成 baseline

之后每改一次 prompt 或工具：
    python -m divana.evals            # 会自动和 baseline 对比，只看变化
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from ..config import Settings, setup_agents_sdk
from .cases import load_cases
from .report import compare, load_baseline, render_report, save_baseline, summarize
from .runner import run_all


def _git_commit() -> str:
    """当前提交号。分数变了要能对上"是哪一版代码"。

    工作区有未提交改动时加个 `+脏`。不加的话你会踩这个坑：prompt 改了但还没提交，
    这时存 baseline，它记的是 HEAD 那个提交——而那个提交里的 prompt 并不是你实际
    跑的那一份。回头想复现"这一版为什么是 14/15"就找不到了。

    只看**已跟踪文件**的改动（`--untracked-files=no`）：baseline.json 自己就是
    个未跟踪文件，否则每次都会显示脏。
    """

    def git(*args: str):
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=5
        )

    try:
        head = git("rev-parse", "--short", "HEAD")
        if head.returncode != 0:
            return ""
        sha = head.stdout.strip()
        dirty = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.SubprocessError):
        return ""
    if dirty.returncode == 0 and dirty.stdout.strip():
        return f"{sha}+脏"
    return sha


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="python -m divana.evals", description="跑一遍评测用例"
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=1,
        help="每条跑几次（看稳定性，但成本翻倍），默认 1",
    )
    parser.add_argument("--only", default="", help="只跑这几条，逗号分开")
    parser.add_argument("--cases", default="", help="用例文件路径")
    parser.add_argument("--baseline", default="", help="baseline 文件路径")
    parser.add_argument("--save", action="store_true", help="把这次结果存成 baseline")
    args = parser.parse_args()

    settings = Settings.from_env()
    setup_agents_sdk(settings)

    cases = load_cases(Path(args.cases) if args.cases else None)
    if args.only:
        wanted = {name.strip() for name in args.only.split(",") if name.strip()}
        cases = [case for case in cases if case.id in wanted]
        if not cases:
            print(f"没有匹配的用例：{args.only}")
            return

    baseline_path = Path(args.baseline) if args.baseline else None
    print(f"跑 {len(cases)} 条用例，每条 {args.attempts} 次。\n")
    started = datetime.now()

    def show(result) -> None:
        mark = "过" if result.passed else "挂"
        print(
            f"  [{mark}] {result.case_id}　{result.passed_attempts}/{result.attempts}　"
            f"工具 {len(result.outcome.tool_calls)} 次　{result.outcome.tokens} tokens"
        )

    results = asyncio.run(
        run_all(settings, cases, attempts=args.attempts, on_result=show)
    )

    summary = summarize(
        results,
        model=settings.model,
        commit=_git_commit(),
        ran_at=started.strftime("%Y-%m-%d %H:%M"),
    )
    baseline = load_baseline(baseline_path)
    diff = compare(baseline, summary) if baseline else None

    print()
    print(render_report(summary, diff))

    if args.save:
        saved = save_baseline(summary, baseline_path)
        print()
        print(f"已存成 baseline：{saved}")


if __name__ == "__main__":
    main()
