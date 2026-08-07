"""Интеграционный тест полного игрового сценария Quiz-бота.

В отличие от unit-тестов, здесь апдейты Telegram прогоняются через НАСТОЯЩИЙ
Dispatcher: router, middleware, хендлеры и БД бота (тот же код, что и в bot.py).
Исходящие вызовы Bot API перехватываются CapturingSession — сеть не нужна,
тест детерминирован.

Сценарий: /start → /quiz → выбор сложности → 10 вопросов → /leaderboard → /stats.
Плюс проверка nonce-защиты: кнопка от ПРОШЛОЙ игры недействительна.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import bot as botmod
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import TelegramMethod
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    Update,
    User,
)
from db import Database
from middlewares import LoggingMiddleware, ThrottlingMiddleware

USER_ID = 777
USERNAME = "live_tester"
FAKE_TOKEN = "12345:test-only-no-network"


class CapturingSession(BaseSession):
    """Перехватывает исходящие вызовы Bot API и записывает их в self.calls."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[dict] = []

    async def make_request(
        self, bot: Bot, method: TelegramMethod, timeout: int | None = None
    ):
        data = method.model_dump(exclude_none=True)
        self.calls.append({"method": type(method).__name__, "data": data})
        return _fake_result(method, data)

    async def close(self) -> None:
        return None

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield b""


def _fake_result(method: TelegramMethod, data: dict):
    name = type(method).__name__
    if name in ("SendMessage", "EditMessageText"):
        return Message(
            message_id=1,
            date=datetime.now(),
            chat=Chat(id=USER_ID, type="private"),
            text=data.get("text", ""),
            reply_markup=(
                InlineKeyboardMarkup(
                    inline_keyboard=data["reply_markup"]["inline_keyboard"]
                )
                if data.get("reply_markup")
                else None
            ),
        )
    if name == "AnswerCallbackQuery":
        return True
    if name == "GetMe":
        return User(id=8234005969, is_bot=True, first_name="test_net_bot", username="dfs12f_bot")
    return True


def _user() -> User:
    return User(id=USER_ID, is_bot=False, first_name="Live", username=USERNAME)


def _chat() -> Chat:
    return Chat(id=USER_ID, type="private")


def _msg_update(text: str, message_id: int, update_id: int) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=message_id,
            date=datetime.now(),
            chat=_chat(),
            from_user=_user(),
            text=text,
        ),
    )


def _cb_update(data: str, message_id: int, update_id: int) -> Update:
    return Update(
        update_id=update_id,
        callback_query=CallbackQuery(
            id=f"cb{update_id}",
            from_user=_user(),
            chat_instance="test-instance",
            message=Message(
                message_id=message_id, date=datetime.now(), chat=_chat(),
                from_user=_user(), text="игровое сообщение",
            ),
            data=data,
        ),
    )


def _send_texts(session: CapturingSession) -> list[str]:
    return [c["data"].get("text", "") for c in session.calls if c["method"] == "SendMessage"]


def _edit_texts(session: CapturingSession) -> list[str]:
    return [c["data"].get("text", "") for c in session.calls if c["method"] == "EditMessageText"]


def _last_send_markup(session: CapturingSession) -> InlineKeyboardMarkup | None:
    for c in reversed(session.calls):
        if c["method"] == "SendMessage" and c["data"].get("reply_markup"):
            return InlineKeyboardMarkup(inline_keyboard=c["data"]["reply_markup"]["inline_keyboard"])
    return None


def _markup_texts(markup: InlineKeyboardMarkup | None) -> list[str]:
    if not markup:
        return []
    return [btn.text for row in markup.inline_keyboard for btn in row]


# Роутер бота можно прикрепить только к ОДНОМУ Dispatcher'у (aiogram кидает
# RuntimeError при повторном include_router), поэтому Dispatcher создаётся один
# раз на весь тестовый модуль и переиспользуется во всех сценариях.
DP = Dispatcher()
DP.include_router(botmod.router)
# interval=0: в тесте апдейты идут без задержек, троттлинг не должен их дропать
DP.message.middleware(ThrottlingMiddleware(min_interval=0.0))
DP.update.middleware(LoggingMiddleware())


def _make_bot(session: CapturingSession) -> Bot:
    return Bot(
        token=FAKE_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )


def _reset_state(db_path: str) -> None:
    """Свежая БД + чистые глобальные состояния игры (изоляция между тестами)."""
    botmod._games.clear()
    botmod._lb_page.clear()
    botmod.db = Database(db_path)


# ---------------------------------------------------------------- сценарии

def test_full_game_flow(tmp_path) -> None:
    """/start → /quiz → diff:any → 10 правильных ответов → /leaderboard → /stats."""
    db_path = str(tmp_path / "quiz.db")

    async def run() -> None:
        _reset_state(db_path)
        await botmod.db.init()
        session = CapturingSession()
        bot = _make_bot(session)
        dp = DP

        upd = mid = 0

        # /start
        await dp.feed_update(bot, _msg_update("/start", mid := mid + 1, upd := upd + 1))
        assert any("Telegram Quiz Bot" in t for t in _send_texts(session))

        # /quiz → меню сложности с 4 кнопками
        session.calls.clear()
        await dp.feed_update(bot, _msg_update("/quiz", mid := mid + 1, upd := upd + 1))
        menu = _send_texts(session)
        assert any("Выберите сложность" in t for t in menu)
        difficulty_btns = _markup_texts(_last_send_markup(session))
        assert difficulty_btns == ["🎲 Любая", "🟢 Лёгкая", "🟡 Средняя", "🔴 Сложная"]

        # выбор сложности → первый вопрос с 4 вариантами
        session.calls.clear()
        await dp.feed_update(bot, _cb_update("diff:any", mid := mid + 1, upd := upd + 1))
        q1 = _send_texts(session)
        assert any("Вопрос 1/10" in t for t in q1)
        option_btns = _markup_texts(_last_send_markup(session))
        assert len(option_btns) == 4

        # ответы на все 10 вопросов — правильно
        game = botmod._games[USER_ID]
        total = len(game["questions"])
        assert total == 10
        nonce = game["nonce"]
        for i in range(total):
            q = game["questions"][i]
            await dp.feed_update(
                bot, _cb_update(f"quiz:{USER_ID}:{nonce}:{q.correct_index}",
                                mid := mid + 1, upd := upd + 1)
            )

        edits = _edit_texts(session)
        assert len(edits) == 10                      # вердикт после каждого ответа
        assert all("✅ Верно!" in t for t in edits)
        finals = _send_texts(session)
        assert any("Игра окончена!" in t and "10 / 10" in t for t in finals)
        assert USER_ID not in botmod._games          # игра удалена из состояния

        # /leaderboard — наш игрок с 10 очками
        session.calls.clear()
        await dp.feed_update(bot, _msg_update("/leaderboard", mid := mid + 1, upd := upd + 1))
        lb = _send_texts(session)
        assert any("Топ игроков" in t and USERNAME in t and "10" in t for t in lb)

        # /stats — точность 100%
        session.calls.clear()
        await dp.feed_update(bot, _msg_update("/stats", mid := mid + 1, upd := upd + 1))
        st = _send_texts(session)
        assert any("100.0%" in t for t in st)

        # БД: журнал игры + сводка
        full = await botmod.db.full_stats(USER_ID)
        assert full["games_played"] == 1
        assert full["best_score"] == 10
        assert full["accuracy"] == 100.0
        assert full["recent"][0]["difficulty"] == "any"

        await bot.session.close()

    asyncio.run(run())


def test_wrong_answer_shows_correct_option(tmp_path) -> None:
    """Неверный ответ: в вердикте показывается правильный вариант, счёт не растёт."""
    db_path = str(tmp_path / "quiz.db")

    async def run() -> None:
        _reset_state(db_path)
        await botmod.db.init()
        session = CapturingSession()
        bot = _make_bot(session)
        dp = DP

        upd = mid = 0
        await dp.feed_update(bot, _msg_update("/quiz", mid := mid + 1, upd := upd + 1))
        await dp.feed_update(bot, _cb_update("diff:any", mid := mid + 1, upd := upd + 1))

        game = botmod._games[USER_ID]
        q = game["questions"][0]
        wrong = (q.correct_index + 1) % len(q.options)
        await dp.feed_update(
            bot, _cb_update(f"quiz:{USER_ID}:{game['nonce']}:{wrong}",
                            mid := mid + 1, upd := upd + 1)
        )

        edits = _edit_texts(session)
        assert edits[0].startswith(botmod._html.escape(q.text))
        assert "❌ Ошибка. Правильный ответ:" in edits[0]
        assert "Счёт: <b>0</b> / 1" in edits[0]
        await bot.session.close()

    asyncio.run(run())


def test_stale_button_from_previous_game_rejected(tmp_path) -> None:
    """Nonce-защита: кнопка ПРОШЛОЙ игры не работает после старта новой."""
    db_path = str(tmp_path / "quiz.db")

    async def run() -> None:
        _reset_state(db_path)
        await botmod.db.init()
        session = CapturingSession()
        bot = _make_bot(session)
        dp = DP

        upd = mid = 0
        # игра 1
        await dp.feed_update(bot, _msg_update("/quiz", mid := mid + 1, upd := upd + 1))
        await dp.feed_update(bot, _cb_update("diff:any", mid := mid + 1, upd := upd + 1))
        old_nonce = botmod._games[USER_ID]["nonce"]
        await dp.feed_update(
            bot, _cb_update(f"quiz:{USER_ID}:{old_nonce}:0", mid := mid + 1, upd := upd + 1)
        )

        # игра 2 — новый nonce
        await dp.feed_update(bot, _msg_update("/quiz", mid := mid + 1, upd := upd + 1))
        await dp.feed_update(bot, _cb_update("diff:medium", mid := mid + 1, upd := upd + 1))
        new_nonce = botmod._games[USER_ID]["nonce"]
        assert new_nonce != old_nonce

        # жмём кнопку СТАРОЙ игры → отклоняется с предупреждением
        session.calls.clear()
        await dp.feed_update(
            bot, _cb_update(f"quiz:{USER_ID}:{old_nonce}:0", mid := mid + 1, upd := upd + 1)
        )
        alerts = [
            c["data"] for c in session.calls
            if c["method"] == "AnswerCallbackQuery" and c["data"].get("show_alert")
        ]
        assert alerts and "устарела" in alerts[0]["text"]
        assert _edit_texts(session) == []  # вердикт не выводился

        # кнопка НОВОЙ игры работает
        await dp.feed_update(
            bot, _cb_update(f"quiz:{USER_ID}:{new_nonce}:0", mid := mid + 1, upd := upd + 1)
        )
        assert len(_edit_texts(session)) == 1
        await bot.session.close()

    asyncio.run(run())
