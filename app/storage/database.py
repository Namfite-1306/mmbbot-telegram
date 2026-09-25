from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY,
    username TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist (
    chat_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(chat_id, ticker),
    FOREIGN KEY(chat_id) REFERENCES users(chat_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS user_settings (
    chat_id INTEGER PRIMARY KEY,
    alerts_enabled INTEGER NOT NULL DEFAULT 0 CHECK(alerts_enabled IN (0, 1)),
    updated_at TEXT NOT NULL,
    FOREIGN KEY(chat_id) REFERENCES users(chat_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS notifications (
    chat_id INTEGER NOT NULL,
    signal_id TEXT NOT NULL,
    ticker TEXT NOT NULL,
    action TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    status TEXT NOT NULL,
    UNIQUE(chat_id, signal_id),
    FOREIGN KEY(chat_id) REFERENCES users(chat_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_watchlist_chat_id ON watchlist(chat_id);
CREATE INDEX IF NOT EXISTS idx_settings_alerts ON user_settings(alerts_enabled);
CREATE TABLE IF NOT EXISTS paper_accounts (
    chat_id INTEGER PRIMARY KEY,
    initial_cash INTEGER NOT NULL CHECK(initial_cash > 0),
    cash INTEGER NOT NULL CHECK(cash >= 0),
    created_at TEXT NOT NULL,
    FOREIGN KEY(chat_id) REFERENCES users(chat_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS paper_positions (
    chat_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    shares INTEGER NOT NULL CHECK(shares > 0),
    cost_basis INTEGER NOT NULL CHECK(cost_basis >= 0),
    PRIMARY KEY(chat_id, ticker),
    FOREIGN KEY(chat_id) REFERENCES paper_accounts(chat_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS paper_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
    shares INTEGER NOT NULL CHECK(shares > 0),
    status TEXT NOT NULL CHECK(status IN ('PENDING', 'FILLED', 'REJECTED', 'CANCELED')),
    requested_at TEXT NOT NULL,
    requested_date TEXT NOT NULL,
    reference_close INTEGER NOT NULL CHECK(reference_close > 0),
    estimated_cost INTEGER NOT NULL DEFAULT 0,
    fill_date TEXT,
    fill_price INTEGER,
    fee INTEGER NOT NULL DEFAULT 0,
    tax INTEGER NOT NULL DEFAULT 0,
    cash_amount INTEGER,
    realized_pnl INTEGER,
    quote_source TEXT,
    quote_time TEXT,
    note TEXT,
    FOREIGN KEY(chat_id) REFERENCES paper_accounts(chat_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_paper_orders_pending ON paper_orders(status, requested_date);
CREATE INDEX IF NOT EXISTS idx_paper_orders_chat ON paper_orders(chat_id, id DESC);
CREATE TABLE IF NOT EXISTS paper_journal (
    order_id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    thesis TEXT,
    exit_rule TEXT,
    reflection TEXT,
    signal_status TEXT,
    signal_action TEXT,
    signal_score REAL,
    signal_date TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES paper_orders(id) ON DELETE CASCADE,
    FOREIGN KEY(chat_id) REFERENCES users(chat_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_paper_journal_chat ON paper_journal(chat_id, order_id DESC);
CREATE TABLE IF NOT EXISTS paper_adjustments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('CAPITAL', 'POSITION')),
    ticker TEXT,
    old_amount INTEGER NOT NULL,
    new_amount INTEGER NOT NULL,
    old_basis INTEGER,
    new_basis INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(chat_id) REFERENCES paper_accounts(chat_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS paper_order_events (
    order_id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    FOREIGN KEY(order_id) REFERENCES paper_orders(id) ON DELETE CASCADE,
    FOREIGN KEY(chat_id) REFERENCES paper_accounts(chat_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_paper_order_events_pending ON paper_order_events(sent_at, order_id);
CREATE TABLE IF NOT EXISTS digest_subscriptions (
    chat_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0, 1)),
    updated_at TEXT NOT NULL,
    FOREIGN KEY(chat_id) REFERENCES users(chat_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS daily_digests (
    chat_id INTEGER NOT NULL,
    session TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY(chat_id, session),
    FOREIGN KEY(chat_id) REFERENCES users(chat_id) ON DELETE CASCADE
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_journal)")}
            if "signal_status" not in columns:
                connection.execute("ALTER TABLE paper_journal ADD COLUMN signal_status TEXT")
            order_columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_orders)")}
            if "quote_source" not in order_columns:
                connection.execute("ALTER TABLE paper_orders ADD COLUMN quote_source TEXT")
            if "quote_time" not in order_columns:
                connection.execute("ALTER TABLE paper_orders ADD COLUMN quote_time TEXT")

    def healthcheck(self) -> bool:
        try:
            with self.connect() as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False
