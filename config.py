"""Конфигурация Telegram Quiz Bot через переменные окружения."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BOT_TOKEN = os.getenv("QUIZ_BOT_TOKEN", "")
DB_PATH = os.getenv("QUIZ_DB_PATH", os.path.join(BASE_DIR, "quiz.db"))

# 1 = оффлайн-режим (встроенный пул вопросов, сеть не нужна) | 0 = OpenTDB API
DEMO_MODE = os.getenv("QUIZ_DEMO_MODE", "1") == "1"

QUESTIONS_PER_GAME = int(os.getenv("QUIZ_QUESTIONS_PER_GAME", "10"))

# Бесплатный публичный API без ключа: https://opentdb.com/api_config.php
QUIZ_API_URL = os.getenv(
    "QUIZ_API_URL",
    "https://opentdb.com/api.php?amount={n}&type=multiple",
)
QUIZ_API_TIMEOUT = float(os.getenv("QUIZ_API_TIMEOUT", "10"))
