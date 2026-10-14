"""练习的离线测试：校验、统计、渲染错题、落盘。

这些都不联网、不碰模型——所以能天天跑。

用法：python -m unittest tests.test_practice -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from divana.practice import (
    MAX_QUESTIONS,
    Answer,
    PracticeError,
    PracticeStore,
    Question,
    build_view,
    check_answers,
    check_quiz,
    render_quiz,
    render_review_entry,
    score,
)


def quiz(questions=None, topic="RAG 基础"):
    return check_quiz(topic, questions or [Question("切分为什么要重叠？", ("边界处语义会被切断",))])


class CheckQuizTest(unittest.TestCase):
    def test_topic_is_required(self) -> None:
        with self.assertRaises(PracticeError) as ctx:
            check_quiz("   ", [Question("题", ("点",))])
        self.assertIn("主题", str(ctx.exception))

    def test_at_least_one_question(self) -> None:
        with self.assertRaises(PracticeError):
            check_quiz("RAG", [])

    def test_too_many_questions_is_rejected(self) -> None:
        many = [Question(f"第 {i} 题", ("点",)) for i in range(MAX_QUESTIONS + 1)]
        with self.assertRaises(PracticeError) as ctx:
            check_quiz("RAG", many)
        self.assertIn("最多", str(ctx.exception))

    def test_empty_prompt_is_rejected(self) -> None:
        with self.assertRaises(PracticeError) as ctx:
            check_quiz("RAG", [Question("   ", ("点",))])
        self.assertIn("题干", str(ctx.exception))

    def test_question_without_points_is_rejected(self) -> None:
        """没有要点就没法批改——她只能凭印象给分，那正是要避免的。"""
        with self.assertRaises(PracticeError) as ctx:
            check_quiz("RAG", [Question("切分为什么要重叠？", ())])
        self.assertIn("参考要点", str(ctx.exception))

    def test_extra_points_are_trimmed(self) -> None:
        made = check_quiz("RAG", [Question("题", tuple(f"点{i}" for i in range(9)))])
        self.assertEqual(len(made.questions[0].points), 6)

    def test_it_records_when_the_quiz_was_made(self) -> None:
        self.assertTrue(quiz().created_at)


class CheckAnswersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.quiz = quiz([Question("一", ("甲",)), Question("二", ("乙",))])

    def test_index_out_of_range_is_rejected(self) -> None:
        with self.assertRaises(PracticeError) as ctx:
            check_answers(self.quiz, [Answer(index=3, verdict="对")])
        self.assertIn("不存在", str(ctx.exception))

    def test_same_question_graded_twice_is_rejected(self) -> None:
        with self.assertRaises(PracticeError):
            check_answers(
                self.quiz, [Answer(index=1, verdict="对"), Answer(index=1, verdict="错")]
            )

    def test_verdict_must_be_one_of_three(self) -> None:
        with self.assertRaises(PracticeError) as ctx:
            check_answers(self.quiz, [Answer(index=1, verdict="基本正确")])
        self.assertIn("半对", str(ctx.exception))

    def test_empty_answers_is_rejected(self) -> None:
        with self.assertRaises(PracticeError):
            check_answers(self.quiz, [])

    def test_answers_come_back_sorted(self) -> None:
        made = check_answers(
            self.quiz, [Answer(index=2, verdict="错"), Answer(index=1, verdict="对")]
        )
        self.assertEqual([item.index for item in made], [1, 2])


class ScoreTest(unittest.TestCase):
    def test_counts_and_percent(self) -> None:
        made = quiz([Question(f"题{i}", ("点",)) for i in range(4)])
        answers = check_answers(
            made,
            [
                Answer(index=1, verdict="对"),
                Answer(index=2, verdict="对"),
                Answer(index=3, verdict="半对"),
                Answer(index=4, verdict="错"),
            ],
        )
        stat = score(made, answers)
        self.assertEqual((stat.total, stat.right, stat.half, stat.wrong), (4, 2, 1, 1))
        # 2 + 0.5 = 2.5 / 4 → 62.5 → 62（半对按一半算，否则"都答一半"和"全对"同分）
        self.assertEqual(stat.percent, 62)

    def test_percent_of_empty_is_zero(self) -> None:
        from divana.practice import Score

        self.assertEqual(Score(total=0, right=0, half=0, wrong=0).percent, 0)


class RenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.quiz = quiz(
            [Question("切分为什么要重叠？", ("边界处语义会被切断",), "笔记：切分"),
             Question("检索为什么先粗排？", ("全量精排太贵",))],
            topic="RAG 基础",
        )

    def test_quiz_list_never_leaks_the_reference_points(self) -> None:
        """题目清单里带上要点就等于把答案念出来了——这是最容易犯的错。"""
        text = render_quiz(self.quiz)
        self.assertIn("切分为什么要重叠？", text)
        self.assertNotIn("边界处语义会被切断", text)

    def test_review_entry_lists_only_what_was_missed(self) -> None:
        answers = check_answers(
            self.quiz,
            [
                Answer(index=1, verdict="对"),
                Answer(index=2, verdict="半对", comment="方向对但没说成本", missing=("全量精排太贵",)),
            ],
        )
        text = render_review_entry(self.quiz, answers)
        self.assertIn("### 自测：RAG 基础", text)
        self.assertIn("2 题：对 1、半对 1、错 0（75 分）", text)
        self.assertIn("第 2 题", text)
        self.assertIn("漏了：全量精排太贵", text)
        # 答对的那道不该出现——复盘里只留没掌握的
        self.assertNotIn("切分为什么要重叠", text)

    def test_all_correct_says_so(self) -> None:
        answers = check_answers(
            self.quiz,
            [Answer(index=1, verdict="对"), Answer(index=2, verdict="对")],
        )
        text = render_review_entry(self.quiz, answers)
        self.assertIn("这一测全对", text)


class BuildViewTest(unittest.TestCase):
    """练习页要的形状：把"当前这一测"整理成页面能直接渲染的数据。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = PracticeStore(Path(self._tmp.name) / "practice.json")

    def test_no_quiz_gives_an_empty_view_not_an_error(self) -> None:
        """还没出题时页面该说"去对话里说考考我"，不是一屏红色错误。"""
        view = build_view(self.store)
        self.assertFalse(view.has_quiz)
        self.assertEqual(view.total, 0)
        self.assertEqual(view.percent, 0)

    def test_ungraded_quiz_lists_the_questions(self) -> None:
        self.store.save_quiz(quiz([Question("一", ("甲",)), Question("二", ("乙",))]))
        view = build_view(self.store)
        self.assertTrue(view.has_quiz)
        self.assertEqual([item.prompt for item in view.questions], ["一", "二"])
        self.assertEqual([item.verdict for item in view.questions], ["", ""])
        self.assertFalse(view.graded)

    def test_view_never_carries_the_reference_points(self) -> None:
        """要点是批改对照用的，发到页面上等于把答案给他。"""
        self.store.save_quiz(quiz([Question("一", ("甲要点", "乙要点"))]))
        view = build_view(self.store)
        for item in view.questions:
            self.assertFalse(hasattr(item, "points"))
        self.assertNotIn("甲要点", repr(view))

    def test_graded_view_carries_verdicts_and_score(self) -> None:
        made = quiz([Question("一", ("甲",)), Question("二", ("乙",))])
        self.store.save_quiz(made)
        self.store.save_answers(
            check_answers(
                made,
                [
                    Answer(index=1, verdict="对"),
                    Answer(index=2, verdict="错", comment="完全答反了", missing=("乙",)),
                ],
            )
        )
        view = build_view(self.store)
        self.assertTrue(view.graded)
        self.assertEqual((view.right, view.half, view.wrong), (1, 0, 1))
        self.assertEqual(view.percent, 50)
        self.assertEqual(view.questions[1].verdict, "错")
        self.assertEqual(view.questions[1].missing, ("乙",))
        self.assertEqual(view.questions[1].comment, "完全答反了")


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = PracticeStore(Path(self._tmp.name) / "practice.json")

    def test_nothing_saved_yet(self) -> None:
        self.assertFalse(self.store.has_quiz())
        with self.assertRaises(PracticeError) as ctx:
            self.store.load()
        self.assertIn("出过题", str(ctx.exception))

    def test_roundtrip(self) -> None:
        made = quiz([Question("一", ("甲", "乙"), "笔记：X")])
        self.store.save_quiz(made)
        state = self.store.load()
        self.assertEqual(state.quiz.topic, made.topic)
        self.assertEqual(state.quiz.questions[0].points, ("甲", "乙"))
        self.assertEqual(state.quiz.questions[0].source, "笔记：X")
        self.assertFalse(state.graded)

    def test_saving_answers_keeps_the_quiz(self) -> None:
        made = quiz()
        self.store.save_quiz(made)
        self.store.save_answers(check_answers(made, [Answer(index=1, verdict="错")]))
        state = self.store.load()
        self.assertTrue(state.graded)
        self.assertEqual(state.answers[0].verdict, "错")
        self.assertEqual(state.quiz.topic, made.topic)

    def test_new_quiz_drops_the_old_verdicts(self) -> None:
        """开了新一测，上一次的判定不该跟着走——否则成绩会串。"""
        made = quiz()
        self.store.save_quiz(made)
        self.store.save_answers(check_answers(made, [Answer(index=1, verdict="对")]))
        self.store.save_quiz(quiz(topic="Transformer"))
        state = self.store.load()
        self.assertFalse(state.graded)
        self.assertEqual(state.quiz.topic, "Transformer")

    def test_broken_file_reports_clearly(self) -> None:
        self.store.path.parent.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text("{ 这不是 json", encoding="utf-8")
        with self.assertRaises(PracticeError) as ctx:
            self.store.load()
        self.assertIn("读不出来", str(ctx.exception))

    def test_clear(self) -> None:
        self.store.save_quiz(quiz())
        self.store.clear()
        self.assertFalse(self.store.has_quiz())
        self.store.clear()  # 再删一次不该炸

    def test_saved_file_is_readable_json(self) -> None:
        """落盘的是给人看也看得懂的 JSON——排查问题时能直接打开。"""
        self.store.save_quiz(quiz())
        raw = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertIn("quiz", raw)
        self.assertEqual(raw["quiz"]["questions"][0]["prompt"], "切分为什么要重叠？")


if __name__ == "__main__":
    unittest.main()
