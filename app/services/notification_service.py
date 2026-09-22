from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.bot.formatter import format_signal
from app.models import Action, SignalStatus
from app.services.signal_service import SignalService
from app.services.subscription_service import SubscriptionService
from app.storage.repositories import NotificationRepository


logger = logging.getLogger(__name__)
SendMessage = Callable[[int, str], Awaitable[None]]


class NotificationService:
    def __init__(
        self,
        signal_service: SignalService,
        subscriptions: SubscriptionService,
        notifications: NotificationRepository,
        timezone_name: str,
    ):
        self.signal_service = signal_service
        self.subscriptions = subscriptions
        self.notifications = notifications
        self.timezone_name = timezone_name
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
