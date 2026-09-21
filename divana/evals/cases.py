"""用例的格式与读取。

一条用例只写四件事：**给她什么、必须做什么、禁止做什么、上限多少**。
判的是轨迹（她做了什么），不是文风——所以大部分断言都能自动判、零噪声。

加用例最好的方式是：**某天发现她答错了，就把当时那句话存成一条**。
那是回归用例，比手写合成的有价值得多。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CASES_PATH = PROJECT_ROOT / "evals" / "cases.yaml"

# 允许出现的字段，白名单。写错一个字段名会让那条断言悄悄失效——宁可报错。
_KNOWN_FIELDS = frozenset(
    {
        "id",
        "question",
        "follow_ups",
        "given",
        "expect_tools",
        "forbid_tools",
        "expect_text",
        "expect_any",
        "max_tool_calls",
        "max_tokens",
        "check_citations",
        "note",
    }
)


class CaseError(ValueError):
    """用例文件写错了。宁可起不来，也不要悄悄少测一条。"""


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    follow_ups: tuple[str, ...] = ()
    given_profile: dict[str, str] = field(default_factory=dict)
    given_plan: dict[str, str] = field(default_factory=dict)
    expect_tools: tuple[str, ...] = ()
    forbid_tools: tuple[str, ...] = ()
    expect_text: tuple[str, ...] = ()
    expect_any: tuple[str, ...] = ()
    max_tool_calls: int | None = None
    max_tokens: int | None = None
    check_citations: bool = False
    note: str = ""

    @property
    def turns(self) -> tuple[str, ...]:
        """这一条要问的每一轮。多轮用例就是在这里体现的。"""
        return (self.question, *self.follow_ups)


def _string_list(value: Any, *, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise CaseError(f"{where}：这里要写字符串或字符串列表，实际是 {value!r}")


def _section_map(value: Any, *, where: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise CaseError(f"{where}：要写成 {{章节: 内容}} 的形式")
    return {str(key): str(item) for key, item in value.items()}


def parse_case(raw: Any, *, where: str = "") -> Case:
    """把一条 YAML 记录变成 Case，顺便把写错的地方挑出来。"""
    if not isinstance(raw, dict):
        raise CaseError(f"{where}：一条用例应该是一个字典，实际是 {type(raw).__name__}")

    unknown = set(raw) - _KNOWN_FIELDS
    if unknown:
        raise CaseError(f"{where}：不认识的字段 {sorted(unknown)}（拼错了？）")

    for needed in ("id", "question"):
        if not str(raw.get(needed, "")).strip():
            raise CaseError(f"{where}：缺 {needed}")

    case_id = str(raw["id"]).strip()
    where = f"用例 {case_id}"
    given = raw.get("given") or {}
    if not isinstance(given, dict):
        raise CaseError(f"{where}：given 要写成字典")

    for field_name in ("max_tool_calls", "max_tokens"):
        value = raw.get(field_name)
        if value is not None and not isinstance(value, int):
            raise CaseError(f"{where}：{field_name} 要写整数")

    return Case(
        id=case_id,
        question=str(raw["question"]).strip(),
        follow_ups=_string_list(raw.get("follow_ups"), where=f"{where}.follow_ups"),
        given_profile=_section_map(given.get("profile"), where=f"{where}.given.profile"),
        given_plan=_section_map(given.get("plan"), where=f"{where}.given.plan"),
        expect_tools=_string_list(raw.get("expect_tools"), where=f"{where}.expect_tools"),
        forbid_tools=_string_list(raw.get("forbid_tools"), where=f"{where}.forbid_tools"),
        expect_text=_string_list(raw.get("expect_text"), where=f"{where}.expect_text"),
        expect_any=_string_list(raw.get("expect_any"), where=f"{where}.expect_any"),
        max_tool_calls=raw.get("max_tool_calls"),
        max_tokens=raw.get("max_tokens"),
        check_citations=bool(raw.get("check_citations", False)),
        note=str(raw.get("note", "")),
    )


def load_cases(path: Path | None = None) -> list[Case]:
    """读用例文件。文件不存在、或者 YAML 坏了，都直接报错——不能装作跑完了。"""
    target = Path(path) if path is not None else CASES_PATH
    if not target.exists():
        raise CaseError(f"找不到用例文件：{target}")

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CaseError(f"用例文件的 YAML 有问题：{exc}") from exc

    if not isinstance(raw, list):
        raise CaseError("用例文件的顶层应该是一个列表")

    cases = [parse_case(item, where=f"第 {index + 1} 条") for index, item in enumerate(raw)]
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise CaseError(f"用例 id 重复了：{case.id}")
        seen.add(case.id)
    return cases
