"""用例的格式与读取。

一条用例只写四件事：**给她什么、必须做什么、禁止做什么、上限多少**。
判的是轨迹（她做了什么），不是文风——所以大部分断言都能自动判、零噪声。

加用例最好的方式是：**某天发现她答错了，就把当时那句话存成一条**。
那是回归用例，比手写合成的有价值得多。
"""

from __future__ import annotations

import re
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
        "expect_pattern",
        "expect_files",
        "max_tool_calls",
        "max_calls_per_tool",
        "max_tokens",
        "check_citations",
        "note",
    }
)


class CaseError(ValueError):
    """用例文件写错了。宁可起不来，也不要悄悄少测一条。"""


@dataclass(frozen=True)
class TextPattern:
    """一条结构断言：用正则描述"这个回答该长成什么样"。

    为什么不只用词表（`expect_any`）：**词表是在追措辞的尾巴**。实测她答
    "结果里没有一条跟「zzqqxxyy」…有关的东西"——意思完全对，但原来那串词表
    （没搜到 / 没有结果 / …）一个都没命中。

    更糟的是，词表加词会带**副作用**：往里加一个"输入错误"想放宽，等于把
    "她反过来指责用户打错字"也放行成了通过。字符串匹配没有语义，加词就是加漏洞。

    正则能表达"否定词 + 结果词"这类**结构**，措辞怎么变都能判，也不会把
    反义行为放进来。`label` 是给人看的——报告里失败时列的是它，不是那串正则。
    """

    pattern: str
    label: str = ""


@dataclass(frozen=True)
class FileExpectation:
    """跑完之后，对临时 vault 里某个文件的断言。

    前面那些断言只看得见她**做了什么**（调了哪个工具）；这一层看**结果对不对**。
    差别很实在：她完全可能调了 `update_plan`，却把整份计划覆盖、丢了一半内容——
    只看工具调用是发现不了的。

    `path` 是相对 vault 的路径，支持 `fnmatch` 通配（比如 `notes/*.md`）。
    匹配到多个文件时，**只要有一个**同时满足全部条件就算过——不然她多存一篇笔记
    就会误判。
    """

    path: str
    exists: bool = True
    contains: tuple[str, ...] = ()
    not_contains: tuple[str, ...] = ()
    pattern: str = ""
    not_pattern: str = ""
    label: str = ""

    @property
    def has_assertion(self) -> bool:
        """光写个 path 什么都不判，等于白写——加载时要拦下来。"""
        return bool(
            not self.exists
            or self.contains
            or self.not_contains
            or self.pattern
            or self.not_pattern
        )


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    follow_ups: tuple[str, ...] = ()
    given_profile: dict[str, str] = field(default_factory=dict)
    given_plan: dict[str, str] = field(default_factory=dict)
    given_settings: dict[str, Any] = field(default_factory=dict)
    expect_tools: tuple[str, ...] = ()
    forbid_tools: tuple[str, ...] = ()
    expect_text: tuple[str, ...] = ()
    expect_any: tuple[str, ...] = ()
    expect_pattern: tuple[TextPattern, ...] = ()
    expect_files: tuple[FileExpectation, ...] = ()
    max_tool_calls: int | None = None
    max_calls_per_tool: dict[str, int] = field(default_factory=dict)
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


def _settings_map(value: Any, *, where: str) -> dict[str, Any]:
    """覆盖 Settings 的字段，用来造"工具此时不可用"这类场景。

    例：把 `search_api_key` 置空，搜索工具就会正常地失败——这样就能测
    "工具挂了的时候她怎么反应"，而不必真去搞坏一个 API key。

    字段名对不对这里查不了（这一层不该 import config），所以有一条测试专门
    盯着用法：用例文件里出现的字段必须真的是 Settings 的字段。
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CaseError(f"{where}：要写成 {{字段: 值}} 的形式")

    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise CaseError(f"{where}：字段名要写字符串")
        if not isinstance(item, (str, int, bool)):
            raise CaseError(f"{where}：{key} 的值只能是字符串 / 整数 / 布尔")
        result[key.strip()] = item
    return result


def _int_map(value: Any, *, where: str) -> dict[str, int]:
    """工具名 → 次数上限。比如 `{update_plan: 1}`。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CaseError(f"{where}：要写成 {{工具名: 上限}} 的形式")

    result: dict[str, int] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise CaseError(f"{where}：工具名要写字符串")
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise CaseError(f"{where}：{key} 的上限要写非负整数")
        result[key.strip()] = item
    return result


def _file_expectations(value: Any, *, where: str) -> tuple[FileExpectation, ...]:
    """解析 expect_files：每一项是"一个文件 + 要在它身上成立的条件"。"""
    if value is None:
        return ()
    items = [value] if isinstance(value, dict) else value
    if not isinstance(items, list):
        raise CaseError(f"{where}：要写字典，或者字典的列表")

    fields = {
        "path",
        "exists",
        "contains",
        "not_contains",
        "pattern",
        "not_pattern",
        "label",
    }
    parsed: list[FileExpectation] = []
    for item in items:
        if not isinstance(item, dict):
            raise CaseError(f"{where}：每一项都要是字典，比如 {{path: plan.md, ...}}")
        unknown = set(item) - fields
        if unknown:
            raise CaseError(f"{where}：不认识的字段 {sorted(unknown)}（拼错了？）")

        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            raise CaseError(f"{where}：缺 path（相对 vault 的路径，比如 plan.md）")

        exists = item.get("exists", True)
        if not isinstance(exists, bool):
            raise CaseError(f"{where}：exists 要写 true / false")

        for key in ("pattern", "not_pattern"):
            raw = item.get(key, "")
            if not isinstance(raw, str):
                raise CaseError(f"{where}：{key} 要写字符串")
            if raw:
                try:
                    re.compile(raw)
                except re.error as exc:
                    raise CaseError(
                        f"{where}：{key} 的正则写错了 {raw!r} —— {exc}"
                    ) from exc

        label = item.get("label", "")
        if not isinstance(label, str):
            raise CaseError(f"{where}：label 要写字符串")

        made = FileExpectation(
            path=path.strip(),
            exists=exists,
            contains=_string_list(item.get("contains"), where=f"{where}.contains"),
            not_contains=_string_list(
                item.get("not_contains"), where=f"{where}.not_contains"
            ),
            pattern=str(item.get("pattern", "")).strip(),
            not_pattern=str(item.get("not_pattern", "")).strip(),
            label=label.strip(),
        )
        if not made.has_assertion:
            raise CaseError(
                f"{where}：{made.path} 这条只写了 path、什么条件都没写——等于白写。"
                "要么加 contains / pattern，要么把这条删了"
            )
        parsed.append(made)
    return tuple(parsed)


def _patterns(value: Any, *, where: str) -> tuple[TextPattern, ...]:
    """解析 expect_pattern。几种写法都收：

        expect_pattern: '正则'                # 一条，label 就用正则本身
        expect_pattern: ['正则1', '正则2']     # 多条，**全部**必须命中
        expect_pattern:
          - label: 如实说没搜到                # 带标签，报告里好读
            pattern: '正则'

    列表是"全部命中"，不是"任一命中"：正则自己就能用 `|` 表达"任一"，
    两个语义都留会让人每次都得想一下。要"任一"就写进同一条正则里。
    """
    if value is None:
        return ()
    items = [value] if isinstance(value, (str, dict)) else value
    if not isinstance(items, list):
        raise CaseError(f"{where}：要写字符串、字典，或者它们的列表")

    parsed: list[TextPattern] = []
    for item in items:
        if isinstance(item, str):
            pattern, label = item, ""
        elif isinstance(item, dict):
            unknown = set(item) - {"pattern", "label"}
            if unknown:
                raise CaseError(f"{where}：不认识的字段 {sorted(unknown)}（拼错了？）")
            pattern = item.get("pattern")
            label = item.get("label", "")
            if not isinstance(pattern, str):
                raise CaseError(f"{where}：缺 pattern，或者它不是字符串")
            if not isinstance(label, str):
                raise CaseError(f"{where}：label 要写字符串")
        else:
            raise CaseError(f"{where}：每一项要么是字符串，要么是 {{pattern, label}}")

        try:
            re.compile(pattern)
        except re.error as exc:
            # 加载时就炸。别等某天正好跑到这条用例，才发现正则写错了。
            raise CaseError(f"{where}：正则写错了 {pattern!r} —— {exc}") from exc
        parsed.append(TextPattern(pattern=pattern, label=label.strip()))
    return tuple(parsed)


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
    unknown_given = set(given) - {"profile", "plan", "settings"}
    if unknown_given:
        raise CaseError(f"{where}：given 里不认识的字段 {sorted(unknown_given)}")

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
        given_settings=_settings_map(
            given.get("settings"), where=f"{where}.given.settings"
        ),
        expect_tools=_string_list(raw.get("expect_tools"), where=f"{where}.expect_tools"),
        forbid_tools=_string_list(raw.get("forbid_tools"), where=f"{where}.forbid_tools"),
        expect_text=_string_list(raw.get("expect_text"), where=f"{where}.expect_text"),
        expect_any=_string_list(raw.get("expect_any"), where=f"{where}.expect_any"),
        expect_pattern=_patterns(
            raw.get("expect_pattern"), where=f"{where}.expect_pattern"
        ),
        expect_files=_file_expectations(
            raw.get("expect_files"), where=f"{where}.expect_files"
        ),
        max_tool_calls=raw.get("max_tool_calls"),
        max_calls_per_tool=_int_map(
            raw.get("max_calls_per_tool"), where=f"{where}.max_calls_per_tool"
        ),
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
