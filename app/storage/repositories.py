from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3

from app.models import Signal
from app.storage.database import Database


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class WatchlistAddResult:
    added: list[str]
    existing: list[str]
    full: list[str]


class UserRepository:
    def __init__(self, database: Database):
        self.database = database

    def upsert(self, chat_id: int, username: str | None) -> None:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO users(chat_id, username, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET username=excluded.username, updated_at=excluded.updated_at
                """,
                (chat_id, username, now, now),
            )
            connection.execute(
                "INSERT OR IGNORE INTO user_settings(chat_id, alerts_enabled, updated_at) VALUES (?, 0, ?)",
                (chat_id, now),
            )


class WatchlistRepository:
    def __init__(self, database: Database):
        self.database = database

    def list(self, chat_id: int) -> list[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT ticker FROM watchlist WHERE chat_id=? ORDER BY created_at, ticker", (chat_id,)
            ).fetchall()
        return [row["ticker"] for row in rows]

    def add_many(self, chat_id: int, tickers: list[str], limit: int = 10) -> WatchlistAddResult:
        added: list[str] = []
        existing: list[str] = []
        full: list[str] = []
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = {
                row["ticker"]
                for row in connection.execute("SELECT ticker FROM watchlist WHERE chat_id=?", (chat_id,))
            }
            for ticker in tickers:
                if ticker in current:
                    existing.append(ticker)
                elif len(current) >= limit:
                    full.append(ticker)
                else:
                    connection.execute(
                        "INSERT INTO watchlist(chat_id, ticker, created_at) VALUES (?, ?, ?)",
                        (chat_id, ticker, now),
                    )
                    current.add(ticker)
                    added.append(ticker)
        return WatchlistAddResult(added, existing, full)

    def remove_many(self, chat_id: int, tickers: list[str]) -> tuple[list[str], list[str]]:
        removed: list[str] = []
        missing: list[str] = []
        with self.database.connect() as connection:
            for ticker in tickers:
                cursor = connection.execute(
                    "DELETE FROM watchlist WHERE chat_id=? AND ticker=?", (chat_id, ticker)
                )
                (removed if cursor.rowcount else missing).append(ticker)
        return removed, missing


class SettingsRepository:
    def __init__(self, database: Database):
        self.database = database

    def set_alerts(self, chat_id: int, enabled: bool) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO user_settings(chat_id, alerts_enabled, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET alerts_enabled=excluded.alerts_enabled, updated_at=excluded.updated_at
                """,
                (chat_id, int(enabled), utc_now()),
            )

    def alerts_enabled(self, chat_id: int) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT alerts_enabled FROM user_settings WHERE chat_id=?", (chat_id,)
            ).fetchone()
        return bool(row and row["alerts_enabled"])

    def alert_subscribers(self) -> list[int]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT chat_id FROM user_settings WHERE alerts_enabled=1 ORDER BY chat_id"
            ).fetchall()
        return [row["chat_id"] for row in rows]


class NotificationRepository:
    def __init__(self, database: Database):
        self.database = database

    def was_sent(self, chat_id: int, signal_id: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM notifications WHERE chat_id=? AND signal_id=?", (chat_id, signal_id)
            ).fetchone()
        return row is not None

    def record_sent(self, chat_id: int, signal: Signal) -> bool:
        try:
            with self.database.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO notifications(chat_id, signal_id, ticker, action, sent_at, status)
                    VALUES (?, ?, ?, ?, ?, 'SENT')
                    """,
                    (chat_id, signal.signal_id, signal.ticker, signal.action.value, utc_now()),
                )
            return True
        except sqlite3.IntegrityError:
            if self.was_sent(chat_id, signal.signal_id):
                return False
            raise
