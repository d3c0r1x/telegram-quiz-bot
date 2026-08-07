"""Получение вопросов для викторины: OpenTDB API или встроенный оффлайн-пул.

OpenTDB (Open Trivia Database, https://opentdb.com) — бесплатный публичный API
без ключа. В демо-режиме (QUIZ_DEMO_MODE=1) используется встроенный пул —
бот работает даже без интернета.

Продвинутый уровень: фильтр по сложности (easy/medium/hard) — параметр
&difficulty= в запросе к OpenTDB и маркировка вопросов оффлайн-пула.
"""
from __future__ import annotations

import html
import random
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

import httpx
from pydantic import BaseModel

import config

# (вопрос, варианты, правильный ответ) — оффлайн-пул для демо-режима
DEMO_POOL: list[tuple[str, list[str], str]] = [
    ("Какая планета Солнечной системы самая большая?",
     ["Юпитер", "Сатурн", "Земля", "Марс"], "Юпитер"),
    ("Сколько месяцев в году имеют 28 дней?",
     ["Все 12", "Только февраль", "Один", "Ни одного"], "Все 12"),
    ("Какой язык программирования назван в честь комедийного шоу?",
     ["Python", "Java", "Cobol", "Ruby"], "Python"),
    ("Что означает аббревиатура HTTP?",
     ["HyperText Transfer Protocol", "High Tech Transfer Protocol",
      "HyperText Testing Protocol", "Home Text Transfer Protocol"],
     "HyperText Transfer Protocol"),
    ("Какой самый большой океан на Земле?",
     ["Тихий", "Атлантический", "Индийский", "Северный Ледовитый"], "Тихий"),
    ("Столица Австралии?",
     ["Канберра", "Сидней", "Мельбурн", "Перт"], "Канберра"),
    ("Сколько битов в одном байте?",
     ["8", "4", "16", "32"], "8"),
    ("Кто написал роман «Война и мир»?",
     ["Лев Толстой", "Фёдор Достоевский", "Антон Чехов", "Иван Тургенев"], "Лев Толстой"),
    ("Какой металл при комнатной температуре жидкий?",
     ["Ртуть", "Железо", "Свинец", "Алюминий"], "Ртуть"),
    ("В каком году человек впервые высадился на Луну?",
     ["1969", "1959", "1971", "1965"], "1969"),
    ("Сколько сторон у шестиугольника?",
     ["6", "5", "7", "8"], "6"),
    ("Какой химический символ у золота?",
     ["Au", "Ag", "Go", "Gd"], "Au"),
]


class Question(BaseModel):
    """Один вопрос викторины с перемешанными вариантами ответа."""

    text: str
    options: list[str]          # 4 варианта (перемешаны)
    correct_index: int          # индекс правильного варианта
    category: str = "General Knowledge"
    difficulty: str = "medium"


class QuizClient:
    """Загружает вопросы викторины. transport подменяется в тестах."""

    def __init__(
        self,
        *,
        demo_mode: bool | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.demo_mode = config.DEMO_MODE if demo_mode is None else demo_mode
        self._transport = transport

    async def fetch_questions(
        self, amount: int, difficulty: str = "any"
    ) -> list[Question]:
        if self.demo_mode:
            return self._demo_questions(amount, difficulty)
        url = self._build_url(amount, difficulty)
        async with httpx.AsyncClient(
            transport=self._transport, timeout=config.QUIZ_API_TIMEOUT
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            payload = resp.json()
        if payload.get("response_code") != 0 or not payload.get("results"):
            # response_code=1 означает «API перегружен» — откат на оффлайн-пул
            return self._demo_questions(amount, difficulty)
        return [self._parse(item) for item in payload["results"]]

    @staticmethod
    def _build_url(amount: int, difficulty: str) -> str:
        """Добавляет &difficulty= к базовому URL из конфига (если не 'any')."""
        url = config.QUIZ_API_URL.format(n=amount)
        if difficulty in ("easy", "medium", "hard"):
            parsed = urlparse(url)
            query = parse_qs(parsed.query, keep_blank_values=True)
            query["difficulty"] = [difficulty]
            parsed = parsed._replace(query=urlencode(query, doseq=True))
            url = urlunparse(parsed)
        return url

    @staticmethod
    def _parse(item: dict) -> Question:
        """Разбирает элемент ответа OpenTDB, снимает HTML-сущности, мешает варианты."""
        options = [html.unescape(str(o)) for o in item.get("incorrect_answers", [])]
        correct = html.unescape(str(item.get("correct_answer", "")))
        options.append(correct)
        random.shuffle(options)
        return Question(
            text=html.unescape(str(item.get("question", ""))),
            options=options,
            correct_index=options.index(correct),
            category=str(item.get("category", "General Knowledge")),
            difficulty=str(item.get("difficulty", "medium")),
        )

    @staticmethod
    def _demo_questions(amount: int, difficulty: str = "any") -> list[Question]:
        pool = list(DEMO_POOL)
        random.shuffle(pool)
        mark = difficulty if difficulty in ("easy", "medium", "hard") else "easy"
        return [
            Question(
                text=text,
                options=options,
                correct_index=options.index(correct),
                category="Demo",
                difficulty=mark,
            )
            for text, options, correct in pool[:amount]
        ]
