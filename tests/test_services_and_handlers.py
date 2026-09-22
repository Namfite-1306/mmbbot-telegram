from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import BadRequest, NetworkError

from app.bot.formatter import format_help, format_start
from app.bot.handlers import BotHandlers
from app.config import Settings
from app.main import RedactingFormatter
from app.providers import MockSignalProvider
from app.providers.strategy_signal_provider import StrategySignalProvider
from app.strategy_engine import VietcapStrategyEngine
from app.models import Signal, SignalStatus
from datetime import datetime
from zoneinfo import ZoneInfo
from app.services import SignalService, SubscriptionService
from app.storage import Database, SettingsRepository, UserRepository, WatchlistRepository


def test_missing_soi_argument(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "handler.db")
        database.initialize()
        subscriptions = SubscriptionService(
            UserRepository(database), WatchlistRepository(database), SettingsRepository(database)
        )
        handlers = BotHandlers(
            Settings("fake-token", database_path=tmp_path / "handler.db"),
            SignalService(MockSignalProvider()),
            subscriptions,
            database,
        )
        reply = AsyncMock()
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=1),
            effective_user=SimpleNamespace(username="tester"),
            effective_message=SimpleNamespace(reply_text=reply),
        )
        context = SimpleNamespace(args=[])
        await handlers.soi(update, context)
        reply.assert_awaited_once()
        assert "Cú pháp: /soi" in reply.await_args.args[0]

    asyncio.run(scenario())


def test_start_registers_user_and_returns_welcome(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "start.db")
        database.initialize()
        subscriptions = SubscriptionService(
            UserRepository(database), WatchlistRepository(database), SettingsRepository(database)
        )
        handlers = BotHandlers(
            Settings("fake-token", database_path=tmp_path / "start.db"),
            SignalService(MockSignalProvider()),
            subscriptions,
            database,
        )
        reply = AsyncMock()
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=22),
            effective_user=SimpleNamespace(username="starter"),
            effective_message=SimpleNamespace(reply_text=reply),
        )
        await handlers.start(update, SimpleNamespace())
        assert "Chào bạn, tôi là nhà tư vấn chiến lược Mr. Mission Bossible bot. Tôi có thể giúp gì cho bạn." in reply.await_args.args[0]
        assert await subscriptions.alerts_enabled(22) is False

    asyncio.run(scenario())


def test_command_guidance_uses_ticker_placeholder() -> None:
    for message in (format_start(), format_help()):
        assert "/soi <MÃ>" in message
        assert "/add <MÃ> <MÃ>" in message
        assert "/portfolio" in message
        assert "/remove" in format_help()
        assert "FPT" not in message
        assert "HPG" not in message


def test_ticker_not_found_is_not_system_error() -> None:
    async def scenario() -> None:
        lookup = await SignalService(MockSignalProvider()).lookup("AAA")
        assert lookup.signal is None
        assert lookup.error_code == "not_found"

    asyncio.run(scenario())


def test_provider_error_does_not_escape_service() -> None:
    async def scenario() -> None:
        lookup = await SignalService(MockSignalProvider()).lookup("ERR")
        assert lookup.signal is None
        assert lookup.error_code == "provider_error"

    asyncio.run(scenario())


def test_reply_retries_transient_network_error() -> None:
    async def scenario() -> None:
        reply = AsyncMock(side_effect=[NetworkError("temporary"), None])
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=1),
            effective_message=SimpleNamespace(reply_text=reply),
        )
        with patch("app.bot.handlers.asyncio.sleep", new=AsyncMock()) as sleep:
            await BotHandlers._reply(update, "hello")
        assert reply.await_count == 2
        sleep.assert_awaited_once()
        assert reply.await_args.kwargs["link_preview_options"].is_disabled is True

    asyncio.run(scenario())


def test_strategy_soi_includes_chart_button_and_score_breakdown(tmp_path: Path) -> None:
    async def scenario() -> None:
        class Store:
            def stock_overview(self, symbol, signal_date=None):
                assert (symbol, signal_date) == ("AAA", "2026-09-21")
                return {"quote": {"trade_date": "2026-09-21", "close": 10000,
                                  "open": 10000, "high": 11000, "low": 9000,
                                  "volume": 100000, "is_final": 1},
                        "previous_close": 9900, "financial": None}

            def close_before(self, symbol, trade_day):
                return 9900

        timestamp = datetime(2026, 9, 21, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        signal = Signal("test", "AAA", None, 10000, 100, ["Thiếu F"], timestamp,
                        timestamp, "v1", "1D", SignalStatus.WATCH_ONLY,
                        {"T": 100, "F": None, "M": 50})
        provider = StrategySignalProvider(engine=VietcapStrategyEngine(Store()), auto_refresh=False)
        provider.get_signal = AsyncMock(return_value=signal)
        database = Database(tmp_path / "bot.db")
        database.initialize()
        subscriptions = SubscriptionService(UserRepository(database), WatchlistRepository(database),
                                            SettingsRepository(database))
        handlers = BotHandlers(Settings("fake-token", database_path=tmp_path / "bot.db"),
                               SignalService(provider), subscriptions, database)
        reply = AsyncMock()
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=2),
                                 effective_user=SimpleNamespace(username="tester"),
                                 effective_message=SimpleNamespace(reply_text=reply))
        with patch("app.bot.handlers.fetch_company_news", return_value=[]), \
             patch("app.bot.handlers.DNSEMarketClient") as dnse:
            dnse.return_value.get_latest_trade.return_value = {
                "symbol": "AAA", "price_vnd": 10100, "time": timestamp,
            }
            await handlers.soi(update, SimpleNamespace(args=["AAA"]))
        message = reply.await_args.args[0]
        markup = reply.await_args.kwargs["reply_markup"]
        assert "Chưa có điểm tổng" in message
        assert "10.100 VNĐ" in message
        assert markup.inline_keyboard[0][0].callback_data == "chart:AAA"
        assert markup.inline_keyboard[0][1].callback_data == "flow:AAA"

        reply.reset_mock()
        with patch("app.bot.handlers.fetch_company_news", return_value=[]), \
             patch("app.bot.handlers.DNSEMarketClient", side_effect=RuntimeError("offline")):
            await handlers.soi(update, SimpleNamespace(args=["AAA"]))
        assert "dùng dữ liệu đã lưu" in reply.await_args.args[0]

    asyncio.run(scenario())


def test_chart_callback_delivers_image_and_document_fallback(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "chart.db")
        database.initialize()
        subscriptions = SubscriptionService(UserRepository(database), WatchlistRepository(database),
                                            SettingsRepository(database))

        class Store:
            pass

        provider = StrategySignalProvider(engine=VietcapStrategyEngine(Store()), auto_refresh=False)
        service = SignalService(provider)
        service.ticker_exists = AsyncMock(return_value=True)
        handlers = BotHandlers(Settings("fake-token", database_path=tmp_path / "chart.db"),
                               service, subscriptions, database)
        bot = SimpleNamespace(send_photo=AsyncMock(), send_document=AsyncMock())
        query = SimpleNamespace(data="chart:AAA", answer=AsyncMock(side_effect=NetworkError("temporary")))
        update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=9),
                                 effective_message=SimpleNamespace(reply_text=AsyncMock()))
        with patch("app.bot.handlers.render_stock_chart", return_value=BytesIO(b"PNG")):
            await handlers.chart(update, SimpleNamespace(bot=bot))
        bot.send_photo.assert_awaited_once()
        assert bot.send_photo.await_args.kwargs["chat_id"] == 9
        bot.send_photo.side_effect = BadRequest("photo rejected")
        with patch("app.bot.handlers.render_stock_chart", return_value=BytesIO(b"PNG")):
            await handlers.chart(update, SimpleNamespace(bot=bot))
        bot.send_document.assert_awaited_once()

        bot.send_photo.reset_mock()
        bot.send_photo.side_effect = None
        query.data = "flow:AAA"
        with patch("app.bot.handlers.render_money_flow_chart", return_value=BytesIO(b"PNG")) as render:
            await handlers.chart(update, SimpleNamespace(bot=bot))
        render.assert_called_once()
        assert "CMF(20)" in bot.send_photo.await_args.kwargs["caption"]

    asyncio.run(scenario())


def test_portfolio_uses_saved_signals_without_refresh(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "portfolio.db")
        database.initialize()
        subscriptions = SubscriptionService(UserRepository(database), WatchlistRepository(database),
                                            SettingsRepository(database))

        class Store:
            def stock_overview(self, ticker, signal_day=None):
                return {"quote": {"trade_date": "2026-09-21", "close": 10000,
                                  "is_final": 1}, "previous_close": 9500}

        timestamp = datetime(2026, 9, 21, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        signal = Signal("x", "AAA", None, 10000, 60, [], timestamp,
                        timestamp, "v1", "1D", SignalStatus.WATCH_ONLY)
        provider = StrategySignalProvider(engine=VietcapStrategyEngine(Store()), auto_refresh=False)
        provider.get_saved_signal = AsyncMock(return_value=signal)
        provider.get_signal = AsyncMock(side_effect=AssertionError("portfolio must not crawl"))
        handlers = BotHandlers(Settings("fake-token", database_path=tmp_path / "portfolio.db"),
                               SignalService(provider), subscriptions, database)
        reply = AsyncMock()
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=3),
                                 effective_user=SimpleNamespace(username="tester"),
                                 effective_message=SimpleNamespace(reply_text=reply))
        await subscriptions.ensure_user(3, "tester")
        await subscriptions.add(3, ["AAA"])
        await handlers.watchlist(update, SimpleNamespace())
        assert "🟡 AAA — Trung tính" in reply.await_args.args[0]
        assert "+5.26%" in reply.await_args.args[0]
        provider.get_signal.assert_not_awaited()

    asyncio.run(scenario())


def test_single_ticker_text_uses_detailed_view(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "text.db")
        database.initialize()
        subscriptions = SubscriptionService(UserRepository(database), WatchlistRepository(database),
                                            SettingsRepository(database))
        handlers = BotHandlers(Settings("fake-token", database_path=tmp_path / "text.db"),
                               SignalService(MockSignalProvider()), subscriptions, database)
        handlers._send_stock_lookup = AsyncMock()
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=4),
                                 effective_user=SimpleNamespace(username="tester"),
                                 effective_message=SimpleNamespace(text="FPT", reply_text=AsyncMock()))
        await handlers.text(update, SimpleNamespace())
        handlers._send_stock_lookup.assert_awaited_once()
        assert handlers._send_stock_lookup.await_args.args[1].ticker == "FPT"

    asyncio.run(scenario())


def test_log_formatter_redacts_telegram_token() -> None:
    import logging

    fake_secret = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
    record = logging.LogRecord(
        "httpx", logging.INFO, __file__, 1, "POST https://api.telegram.org/bot%s/sendMessage", (fake_secret,), None
    )
    output = RedactingFormatter("%(message)s").format(record)
    assert fake_secret not in output
    assert "bot<redacted>" in output


def test_scan_order_buy_then_sell_score_descending() -> None:
    async def scenario() -> None:
        signals = await SignalService(MockSignalProvider()).scan()
        actions = [signal.action.value for signal in signals]
        first_sell = actions.index("SELL")
        assert all(action == "BUY" for action in actions[:first_sell])
        assert all(action == "SELL" for action in actions[first_sell:])
        buy_scores = [signal.score for signal in signals[:first_sell]]
        sell_scores = [signal.score for signal in signals[first_sell:]]
        assert buy_scores == sorted(buy_scores, reverse=True)
        assert sell_scores == sorted(sell_scores, reverse=True)

    asyncio.run(scenario())
