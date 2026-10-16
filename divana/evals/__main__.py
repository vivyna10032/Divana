"""跑评测：`python -m divana.evals`

第一次跑：
    python -m divana.evals --save     # 把这次结果存成 baseline

之后每改一次 prompt 或工具：
    python -m divana.evals            # 会自动和 baseline 对比，只看变化

想看她**这段时间准不准、贵不贵**（不需要 API key）：
    python -m divana.evals --history  # 台账：端到端成功率 + 单次成本的趋势

没过的那几条会把**完整轨迹**写成 markdown（`evals/trajectory/`）——哪一轮、
调了什么工具、工具返回了什么、她最后说了什么、跑完文件长什么样。改 prompt 之前
先看轨迹，比只看"哪条断言挂了"有用得多。
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from ..config import Settings, setup_agents_sdk
from .calibrate import render_caps, suggest_caps
from .cases import load_cases
from .pricing import Price, format_money
from .report import (
    append_history,
    compare,
    load_baseline,
    load_history,
    render_attempts_table,
    render_history,
    render_report,
    render_token_table,
    save_baseline,
    summarize,
    write_trajectory,
)


#: 评测**自己写出来**的文件。判断"工作区脏不脏"时要把它们排除掉——
#: 见 `_git_commit` 里的说明。
_OWN_OUTPUTS = ("evals/baseline.json", "evals/history.jsonl")


def _git_commit() -> str:
    """当前提交号。分数变了要能对上"是哪一版代码"。

    工作区有未提交改动时加个 `+脏`。不加的话你会踩这个坑：prompt 改了但还没提交，
    这时存 baseline，它记的是 HEAD 那个提交——而那个提交里的 prompt 并不是你实际
    跑的那一份。回头想复现"这一版为什么是 14/15"就找不到了。

    **但它自己的产物要排除掉。** 这条是 2026-10-15 补的：那天连着跑两次都全绿，
    第二次的提交号却记成了 `b399d9e+脏`——脏的只是评测刚写完的 baseline.json 和
    history.jsonl。而台账是拿去说事的东西，一个看不出原因的 `+脏` 会让读的人以为
    "这版 prompt 是改过还没提交的"，那正好把这一列的意义抵消掉。

    所以要排除两批：**未跟踪文件**（`--untracked-files=no`，否则新 store 出来的
    baseline 自己就让工作区变脏）和**上面那两个自己写的文件**。
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
        dirty = git(
            "status",
            "--porcelain",
            "--untracked-files=no",
            "--",
            ".",
            *(f":(exclude){path}" for path in _OWN_OUTPUTS),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if dirty.returncode == 0 and dirty.stdout.strip():
        return f"{sha}+脏"
    return sha


def main() -> None:
    load_dotenv()

    # 中文 Windows 的控制台是 GBK（cp936）：报告里出现一个它编不出来的字符
    # （比如半角 ¥），`print` 会直接抛 UnicodeEncodeError——不是显示成问号，
    # 是**整条命令崩掉**，报告一个字都看不到。这里让它容错：能编的照常编，
    # 编不出的换成 ?。宁可一个符号难看，也不要整份报告消失。
    with suppress(Exception):
        sys.stdout.reconfigure(errors="replace")

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
    parser.add_argument(
        "--history", action="store_true", help="只打台账（成功率和单次成本的趋势）"
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="只打护栏校准表（对着 baseline 的实测数据算 max_tokens 该是多少）",
    )
    parser.add_argument(
        "--no-history", action="store_true", help="这次不往台账里记"
    )
    parser.add_argument(
        "--trajectory",
        choices=("failed", "all", "off"),
        default="failed",
        help="把完整轨迹写成 markdown：默认只写没过的（failed）",
    )
    args = parser.parse_args()

    # 看台账不需要 API key，也不该起 agent 栈——所以放在最前面直接返回。
    # "我只想知道这项目现在多准多贵"是个很自然的诉求，不该逼人先配好 key。
    if args.history:
        print(render_history(load_history()))
        return

    # 校准同理：读的是已经跑出来的数据，不需要 key，也不该起 agent 栈。
    if args.calibrate:
        baseline = load_baseline(Path(args.baseline) if args.baseline else None)
        if not baseline:
            print("还没有 baseline，先全量跑一次评测（可以带 --save）")
            return
        cases = load_cases(Path(args.cases) if args.cases else None)
        print(render_caps(suggest_caps(baseline, cases), ran_at=str(baseline.get("ran_at", ""))))
        return

    settings = Settings.from_env()
    setup_agents_sdk(settings)
    price = Price.from_env()

    all_cases = load_cases(Path(args.cases) if args.cases else None)
    cases = all_cases
    if args.only:
        wanted = {name.strip() for name in args.only.split(",") if name.strip()}
        cases = [case for case in all_cases if case.id in wanted]
        if not cases:
            print(f"没有匹配的用例：{args.only}")
            return

    baseline_path = Path(args.baseline) if args.baseline else None
    print(f"跑 {len(cases)} 条用例，每条 {args.attempts} 次。\n")
    started = datetime.now()
    cases_by_id = {case.id: case for case in cases}
    commit = _git_commit()
    written: list[Path] = []

    def wants_trajectory(result) -> bool:
        if args.trajectory == "all":
            return True
        return args.trajectory == "failed" and not result.passed

    def show(result) -> None:
        mark = "过" if result.passed else "挂"
        print(
            f"  [{mark}] {result.case_id}　{result.passed_attempts}/{result.attempts}　"
            f"工具 {len(result.outcome.tool_calls)} 次　{result.outcome.tokens} tokens"
        )
        if not result.passed:
            # 先把工具序列打出来：一眼能看出"绕圈/重复调用"这类退化。
            chain = " → ".join(call.name for call in result.outcome.tool_calls)
            print(f"        工具轨迹：{chain or '（一次都没调）'}")
        if wants_trajectory(result):
            path = write_trajectory(
                cases_by_id[result.case_id],
                result,
                model=settings.model,
                commit=commit,
                price=price,
            )
            written.append(path)
            print(f"        完整轨迹：{path}")

    # runner 会牵进整个 agent 栈（agents / pydantic）。特意放在这里、而不是文件
    # 顶部：这样 `--history` 那种"只想看数"的命令不用装齐依赖也能跑。
    from .runner import run_all

    results = asyncio.run(
        run_all(settings, cases, attempts=args.attempts, on_result=show)
    )

    summary = summarize(
        results,
        model=settings.model,
        commit=commit,
        ran_at=started.strftime("%Y-%m-%d %H:%M"),
        price=price,
        # 传**全集**：判据指纹要能横向比。--only 只跑几条时，指纹仍记全套，
        # 否则一次半截运行会把"尺子"的样子记歪。
        cases=all_cases,
    )
    baseline = load_baseline(baseline_path)
    # --only 时传 subset：否则 baseline 里没跑的用例会被刷成"只存在于 baseline"，
    # 把真正的 [回归] 淹掉（2026-09-26 真被淹过一次，还害我先怀疑是回归）。
    diff = compare(baseline, summary, subset=bool(args.only)) if baseline else None

    print()
    print(render_report(summary, diff))

    # token 花在哪：每条一行。明细（每次模型调用、每个工具返回的体量）在轨迹文件里
    # ——用 --trajectory all 就能全拿到。
    if results:
        print()
        print(render_token_table(results, cases_by_id, price=price))
        attempts = render_attempts_table(results)
        if attempts:
            print()
            print(attempts)

    if price.is_default:
        print()
        print(
            "（成本按内置默认价算的："
            f"{format_money(price.input_per_million)}/百万 input、"
            f"{format_money(price.output_per_million)}/百万 output。"
            "和官网核对过之后，在 .env 里写 DIVANA_PRICE_IN / _OUT / _CACHED 更准）"
        )

    if written:
        print()
        print(f"轨迹写了 {len(written)} 份，在 {written[0].parent}/")
        print("（同名会被这次覆盖；这是运行产物，不进版本控制）")

    if args.save:
        if args.only:
            # `--only X --save` 会把 baseline 写成"只有 X 那几条"——其余用例的
            # 通过记录被**抹掉**，而你下次跑全量时它们会显示成 [新增]，
            # 看起来像一次大改。这种事不该靠人记住，所以直接拒绝。
            print()
            print(
                "（--only 时不存 baseline：那份 baseline 只会有这次跑过的几条，"
                "其余用例的历史会被抹掉。要存就等一次全量跑）"
            )
        else:
            saved = save_baseline(summary, baseline_path)
            print()
            print(f"已存成 baseline：{saved}")

    # 台账只记**全量**跑：--only 的半截数据混进去，趋势线立刻失真
    # （成功率会凭跑了几条上下跳，看着像她在退化）。
    if args.only:
        return
    if args.no_history:
        print()
        print("（--no-history：这次没记台账）")
        return

    saved = append_history(summary)
    print()
    print(f"已记进台账：{saved}（第 {len(load_history())} 次全量跑）")
    print("趋势用 `python -m divana.evals --history` 看")


if __name__ == "__main__":
    main()
