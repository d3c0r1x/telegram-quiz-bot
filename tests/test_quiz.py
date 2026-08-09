"""Unit-тесты Quiz-бота: парсинг вопросов, сложность, пагинация, статистика."""
import asyncio

import httpx

from bot import paginate
from db import Database
from quiz_api import DEMO_POOL, Question, QuizClient

SAMPLE_RESULT = {
    "response_code": 0,
    "results": [
        {
            "type": "multiple",
            "difficulty": "hard",
            "category": "Science",
            "question": "What is H2O?",
            "correct_answer": "Water",
            "incorrect_answers": ["Fire", "Air", "Earth"],
        }
    ],
}


class FakeHTTPXTransport(httpx.AsyncBaseTransport):
    """Подмена транспорта: отдаёт payload и запоминает URL запросов."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.urls: list[str] = []

    async def handle_async_request(self, request):
        self.urls.append(str(request.url))
        return httpx.Response(200, json=self.payload, request=request)


def test_demo_pool_has_enough_questions() -> None:
    assert len(DEMO_POOL) >= 10


def test_demo_questions_shape_and_difficulty() -> None:
    questions = QuizClient(demo_mode=True)._demo_questions(10, "hard")
    assert len(questions) == 10
    for q in questions:
        assert isinstance(q, Question)
        assert len(q.options) == 4
        assert 0 <= q.correct_index < 4
        assert len(set(q.options)) == 4
        assert q.difficulty == "hard"  # сложность маркируется как запрошена


def test_difficulty_added_to_opentdb_url() -> None:
    """&difficulty=hard добавляется в URL OpenTDB; 'any' — без параметра."""
    async def run() -> None:
        client = QuizClient(demo_mode=False)
        transport = FakeHTTPXTransport(SAMPLE_RESULT)
        client._transport = transport

        await client.fetch_questions(10, "hard")
        assert "difficulty=hard" in transport.urls[0]

        transport.urls.clear()
        await client.fetch_questions(10, "any")
        assert "difficulty=" not in transport.urls[0]

    asyncio.run(run())


def test_parse_opentdb_payload() -> None:
    q = QuizClient._parse(SAMPLE_RESULT["results"][0])
    assert q.text == "What is H2O?"
    assert q.difficulty == "hard"
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


def test_leaderboard_and_full_stats(tmp_path) -> None:
    async def run() -> None:
        db = Database(str(tmp_path / "quiz.db"))
        await db.init()
        await db.save_result(1, "alice", 8, 10, "easy")
        await db.save_result(2, "bob", 5, 10, "medium")
        await db.save_result(1, "alice", 10, 10, "hard")  # вторая игра
        rows = await db.leaderboard()
        assert rows[0] == ("alice", 10)
        assert await db.stats(1) == (2, 10)
        full = await db.full_stats(1)
        assert full["games_played"] == 2
        assert full["total_correct"] == 18
        assert full["accuracy"] == 90.0  # 18/20
        assert len(full["recent"]) == 2
        assert full["recent"][0]["difficulty"] == "hard"
        assert await db.full_stats(999) is None

    asyncio.run(run())


def test_parse_answer_callback() -> None:
    from bot import parse_answer_callback

    assert parse_answer_callback("quiz:123:abcd1234:0:2") == (123, "abcd1234", 0, 2)
    assert parse_answer_callback("quiz:123:abcd1234:3:1") == (123, "abcd1234", 3, 1)
    # старый формат без индекса вопроса — мусорные данные
    assert parse_answer_callback("quiz:123:abcd1234:2") is None
    assert parse_answer_callback("quiz:abc:abcd1234:0:2") is None   # id не число
    assert parse_answer_callback("quiz:123:abcd1234:x:2") is None   # индекс не число
    assert parse_answer_callback("quiz:123:abcd1234:0:y") is None   # ответ не число
    assert parse_answer_callback("junk") is None


def test_paginate_helper() -> None:
    items = list(range(12))
    page1, total, prev, nxt = paginate(items, 1, size=5)
    assert page1 == [0, 1, 2, 3, 4] and total == 3 and not prev and nxt
    page3, _, prev, nxt = paginate(items, 3, size=5)
    assert page3 == [10, 11] and prev and not nxt
    page99, _, prev, nxt = paginate(items, 99, size=5)  # за границей — последняя
    assert page99 == [10, 11] and not nxt
    empty, total, prev, nxt = paginate([], 1)
    assert empty == [] and total == 1 and not prev and not nxt
