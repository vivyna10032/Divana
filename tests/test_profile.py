"""学习者画像的离线测试：不联网、不碰模型，跑起来很快。

用法：python -m unittest tests.test_profile -v
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from divana.profile import (
    DEFAULT_PROFILE_PATH,
    MAX_SECTION_CHARS,
    SECTIONS,
    TEMPLATE,
    ProfileError,
    ProfileStore,
)
from divana.markdown_store import iter_sections
from divana.storage import atomic_write


class ProfileStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "vault" / "profile.md"

    def store(self) -> ProfileStore:
        return ProfileStore(self.path)

    def test_default_path_is_in_vault(self) -> None:
        self.assertEqual(DEFAULT_PROFILE_PATH.name, "profile.md")
        self.assertEqual(DEFAULT_PROFILE_PATH.parent.name, "vault")

    def test_read_falls_back_to_template_without_touching_disk(self) -> None:
        self.assertEqual(self.store().read(), TEMPLATE)
        self.assertFalse(self.path.exists())

    def test_ensure_exists_creates_file_with_every_section(self) -> None:
        store = self.store()
        store.ensure_exists()

        self.assertTrue(self.path.exists())
        text = store.read()
        for section in SECTIONS:
            self.assertIn(f"## {section}", text)

    def test_iter_sections_finds_each_heading_once(self) -> None:
        lines = TEMPLATE.splitlines()
        found = [name for name, _, _ in iter_sections(lines)]
        self.assertEqual(found, list(SECTIONS))

    def test_write_section_replaces_only_that_section(self) -> None:
        store = self.store()
        store.ensure_exists()
        store.write_section("目标", "学会用 PyTorch 训练一个自己的小模型。")

        text = store.read()
        self.assertIn("学会用 PyTorch 训练一个自己的小模型。", text)
        # 其它章节的占位内容原样还在
        self.assertIn("## 薄弱点\n\n（还没记录。）", text)

    def test_write_section_can_be_called_twice(self) -> None:
        store = self.store()
        store.write_section("目标", "第一版")
        store.write_section("目标", "第二版")

        text = store.read()
        self.assertIn("第二版", text)
        self.assertNotIn("第一版", text)
        # 同一节标题不该出现两次
        self.assertEqual(text.count("## 目标"), 1)

    def test_write_section_keeps_handwritten_edits(self) -> None:
        """用户自己加的笔记和章节不能被程序弄丢。"""
        store = self.store()
        store.ensure_exists()
        self.path.write_text(
            store.read() + "\n## 我自己的备注\n\n这一节是手写的，程序不该碰。\n",
            encoding="utf-8",
        )

        store.write_section("当前水平", "能读懂简单的前向传播代码。")

        text = store.read()
        self.assertIn("## 我自己的备注", text)
        self.assertIn("这一节是手写的，程序不该碰。", text)

    def test_unknown_section_is_rejected(self) -> None:
        with self.assertRaises(ProfileError):
            self.store().write_section("最喜欢的颜色", "蓝色")

    def test_empty_content_is_rejected(self) -> None:
        with self.assertRaises(ProfileError):
            self.store().write_section("目标", "   \n  ")

    def test_too_long_content_is_rejected(self) -> None:
        with self.assertRaises(ProfileError):
            self.store().write_section("目标", "长" * (MAX_SECTION_CHARS + 1))

    def test_write_leaves_no_temp_file_behind(self) -> None:
        store = self.store()
        store.write_section("已掌握", "列表推导式、字典、函数。")

        leftovers = sorted(p.name for p in self.path.parent.iterdir())
        self.assertEqual(leftovers, ["profile.md"])

    def test_written_file_ends_with_newline(self) -> None:
        store = self.store()
        store.write_section("学习习惯", "喜欢先看例子再看定义。")
        self.assertTrue(store.read().endswith("\n"))


class ConcurrentWriteTest(unittest.TestCase):
    """并发写：这不是假想——SDK 把同步工具丢进线程池跑（`asyncio.to_thread`）。

    2026-09-24 的真实事故：模型一次要求调两个 `update_plan`，两个线程各自
    "读整份 → 改一节 → 写整份"，后写的那个把先写的改动整个盖掉。**工具返回了
    "已更新"，文件里却没有**——计划里"里程碑打勾"就是这么丢的。
    同一批实验还撞出过 WinError 32 和读文件读成 UnicodeDecodeError 两种情况。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "vault" / "profile.md"

    def _run_both(self, *calls) -> None:
        """同时跑几个写操作，并把线程里的异常收回来。

        线程里抛异常不会传给主线程——不收的话，这类测试会"假装通过"。
        """
        errors: list[BaseException] = []

        def guard(call) -> None:
            try:
                call()
            except BaseException as exc:  # noqa: BLE001 - 就是要全收回来
                errors.append(exc)

        threads = [threading.Thread(target=guard, args=(call,)) for call in calls]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        if errors:
            raise AssertionError(f"并发写抛异常了：{errors!r}")

    def test_parallel_section_writes_keep_both_changes(self) -> None:
        store = ProfileStore(self.path)
        store.ensure_exists()
        store.write_section("目标", "旧目标")
        store.write_section("薄弱点", "旧薄弱点")

        self._run_both(
            lambda: store.write_section("目标", "找一个 agent 实习"),
            lambda: store.write_section("薄弱点", "没写过工程项目"),
        )

        text = store.read()
        self.assertIn("找一个 agent 实习", text)
        self.assertIn("没写过工程项目", text)

    def test_parallel_writes_never_corrupt_the_file(self) -> None:
        """写坏比丢更新更糟：一读就 UnicodeDecodeError，她的记忆就没了。"""
        store = ProfileStore(self.path)
        store.ensure_exists()
        for _ in range(40):
            self._run_both(
                lambda: store.write_section("目标", "找一个 agent 实习"),
                lambda: store.write_section("薄弱点", "没写过工程项目"),
            )
            text = store.read()  # 读不出来就会炸
            self.assertIn("找一个 agent 实习", text)
            self.assertIn("没写过工程项目", text)

    def test_atomic_write_never_leaves_a_mixed_file(self) -> None:
        """临时文件名重复的话，两个写者会互相踩——结果可能是半截内容。"""
        path = Path(self._tmp.name) / "x.md"
        first, second = "甲" * 400, "乙" * 400
        for _ in range(40):
            self._run_both(
                lambda: atomic_write(path, first),
                lambda: atomic_write(path, second),
            )
            self.assertIn(path.read_text(encoding="utf-8"), (first, second))


if __name__ == "__main__":
    unittest.main()
