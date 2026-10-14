"""练习：出题 → 你答 → 批改 → 错题沉进复盘。

为什么值得做：Divana 原来只会讲和记，不会考。而"学过"和"能答出来"是两回事——
练习是整个学习闭环里唯一能暴露"以为会了"的那一环。

**设计取舍：不做第二个 agent。** 出题和批改都由她（主 agent）做，这个模块只负责
落盘和汇总。理由是题目和参考要点本来就在她视野里（她刚读完计划/笔记），再起一个
agent 只会多花一轮钱（实测每轮 4~6k token），不会更准。
对比一下：复盘和早报必须另起 agent——那两件事的素材**不在**她的视野里（横跨所有
会话、要按时间过滤）。判断标准始终是同一句：**是不是每轮都要用**。

纯逻辑（校验、统计、渲染错题）都在这里，能离线测；tools.py 只做胶水。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .storage import atomic_write
from .contracts import PracticeQuestionView, PracticeView

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PRACTICE_PATH = DATA_DIR / "practice.json"

# 一测最多几题。多了做不完，也说明想一次考太多——学习闭环靠的是频率不是长度。
MAX_QUESTIONS = 8

# 一道题最多几个参考要点。要点是给批改用的，太细会让"半对"变成"错"。
MAX_POINTS = 6

# 三种判定。故意只有三档：再多她就要纠结"给 6 分还是 7 分"，
# 而这对学习者没有任何帮助。
VERDICTS = ("对", "半对", "错")


class PracticeError(RuntimeError):
    """可预期的失败：还没出题、编号越界、判定不在三档里。"""


@dataclass(frozen=True)
class Question:
    """一道题。"""

    prompt: str
    points: tuple[str, ...] = ()
    """参考要点。**批改时拿来对照的**，不必主动念给用户（念了就等于给答案）。"""

    source: str = ""
    """这题从哪来的：哪篇笔记、计划里的哪一条。让"考的确实是他学过的"可核对。"""


@dataclass(frozen=True)
class Quiz:
    topic: str
    questions: tuple[Question, ...]
    created_at: str = ""


@dataclass(frozen=True)
class Answer:
    """一道题的判定。"""

    index: int
    """第几题，从 1 开始。"""

    verdict: str
    comment: str = ""
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class Score:
    total: int
    right: int
    half: int
    wrong: int

    @property
    def percent(self) -> int:
        if not self.total:
            return 0
        # 半对按一半算：这样"都答了一半"不会和"全答对"同分
        earned = self.right + self.half / 2
        return round(100 * earned / self.total)


@dataclass
class QuizState:
    """当前这一测：题目 + 判定。判定为空表示还没批改。"""

    quiz: Quiz
    answers: tuple[Answer, ...] = field(default_factory=tuple)

    @property
    def graded(self) -> bool:
        return bool(self.answers)


def check_quiz(topic: str, questions: list[Question]) -> Quiz:
    """校一遍再存：宁可当场报错，也不要存一份考不出东西的题。"""
    topic = topic.strip()
    if not topic:
        raise PracticeError("出题要写清主题，不然复盘时不知道这次考的是什么")
    if not questions:
        raise PracticeError("一道题都没有")
    if len(questions) > MAX_QUESTIONS:
        raise PracticeError(f"题太多了：一次最多 {MAX_QUESTIONS} 道")

    cleaned: list[Question] = []
    for index, question in enumerate(questions, 1):
        prompt = question.prompt.strip()
        if not prompt:
            raise PracticeError(f"第 {index} 题没有题干")
        points = tuple(point.strip() for point in question.points if point.strip())
        if not points:
            # 没有要点就没法批改——她只能凭印象给分，那正是要避免的
            raise PracticeError(f"第 {index} 题没给参考要点，批改时没法对照")
        if len(points) > MAX_POINTS:
            points = points[:MAX_POINTS]
        cleaned.append(
            Question(prompt=prompt, points=points, source=question.source.strip())
        )
    return Quiz(
        topic=topic,
        questions=tuple(cleaned),
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


def check_answers(quiz: Quiz, answers: list[Answer]) -> tuple[Answer, ...]:
    """校一遍判定：编号要在题号范围内，判定只能是那三档。"""
    if not quiz.questions:
        raise PracticeError("还没有题目，先出题")
    if not answers:
        raise PracticeError("没有判定——批改的结果呢？")

    seen: set[int] = set()
    cleaned: list[Answer] = []
    for answer in answers:
        if not 1 <= answer.index <= len(quiz.questions):
            raise PracticeError(
                f"第 {answer.index} 题不存在（这次一共 {len(quiz.questions)} 题）"
            )
        if answer.index in seen:
            raise PracticeError(f"第 {answer.index} 题判了两次")
        if answer.verdict not in VERDICTS:
            raise PracticeError(
                f"判定只能是 {'、'.join(VERDICTS)}，收到的是「{answer.verdict}」"
            )
        seen.add(answer.index)
        cleaned.append(
            Answer(
                index=answer.index,
                verdict=answer.verdict,
                comment=answer.comment.strip(),
                missing=tuple(item.strip() for item in answer.missing if item.strip()),
            )
        )
    return tuple(sorted(cleaned, key=lambda item: item.index))


def score(quiz: Quiz, answers: tuple[Answer, ...]) -> Score:
    """数一数：对几个、半对几个、错几个。"""
    right = sum(1 for item in answers if item.verdict == "对")
    half = sum(1 for item in answers if item.verdict == "半对")
    wrong = sum(1 for item in answers if item.verdict == "错")
    return Score(total=len(quiz.questions), right=right, half=half, wrong=wrong)


def render_quiz(quiz: Quiz) -> str:
    """给她看的题目清单（批改前不该带上要点——那等于把答案念出来）。"""
    lines = [f"这一测：{quiz.topic}（{len(quiz.questions)} 题）", ""]
    for index, question in enumerate(quiz.questions, 1):
        lines.append(f"{index}. {question.prompt}")
    return "\n".join(lines)


def render_review_entry(quiz: Quiz, answers: tuple[Answer, ...]) -> str:
    """把这次自测汇总成一段，追加进学习计划的"复盘记录"。

    只写**没掌握的**：全对的题写进去只是噪音，复盘要能一眼看出该补哪儿。
    （全对时也留一行——"这一测全对"本身是值得记的进展。）
    """
    stat = score(quiz, answers)
    lines = [
        f"### 自测：{quiz.topic}",
        "",
        f"{stat.total} 题：对 {stat.right}、半对 {stat.half}、错 {stat.wrong}"
        f"（{stat.percent} 分）。",
    ]

    weak = [item for item in answers if item.verdict != "对"]
    if not weak:
        lines += ["", "这一测全对。"]
        return "\n".join(lines)

    lines += ["", "没掌握的："]
    for answer in weak:
        question = quiz.questions[answer.index - 1]
        header = f"- **第 {answer.index} 题（{question.prompt}）**"
        # 半对和错要分开说：一个"差一点"，一个"没答上"，补救方式完全不同
        lines.append(f"{header}：{answer.verdict}")
        if answer.missing:
            lines.append(f"    - 漏了：{'、'.join(answer.missing)}")
        if answer.comment:
            lines.append(f"    - {answer.comment}")
    return "\n".join(lines)


def build_view(store: PracticeStore) -> PracticeView:
    """把"当前这一测"整理成页面要的形状。

    没有题目时返回**空 view**（不是报错）：页面第一句话该是"去对话里说『考考我』"。
    注意返回里**不含参考要点**——那是批改用的，发到页面上等于给答案。
    """
    try:
        state = store.load()
    except PracticeError:
        return PracticeView()

    judged = {item.index: item for item in state.answers}
    questions = tuple(
        PracticeQuestionView(
            index=index,
            prompt=question.prompt,
            verdict=judged[index].verdict if index in judged else "",
            comment=judged[index].comment if index in judged else "",
            missing=judged[index].missing if index in judged else (),
        )
        for index, question in enumerate(state.quiz.questions, 1)
    )
    stat = score(state.quiz, state.answers)
    return PracticeView(
        has_quiz=True,
        topic=state.quiz.topic,
        created_at=state.quiz.created_at,
        graded=state.graded,
        questions=questions,
        right=stat.right,
        half=stat.half,
        wrong=stat.wrong,
        percent=stat.percent,
    )


class PracticeStore:
    """当前这一测存哪儿。路径可注入，评测才能用临时目录。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else PRACTICE_PATH

    def has_quiz(self) -> bool:
        return self.path.exists()

    def save_quiz(self, quiz: Quiz) -> None:
        """存新一份题。**会清掉上一份的判定**——开了新一测，旧的判定不该跟着走。"""
        self._write(QuizState(quiz=quiz))

    def save_answers(self, answers: tuple[Answer, ...]) -> None:
        state = self.load()
        self._write(QuizState(quiz=state.quiz, answers=answers))

    def load(self) -> QuizState:
        if not self.path.exists():
            raise PracticeError("还没有出过题（先让她出一道）")
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PracticeError(f"练习记录读不出来：{exc}") from exc

        quiz_raw = raw.get("quiz") or {}
        questions = tuple(
            Question(
                prompt=str(item.get("prompt", "")),
                points=tuple(str(point) for point in item.get("points", [])),
                source=str(item.get("source", "")),
            )
            for item in quiz_raw.get("questions", [])
        )
        quiz = Quiz(
            topic=str(quiz_raw.get("topic", "")),
            questions=questions,
            created_at=str(quiz_raw.get("created_at", "")),
        )
        answers = tuple(
            Answer(
                index=int(item.get("index", 0)),
                verdict=str(item.get("verdict", "")),
                comment=str(item.get("comment", "")),
                missing=tuple(str(one) for one in item.get("missing", [])),
            )
            for item in raw.get("answers", [])
        )
        return QuizState(quiz=quiz, answers=answers)

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return

    def _write(self, state: QuizState) -> None:
        payload = {
            "quiz": {
                "topic": state.quiz.topic,
                "created_at": state.quiz.created_at,
                "questions": [
                    {
                        "prompt": item.prompt,
                        "points": list(item.points),
                        "source": item.source,
                    }
                    for item in state.quiz.questions
                ],
            },
            "answers": [
                {
                    "index": item.index,
                    "verdict": item.verdict,
                    "comment": item.comment,
                    "missing": list(item.missing),
                }
                for item in state.answers
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
