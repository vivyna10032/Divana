"""真跑：一条用例 = 一个临时目录 + 一个独立会话库。

**隔离不是"小心一点"，是结构上保证的**：每条用例都在自己的临时目录里跑，
画像、计划、笔记、会话库全是新建的，跑完就删。所以评测绝不会碰你真实的学习记录
——哪怕某条用例让 agent 去写文件。

agents 的导入放在函数里，这样这个模块不装 agent 栈也能导入（测试里要用它的类型）。
"""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

from ..agent import build_agent
from ..config import Settings
from ..context import build_context
from ..session import open_session
from .cases import Case
from .checks import FileSnapshot, Outcome, Step, ToolCall, check_case
from .report import CaseResult


def _snapshot(vault: Path) -> tuple[FileSnapshot, ...]:
    """把临时 vault 里的文件拍个快照——**必须赶在临时目录被删掉之前**。

    这就是"文件副作用断言"的数据来源：跑完之后她到底写出了什么。
    只收文件，而且都在这个临时目录里，不会碰到你真实的 vault。
    """
    if not vault.is_dir():
        return ()
    shots: list[FileSnapshot] = []
    for path in sorted(vault.rglob("*")):
        if path.is_file():
            shots.append(
                FileSnapshot(
                    path=path.relative_to(vault).as_posix(),
                    text=path.read_text(encoding="utf-8", errors="replace"),
                )
            )
    return tuple(shots)


async def _run_once(settings: Settings, case: Case) -> Outcome:
    """跑一遍：多轮用例就在同一条会话里连着问。"""
    from agents import Runner, ToolCallItem, ToolCallOutputItem

    # 用例可以覆盖几项配置，用来造"工具此时不可用"这类场景：比如把 search_api_key
    # 置空，搜索工具就会**正常地**失败——不用真去搞坏一个 key，也不必真把网断掉。
    if case.given_settings:
        try:
            settings = replace(settings, **case.given_settings)
        except TypeError as exc:
            raise RuntimeError(
                f"用例 {case.id} 的 given.settings 写错了：{exc}"
            ) from exc

    with tempfile.TemporaryDirectory(prefix=f"divana-eval-{case.id}-") as tmp:
        root = Path(tmp)
        vault = root / "vault"
        context = build_context(settings, root=vault)

        # 用例指定的前置状态：想测"她记不记得你"，就得能塞一份画像进去
        for section, content in case.given_profile.items():
            context.profile.write_section(section, content)
        for section, content in case.given_plan.items():
            context.plan.write_section(section, content)

        agent = build_agent(settings, context)
        session = open_session(f"eval-{case.id}", db_path=root / "eval.db")
        steps: list[Step] = []
        outputs: list[str] = []
        text = ""
        tokens = requests = 0
        try:
            for turn in case.turns:
                steps.append(Step(kind="turn", text=turn))
                result = await Runner.run(agent, turn, session=session, context=context)
                for item in result.new_items:
                    if isinstance(item, ToolCallItem):
                        raw = item.raw_item
                        steps.append(
                            Step(
                                kind="tool",
                                name=str(getattr(raw, "name", "") or "?"),
                                arguments=str(getattr(raw, "arguments", "") or ""),
                            )
                        )
                    elif isinstance(item, ToolCallOutputItem):
                        output = str(getattr(item, "output", "") or "")
                        outputs.append(output)
                        # 工具的返回通常紧跟在它那次调用后面。挂到**最近的、还没有
                        # 返回的那次调用**上，而不是简单地挂在上一步——万一 SDK 把
                        # 返回和调用拆开了，这么写也不会错位。
                        for index in range(len(steps) - 1, -1, -1):
                            if steps[index].kind == "tool" and not steps[index].output:
                                steps[index] = replace(steps[index], output=output)
                                break
                text = str(result.final_output or "")
                steps.append(Step(kind="reply", text=text))
                usage = getattr(result.context_wrapper, "usage", None)
                if usage is not None:
                    tokens = int(getattr(usage, "total_tokens", 0) or 0)
                    requests = int(getattr(usage, "requests", 0) or 0)
        finally:
            session.close()

        # 出了这个 with，临时目录就没了——所以快照必须在这儿做。
        files = _snapshot(vault)

    return Outcome(
        text=text,
        # 工具列表从轨迹里推出来，不另存一份——两份数据总有一天会对不上。
        tool_calls=tuple(
            ToolCall(name=step.name, arguments=step.arguments)
            for step in steps
            if step.kind == "tool"
        ),
        tool_outputs=tuple(outputs),
        tokens=tokens,
        requests=requests,
        files=files,
        steps=tuple(steps),
    )


async def run_case(settings: Settings, case: Case, *, attempts: int = 1) -> CaseResult:
    """跑一条用例，可以重复几次看稳定性。

    报告里留的是**失败那一次**的断言（而不是最后一次的）——排查问题时更有用。
    """
    outcome: Outcome | None = None
    findings = []
    passed_attempts = 0

    for _ in range(max(1, attempts)):
        outcome = await _run_once(settings, case)
        current = check_case(case, outcome)
        if all(item.ok for item in current):
            passed_attempts += 1
        elif not findings:
            findings = current

    if outcome is None:  # attempts<=0 时兜底，正常不会走到
        raise RuntimeError("没有跑任何一次")
    if not findings:
        findings = check_case(case, outcome)

    return CaseResult(
        case_id=case.id,
        outcome=outcome,
        findings=tuple(findings),
        attempts=max(1, attempts),
        passed_attempts=passed_attempts,
    )


async def run_all(
    settings: Settings,
    cases: list[Case],
    *,
    attempts: int = 1,
    on_result=None,
) -> list[CaseResult]:
    """一条一条跑。

    故意不并发：一是免得同时打爆接口触发限流，二是输出能一条条看着走。
    """
    results: list[CaseResult] = []
    for case in cases:
        result = await run_case(settings, case, attempts=attempts)
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results
