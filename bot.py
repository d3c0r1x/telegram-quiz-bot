"""Telegram Quiz Bot (aiogram v3).

Стек: aiogram v3 (Telegram Bot API) + httpx (OpenTDB) + aiosqlite (рекорды)
+ pydantic (модель вопроса).

Команды:
  /quiz           — начать викторину (10 вопросов, инлайн-кнопки)
  /leaderboard    — топ-10 игроков
  /stats          — моя статистика (игр сыграно, лучший результат)

Запуск:  python bot.py   (задайте QUIZ_BOT_TOKEN, или используйте run_bot5.cmd).
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import os
import uuid

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
from db import Database
from quiz_api import Question, QuizClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(config.BASE_DIR, "bot.log"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

router = Router()
db = Database(config.DB_PATH)
quiz = QuizClient()

# user_id -> активная игра: {"questions": [...], "index": int, "score": int, "nonce": str}
_games: dict[int, dict] = {}


def _game_keyboard(question: Question, game_id: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for idx, option in enumerate(question.options):
        kb.button(text=f"{idx + 1}. {option[:60]}", callback_data=f"quiz:{game_id}:{idx}")
    kb.adjust(1)
    return kb.as_markup()


def _question_text(question: Question, index: int, total: int) -> str:
    return (
        f"❓ <b>Вопрос {index}/{total}</b>\n"
        f"[{_html.escape(question.category)} · {_html.escape(question.difficulty)}]\n\n"
        f"{_html.escape(question.text)}"
    )


async def _send_question(message: Message, user_id: int, game_id: str) -> None:
    game = _games[user_id]
    q = game["questions"][game["index"]]
    await message.answer(
        _question_text(q, game["index"] + 1, len(game["questions"])),
        reply_markup=_game_keyboard(q, game_id),
    )


async def _finish(message: Message, user_id: int) -> None:
    game = _games.pop(user_id)
    total = len(game["questions"])
    await db.save_result(user_id, message.from_user.username, game["score"])
    ratio = game["score"] / total if total else 0
    emoji = "🏆" if ratio >= 0.8 else "👍" if ratio >= 0.5 else "💪"
    await message.answer(
        f"{emoji} <b>Игра окончена!</b>\n"
        f"Ваш результат: <b>{game['score']} / {total}</b>\n\n"
        "/leaderboard — посмотреть топ игроков"
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "🎓 <b>Telegram Quiz Bot</b>\n\n"
        "/quiz — начать викторину\n"
        "/leaderboard — топ-10 игроков\n"
        "/stats — моя статистика\n\n"
        f"Источник вопросов: <b>{'оффлайн-пул' if quiz.demo_mode else 'OpenTDB API'}</b>"
    )


@router.message(Command("quiz"))
async def cmd_quiz(message: Message) -> None:
    user_id = message.from_user.id
    try:
        questions = await quiz.fetch_questions(config.QUESTIONS_PER_GAME)
    except Exception:
        logger.exception("Не удалось загрузить вопросы")
        await message.answer("⚠️ Не удалось загрузить вопросы. Попробуйте позже: /quiz")
        return
    # nonce отличает эту игру от прошлых: кнопки старой игры не сработают
    nonce = uuid.uuid4().hex[:8]
    _games[user_id] = {"questions": questions, "index": 0, "score": 0, "nonce": nonce}
    await _send_question(message, user_id, f"{user_id}:{nonce}")


@router.callback_query(F.data.startswith("quiz:"))
async def on_answer(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    # формат quiz:{user_id}:{nonce}:{idx}; мусорные данные игнорируем
    if len(parts) != 4 or not parts[1].isdigit() or not parts[3].isdigit():
        await callback.answer("Устаревшая кнопка.", show_alert=False)
        return
    _, user_id_str, nonce, answer_idx = parts
    user_id = callback.from_user.id
    game = _games.get(user_id)
    # чужие кнопки и кнопки от ПРОШЛОЙ игры того же игрока — недействительны
    if user_id_str != str(user_id) or game is None or nonce != game.get("nonce"):
        await callback.answer(
            "Эта кнопка устарела — начните новую игру: /quiz", show_alert=True
        )
        return
    if callback.message is None:
        await callback.answer("Сообщение недоступно.", show_alert=True)
        return
    q = game["questions"][game["index"]]
    correct = int(answer_idx) == q.correct_index
    if correct:
        game["score"] += 1
    game["index"] += 1
    verdict = (
        "✅ Верно!"
        if correct
        else f"❌ Ошибка. Правильный ответ: <b>{_html.escape(q.options[q.correct_index])}</b>"
    )
    await callback.message.edit_text(
        f"{_html.escape(q.text)}\n\n{verdict}\n"
        f"Счёт: <b>{game['score']}</b> / {game['index']}",
        reply_markup=None,
    )
    await callback.answer()
    if game["index"] >= len(game["questions"]):
        await _finish(callback.message, user_id)
    else:
        await _send_question(callback.message, user_id, f"{user_id}:{nonce}")


@router.message(Command("leaderboard"))
async def cmd_leaderboard(message: Message) -> None:
    rows = await db.leaderboard()
    if not rows:
        await message.answer("Пока нет результатов. Сыграйте первую игру: /quiz")
        return
    lines = [
        f"{i}. {_html.escape(name)} — <b>{score}</b>"
        for i, (name, score) in enumerate(rows, 1)
    ]
    await message.answer("🏆 <b>Топ игроков</b>\n\n" + "\n".join(lines))


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    stats = await db.stats(message.from_user.id)
    if not stats:
        await message.answer("Вы ещё не играли. Начните: /quiz")
        return
    games, best = stats
    await message.answer(
        f"📊 <b>Ваша статистика</b>\n\n"
        f"Игр сыграно: <b>{games}</b>\n"
        f"Лучший результат: <b>{best}</b>"
    )


async def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("Не задан QUIZ_BOT_TOKEN. Скопируйте .env.example и задайте токен.")
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await db.init()
    logger.info(
        "Quiz-бот запущен. Режим вопросов: %s",
        "оффлайн-пул" if quiz.demo_mode else "OpenTDB API",
    )
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
