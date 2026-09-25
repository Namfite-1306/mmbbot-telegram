from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, time
from zoneinfo import ZoneInfo

from app.bot.formatter import format_daily_digest, format_signal
from app.models import Action, SignalStatus
from app.services.signal_service import SignalService
from app.services.subscription_service import SubscriptionService
from app.storage.repositories import NotificationRepository
from app.trading_calendar import is_trading_day, previous_trading_day


logger = logging.getLogger(__name__)
SendMessage = Callable[[int, str], Awaitable[None]]


class NotificationService:
    def __init__(
        self,
        signal_service: SignalService,
        subscriptions: SubscriptionService,
        notifications: NotificationRepository,
        timezone_name: str,
        digest_time: time = time(17, 30),
    ):
        self.signal_service = signal_service
        self.subscriptions = subscriptions
        self.notifications = notifications
        self.timezone_name = timezone_name
        self.digest_time = digest_time
        self._send_lock = asyncio.Lock()

    async def send_pending_alerts(self, sender: SendMessage) -> int:
        async with self._send_lock:
            return await self._send_pending_alerts(sender)

    async def _send_pending_alerts(self, sender: SendMessage) -> int:
        sent_count = 0
        for chat_id in await self.subscriptions.alert_subscribers():
            for ticker in await self.subscriptions.list(chat_id):
                lookup = await self.signal_service.lookup(ticker)
                signal = lookup.signal
                if not signal or signal.status is not SignalStatus.SUCCESS:
                    continue
                if signal.action not in {Action.BUY, Action.SELL}:
                    continue
                already_sent = await asyncio.to_thread(
                    self.notifications.was_sent, chat_id, signal.signal_id
                )
                if already_sent:
                    continue
                try:
                    await sender(chat_id, format_signal(signal, self.timezone_name))
                except Exception:
                    logger.exception(
                        "Telegram notification failed",
                        extra={"ticker": ticker, "chat_id": chat_id},
                    )
                    continue
                recorded = await asyncio.to_thread(
                    self.notifications.record_sent, chat_id, signal
                )
                sent_count += int(recorded)
        return sent_count

    async def send_daily_digest(self, sender: SendMessage,
                                now: datetime | None = None) -> int:
        """Opt-in summary of the prior session with a valid final benchmark."""
        async with self._send_lock:
            now = (now or datetime.now(ZoneInfo(self.timezone_name))).astimezone(
                ZoneInfo(self.timezone_name))
            if not is_trading_day(now.date()) or now.time() < self.digest_time:
                return 0
            subscribers = await self.subscriptions.digest_subscribers()
            if not subscribers:
                return 0
            store = getattr(getattr(self.signal_service.provider, "engine", None), "store", None)
            if store is None:
                return 0
            session = previous_trading_day(now.date()).isoformat()
            if await asyncio.to_thread(store.last_scan_session, now.date()) != session:
                return 0
            pending: list[tuple[int, list[str]]] = []
            for chat_id in subscribers:
                if await self.subscriptions.digest_was_sent(chat_id, session):
                    continue
                tickers = await self.subscriptions.list(chat_id)
                if tickers:
                    pending.append((chat_id, tickers))
            if not pending:
                return 0
            coverage = await asyncio.to_thread(store.session_coverage, session)
            signals = await self.signal_service.scan()
            by_ticker = {signal.ticker: signal for signal in signals
                         if signal.data_time.date().isoformat() == session}
            sent_count = 0
            for chat_id, tickers in pending:
                message = format_daily_digest(session,
                                              [(ticker, by_ticker.get(ticker)) for ticker in tickers],
                                              coverage)
                try:
                    await sender(chat_id, message)
                except Exception:
                    logger.exception("Daily digest delivery failed", extra={"chat_id": chat_id})
                    continue
                sent_count += int(await self.subscriptions.record_digest_sent(chat_id, session))
            return sent_count
