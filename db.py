"""SQLite-БД результатов викторины (aiosqlite): статистика и лидерборд."""
from __future__ import annotations

import aiosqlite


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id      INTEGER PRIMARY KEY,
                    username     TEXT,
                    games_played INTEGER NOT NULL DEFAULT 0,
                    best_score   INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            await db.commit()

    async def save_result(self, user_id: int, username: str | None, score: int) -> None:
        """После игры: +1 к числу игр, лучший результат — максимум."""
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, username, games_played, best_score)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    games_played = games_played + 1,
                    best_score = MAX(best_score, excluded.best_score)
                """,
                (user_id, username or "", score),
            )
            await db.commit()

    async def stats(self, user_id: int) -> tuple[int, int] | None:
        """(games_played, best_score) или None, если игрок не играл."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT games_played, best_score FROM users WHERE user_id = ?",
                (user_id,),
            ) as cur:
                row = await cur.fetchone()
        return (row[0], row[1]) if row else None

    async def leaderboard(self, limit: int = 10) -> list[tuple[str, int]]:
        """Топ-N игроков по лучшему результату."""
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                """
                SELECT username, best_score
                FROM users
                ORDER BY best_score DESC, games_played ASC
                LIMIT ?
                """,
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        return [(r[0] or f"user_{r[1]}", r[1]) for r in rows]
