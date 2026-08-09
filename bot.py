"""Telegram Quiz Bot (aiogram v3).

Стек: aiogram v3 (Telegram Bot API) + httpx (OpenTDB) + aiosqlite (рекорды)
+ pydantic (модель вопроса).

Команды:
  /quiz           — выбрать сложность и начать викторину (10 вопросов)
  /leaderboard    — топ игроков (с пагинацией по страницам)
  /stats          — моя статистика (игры, лучший, точность, последние 5)

Продвинутый уровень:
  - выбор сложности инлайн-кнопками (easy/medium/hard/any);
  - nonce игры защищает от «прострела» кнопок старой игры;
  - брошенные игры вычищаются (GAME_TTL_SECONDS);
  - лидерборд с пагинацией, статистика с точностью в %.

Запуск:  python bot.py   (задайте QUIZ_BOT_TOKEN, или используйте run_bot5.cmd).
"""
from __future__ import annotations

import asyncio
import html as _html
import logging
import os
import time
import uuid

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
from db import Database
from middlewares import LoggingMiddleware, ThrottlingMiddleware
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

# user_id -> активная игра
# {"questions": [...], "index": int, "score": int, "nonce": str,
#  "difficulty": str, "started_at": float}
_games: dict[int, dict] = {}
# user_id -> страница лидерборда (для пагинации)
_lb_page: dict[int, int] = {}

LB_PAGE_SIZE = 5


# ------------------------------------------------------------ пагинация (чистая)

def paginate(items: list, page: int, size: int = LB_PAGE_SIZE) -> tuple[list, int, bool, bool]:
    """(слайс, всего_страниц, есть_предыдущая, есть_следующая) — чистая функция."""
    if not items:
        return [], 1, False, False
    total_pages = (len(items) + size - 1) // size
    page = max(1, min(page, total_pages))
    start = (page - 1) * size
    return items[start : start + size], total_pages, page > 1, page < total_pages


def _lb_keyboard(user_id: int, page: int, total_pages: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if page > 1:
        kb.button(text="◀️", callback_data=f"lb:{user_id}:{page - 1}")
    kb.button(text=f"{page}/{total_pages}", callback_data="lb:none")
    if page < total_pages:
        kb.button(text="▶️", callback_data=f"lb:{user_id}:{page + 1}")
    kb.adjust(3)
    return kb.as_markup()


# ------------------------------------------------------------ игровые механики

def _difficulty_keyboard() -> InlineKeyboardMarkup:
    labels = {
        "any": "🎲 Любая",
        "easy": "🟢 Лёгкая",
        "medium": "🟡 Средняя",
        "hard": "🔴 Сложная",
    }
    kb = InlineKeyboardBuilder()
    for diff in config.DIFFICULTIES:
        kb.button(text=labels[diff], callback_data=f"diff:{diff}")
    kb.adjust(2)
    return kb.as_markup()


def parse_answer_callback(data: str) -> tuple[int, str, int, int] | None:
    """Разбирает 'quiz:{user_id}:{nonce}:{question_index}:{answer}'.

    Индекс вопроса в callback-данных защищает от повторного клика по
    кнопке уже отвеченного вопроса (иначе ответ засчитывался бы
    следующему вопросу). None — мусорные/старые данные.
    """
    parts = data.split(":")
    if (
        len(parts) != 5
        or not parts[1].isdigit()
        or not parts[3].isdigit()
        or not parts[4].isdigit()
    ):
        return None
    return int(parts[1]), parts[2], int(parts[3]), int(parts[4])


def _game_keyboard(question: Question, game_id: str, qindex: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for idx, option in enumerate(question.options):
        kb.button(
            text=f"{idx + 1}. {option[:60]}",
            callback_data=f"quiz:{game_id}:{qindex}:{idx}",
        )
    kb.adjust(1)
    return kb.as_markup()


def _purge_stale_games() -> None:
    """Вычищает брошенные игры (старше GAME_TTL_SECONDS) — продвинутый приём."""
    cutoff = time.monotonic() - config.GAME_TTL_SECONDS
    stale = [uid for uid, g in _games.items() if g.get("started_at", 0) < cutoff]
    for uid in stale:
        logger.info("Очищена брошенная игра пользователя %s", uid)
        _games.pop(uid, None)


def _question_text(question: Question, index: int, total: int) -> str:
    return (
        f"❓ <b>Вопрос {index}/{total}</b>\n"
        f"[{_html.escape(question.category)} · {_html.escape(question.difficulty)}]\n\n"
        f"{_html.escape(question.text)}"
    )


async def _start_game(message: Message, user_id: int, difficulty: str) -> None:
    try:
        questions = await quiz.fetch_questions(config.QUESTIONS_PER_GAME, difficulty)
    except Exception:
        logger.exception("Не удалось загрузить вопросы")
        await message.answer("⚠️ Не удалось загрузить вопросы. Попробуйте позже: /quiz")
        return
    if not questions:
        await message.answer("Вопросов не нашлось для этой сложности. Попробуйте другую.")
        return
    nonce = uuid.uuid4().hex[:8]
    _games[user_id] = {
        "questions": questions,
        "index": 0,
        "score": 0,
        "nonce": nonce,
        "difficulty": difficulty,
        "started_at": time.monotonic(),
    }
    await _send_question(message, user_id, f"{user_id}:{nonce}")


async def _send_question(message: Message, user_id: int, game_id: str) -> None:
    game = _games[user_id]
    q = game["questions"][game["index"]]
    await message.answer(
        _question_text(q, game["index"] + 1, len(game["questions"])),
        reply_markup=_game_keyboard(q, game_id, game["index"]),
    )


async def _finish(message: Message, user_id: int) -> None:
    game = _games.pop(user_id)
    total = len(game["questions"])
    await db.save_result(
        user_id, message.from_user.username, game["score"], total, game["difficulty"]
    )
    ratio = game["score"] / total if total else 0
    emoji = "🏆" if ratio >= 0.8 else "👍" if ratio >= 0.5 else "💪"
    await message.answer(
        f"{emoji} <b>Игра окончена!</b>\n"
        f"Ваш результат: <b>{game['score']} / {total}</b> "
        f"(сложность: {_html.escape(game['difficulty'])})\n\n"
        "/leaderboard — посмотреть топ игроков"
    )


# ---------------------------------------------------------------- команды

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "🎓 <b>Telegram Quiz Bot</b>\n\n"
        "/quiz — выбрать сложность и начать викторину\n"
        "/leaderboard — топ игроков\n"
        "/stats — моя статистика (точность, последние игры)\n\n"
        f"Источник вопросов: <b>{'оффлайн-пул' if quiz.demo_mode else 'OpenTDB API'}</b>"
    )


@router.message(Command("quiz"))
async def cmd_quiz(message: Message) -> None:
    _purge_stale_games()
    await message.answer("Выберите сложность:", reply_markup=_difficulty_keyboard())


@router.callback_query(F.data.startswith("diff:"))
async def on_difficulty(callback: CallbackQuery) -> None:
    difficulty = callback.data.split(":", 1)[1]
    if difficulty not in config.DIFFICULTIES:
        await callback.answer("Неизвестная сложность.", show_alert=True)
        return
    await callback.answer(f"Сложность: {difficulty}")
    await _start_game(callback.message, callback.from_user.id, difficulty)


@router.callback_query(F.data.startswith("quiz:"))
async def on_answer(callback: CallbackQuery) -> None:
    # формат quiz:{user_id}:{nonce}:{question_index}:{answer}
    parsed = parse_answer_callback(callback.data or "")
    if parsed is None:
        await callback.answer("Устаревшая кнопка.", show_alert=False)
        return
    cb_user_id, nonce, qindex, answer_idx = parsed
    user_id = callback.from_user.id
    game = _games.get(user_id)
    # чужие кнопки, кнопки от ПРОШЛОЙ игры и повторный клик по уже
    # отвеченному вопросу — недействительны
    if (
        cb_user_id != user_id
        or game is None
        or nonce != game.get("nonce")
        or qindex != game["index"]
    ):
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
    await _show_leaderboard(message, 1)


@router.callback_query(F.data.startswith("lb:"))
async def on_leaderboard_page(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) == 3 and parts[2].isdigit():
        user_id = int(parts[1])
        if user_id != callback.from_user.id:  # чужие кнопки пагинации
            await callback.answer("Это не ваша кнопка.", show_alert=False)
            return
        await _show_leaderboard(callback.message, int(parts[2]))
    await callback.answer()


async def _show_leaderboard(message: Message, page: int) -> None:
    rows = await db.leaderboard(limit=100)  # топ-100, пагинация по 5
    items, total_pages, has_prev, has_next = paginate(rows, page)
    if not items:
        await message.answer("Пока нет результатов. Сыграйте первую игру: /quiz")
        return
    offset = (page - 1) * LB_PAGE_SIZE
    lines = [
        f"{offset + i}. {_html.escape(name)} — <b>{score}</b>"
        for i, (name, score) in enumerate(items, 1)
    ]
    text = "🏆 <b>Топ игроков</b>\n\n" + "\n".join(lines)
    if total_pages > 1:
        await message.answer(text, reply_markup=_lb_keyboard(message.from_user.id, page, total_pages))
    else:
        await message.answer(text)


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    stats = await db.full_stats(message.from_user.id)
    if not stats:
        await message.answer("Вы ещё не играли. Начните: /quiz")
        return
    lines = [
        f"📊 <b>Ваша статистика</b>\n\n"
        f"Игр сыграно: <b>{stats['games_played']}</b>\n"
        f"Лучший результат: <b>{stats['best_score']}</b>\n"
        f"Точность: <b>{stats['accuracy']}%</b> "
        f"(правильных {stats['total_correct']} ответов)\n"
    ]
    if stats["recent"]:
        lines.append("\n🕓 <b>Последние игры:</b>")
        for g in stats["recent"]:
            lines.append(
                f"• {g['score']}/{g['total']} ({_html.escape(g['difficulty'] or 'any')}) — "
                f"{g['played_at']}"
            )
    await message.answer("\n".join(lines))


async def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("Не задан QUIZ_BOT_TOKEN. Скопируйте .env.example и задайте токен.")
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    dp.message.middleware(ThrottlingMiddleware(min_interval=config.THROTTLE_MIN_INTERVAL))
    dp.update.middleware(LoggingMiddleware())
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
