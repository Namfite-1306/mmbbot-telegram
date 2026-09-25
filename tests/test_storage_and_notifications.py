from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.models import Action, Signal, SignalStatus
from app.providers import MockSignalProvider
from app.services import NotificationService, SignalService, SubscriptionService
from app.storage import Database, NotificationRepository, SettingsRepository, UserRepository, WatchlistRepository


def build_services(path: Path):
    database = Database(path)
    database.initialize()
    users = UserRepository(database)
    watchlists = WatchlistRepository(database)
    settings = SettingsRepository(database)
    subscriptions = SubscriptionService(users, watchlists, settings)
    signals = SignalService(MockSignalProvider())
    notifications = NotificationService(
        signals,
        subscriptions,
        NotificationRepository(database),
        "Asia/Ho_Chi_Minh",
    )
    return database, subscriptions, notifications


def test_existing_paper_journal_schema_is_upgraded_without_dropping_data(tmp_path: Path) -> None:
    database = Database(tmp_path / "old.db")
    database.initialize()
    with database.connect() as connection:
        connection.execute("DROP TABLE paper_journal")
        connection.execute("""CREATE TABLE paper_journal (
            order_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL,
            thesis TEXT, exit_rule TEXT, reflection TEXT,
            signal_action TEXT, signal_score REAL, signal_date TEXT,
            updated_at TEXT NOT NULL)""")
        connection.execute("INSERT INTO paper_journal(order_id,chat_id,thesis,updated_at) "
                           "VALUES(1,101,'Old note','2026-09-23')")
    database.initialize()
    with database.connect() as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_journal)")}
        note = connection.execute("SELECT thesis FROM paper_journal WHERE order_id=1").fetchone()[0]
    assert "signal_status" in columns
    assert note == "Old note"


def test_daily_digest_is_opt_in_and_once_per_verified_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        database, subscriptions, _ = build_services(tmp_path / "digest.db")
        await subscriptions.ensure_user(101, "tester")
        await subscriptions.add(101, ["AAA"])
        stamp = datetime(2026, 9, 23, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        signal = Signal("s1", "AAA", Action.BUY, 10000, 80, [], stamp, stamp,
                        "v1", "1D", SignalStatus.SUCCESS,
                        {"T": 80, "F": 80, "M": 80})
        store = SimpleNamespace(last_scan_session=lambda day: "2026-09-23",
                                session_coverage=lambda day: (1, 2))

        class Provider:
            name = "strategy"
            engine = SimpleNamespace(store=store)

            async def get_market_signals(self):
                return [signal]

        notifications = NotificationService(
            SignalService(Provider()), subscriptions, NotificationRepository(database),
            "Asia/Ho_Chi_Minh")
        sent = []

        async def sender(chat_id, text):
            sent.append((chat_id, text))

        now = datetime(2026, 9, 24, 17, 45, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        assert await notifications.send_daily_digest(sender, now) == 0
        await subscriptions.set_digest(101, True)
        assert await notifications.send_daily_digest(sender, now.replace(hour=17, minute=0)) == 0
        assert await notifications.send_daily_digest(sender, now) == 1
        assert await notifications.send_daily_digest(sender, now) == 0
        assert len(sent) == 1 and "phiên 2026-09-23" in sent[0][1]
        assert "AAA: MUA" in sent[0][1]
        assert await subscriptions.digest_enabled(101)
        await subscriptions.set_digest(101, False)
        assert not await subscriptions.digest_enabled(101)

    asyncio.run(scenario())


def test_add_delete_and_duplicate_watchlist(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, subscriptions, _ = build_services(tmp_path / "bot.db")
        await subscriptions.ensure_user(100, "tester")
        first = await subscriptions.add(100, ["FPT", "HPG", "FPT"])
        assert first.added == ["FPT", "HPG"]
        second = await subscriptions.add(100, ["FPT", "VNM"])
        assert second.existing == ["FPT"]
        assert second.added == ["VNM"]
        removed, missing = await subscriptions.remove(100, ["HPG", "VCB"])
        assert removed == ["HPG"]
        assert missing == ["VCB"]
        assert await subscriptions.list(100) == ["FPT", "VNM"]

    asyncio.run(scenario())


def test_watchlist_limit_is_ten(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, subscriptions, _ = build_services(tmp_path / "limit.db")
        await subscriptions.ensure_user(101, "tester")
        tickers = [f"A{i:02d}" for i in range(11)]
        result = await subscriptions.add(101, tickers)
        assert len(result.added) == 10
        assert result.full == ["A10"]
        assert len(await subscriptions.list(101)) == 10

    asyncio.run(scenario())


def test_database_restart_preserves_watchlist(tmp_path: Path) -> None:
    database_path = tmp_path / "persistent.db"

    async def first_run() -> None:
        _, subscriptions, _ = build_services(database_path)
        await subscriptions.ensure_user(102, "tester")
        await subscriptions.add(102, ["FPT"])
        await subscriptions.set_alerts(102, True)

    async def second_run() -> None:
        database = Database(database_path)
        database.initialize()
        subscriptions = SubscriptionService(
            UserRepository(database), WatchlistRepository(database), SettingsRepository(database)
        )
        assert await subscriptions.list(102) == ["FPT"]
        assert await subscriptions.alerts_enabled(102) is True

    asyncio.run(first_run())
    asyncio.run(second_run())


def test_notification_deduplicates_signal_id(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, subscriptions, notifications = build_services(tmp_path / "notifications.db")
        await subscriptions.ensure_user(103, "tester")
        await subscriptions.add(103, ["FPT", "HPG"])
        await subscriptions.set_alerts(103, True)
        messages: list[tuple[int, str]] = []

        async def sender(chat_id: int, text: str) -> None:
            messages.append((chat_id, text))

        assert await notifications.send_pending_alerts(sender) == 1
        assert await notifications.send_pending_alerts(sender) == 0
        assert len(messages) == 1
        assert "FPT — BUY" in messages[0][1]

    asyncio.run(scenario())
