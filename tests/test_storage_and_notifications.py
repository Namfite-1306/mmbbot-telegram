from __future__ import annotations

import asyncio
from pathlib import Path

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

