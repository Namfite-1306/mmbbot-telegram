from __future__ import annotations

import asyncio

from app.storage.repositories import SettingsRepository, UserRepository, WatchlistAddResult, WatchlistRepository


class SubscriptionService:
    def __init__(
        self,
        users: UserRepository,
        watchlists: WatchlistRepository,
        settings: SettingsRepository,
        max_watchlist_size: int = 10,
    ):
        self.users = users
        self.watchlists = watchlists
        self.settings = settings
        self.max_watchlist_size = max_watchlist_size

    async def ensure_user(self, chat_id: int, username: str | None) -> None:
        await asyncio.to_thread(self.users.upsert, chat_id, username)

    async def add(self, chat_id: int, tickers: list[str]) -> WatchlistAddResult:
        return await asyncio.to_thread(
            self.watchlists.add_many, chat_id, tickers, self.max_watchlist_size
        )

    async def remove(self, chat_id: int, tickers: list[str]) -> tuple[list[str], list[str]]:
        return await asyncio.to_thread(self.watchlists.remove_many, chat_id, tickers)

    async def list(self, chat_id: int) -> list[str]:
        return await asyncio.to_thread(self.watchlists.list, chat_id)

    async def set_alerts(self, chat_id: int, enabled: bool) -> None:
        await asyncio.to_thread(self.settings.set_alerts, chat_id, enabled)

    async def alerts_enabled(self, chat_id: int) -> bool:
        return await asyncio.to_thread(self.settings.alerts_enabled, chat_id)

    async def alert_subscribers(self) -> list[int]:
        return await asyncio.to_thread(self.settings.alert_subscribers)

