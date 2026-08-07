"""Unit-тесты Quiz-бота: парсинг вопросов, оффлайн-пул, лидерборд."""
import asyncio

from db import Database
from quiz_api import DEMO_POOL, Question, QuizClient


def test_demo_pool_has_enough_questions() -> None:
    assert len(DEMO_POOL) >= 10


def test_demo_questions_shape() -> None:
    questions = QuizClient(demo_mode=True)._demo_questions(10)
    assert len(questions) == 10
    for q in questions:
        assert isinstance(q, Question)
        assert len(q.options) == 4
        assert 0 <= q.correct_index < 4
        assert len(set(q.options)) == 4  # варианты не дублируются


def test_parse_opentdb_payload() -> None:
    raw = {
        "response_code": 0,
        "results": [
            {
                "type": "multiple",
                "difficulty": "easy",
                "category": "Science",
                "question": "What is H2O?",
                "correct_answer": "Water",
                "incorrect_answers": ["Fire", "Air", "Earth"],
            }
        ],
    }
    q = QuizClient._parse(raw["results"][0])
    assert q.text == "What is H2O?"
    assert q.category == "Science"
    assert len(q.options) == 4
    assert q.options[q.correct_index] == "Water"


def test_html_unescape_in_questions() -> None:
    raw = {
        "question": "A &quot;quoted&quot; one?",
        "correct_answer": "Yes",
        "incorrect_answers": ["No", "Maybe", "Never"],
    }
    q = QuizClient._parse(raw)
    assert q.text == 'A "quoted" one?'


def test_leaderboard_and_stats(tmp_path) -> None:
    async def run() -> None:
        # файловая БД: у aiosqlite каждый connect(":memory:") даёт новую базу
        db = Database(str(tmp_path / "quiz.db"))
        await db.init()
        await db.save_result(1, "alice", 8)
        await db.save_result(2, "bob", 5)
        await db.save_result(1, "alice", 10)  # вторая игра: +1, лучший = 10
        rows = await db.leaderboard()
        assert rows[0] == ("alice", 10)
        assert rows[1] == ("bob", 5)
        assert await db.stats(1) == (2, 10)
        assert await db.stats(999) is None

    asyncio.run(run())
