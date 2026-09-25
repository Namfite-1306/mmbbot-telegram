from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from telegram.error import BadRequest, NetworkError
from telegram.ext import CommandHandler, MessageHandler

from app.bot.formatter import format_help, format_start
from app.bot.handlers import BotHandlers
from app.config import Settings
from app.main import RedactingFormatter, main
from app.providers import MockSignalProvider
from app.providers.base import ProviderError
from app.providers.strategy_signal_provider import StrategySignalProvider
from app.strategy_engine import VietcapStrategyEngine
from app.models import Action, Signal, SignalStatus
from datetime import datetime
from zoneinfo import ZoneInfo
from app.services import SignalService, SubscriptionService
from app.storage import Database, SettingsRepository, UserRepository, WatchlistRepository


def test_start_replies_when_update_reaches_handler(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "start.db")
        database.initialize()
        subscriptions = SubscriptionService(
            UserRepository(database), WatchlistRepository(database), SettingsRepository(database)
        )
        handlers = BotHandlers(Settings("fake-token", database_path=tmp_path / "start.db"),
                               SignalService(MockSignalProvider()), subscriptions, database)
        reply = AsyncMock()
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=1),
            effective_user=SimpleNamespace(username="tester"),
            effective_message=SimpleNamespace(reply_text=reply),
        )
        await handlers.start(update, SimpleNamespace())
        assert "Mr. Mission Bossible" in reply.await_args.args[0]

    asyncio.run(scenario())


def test_command_shows_waiting_message_then_edits_response(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "waiting.db")
        database.initialize()
        subscriptions = SubscriptionService(
            UserRepository(database), WatchlistRepository(database), SettingsRepository(database)
        )
        handlers = BotHandlers(Settings("fake-token", database_path=tmp_path / "waiting.db"),
                               SignalService(MockSignalProvider()), subscriptions, database)
        waiting = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
        reply = AsyncMock(return_value=waiting)
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=1),
            effective_user=SimpleNamespace(username="tester"),
            effective_message=SimpleNamespace(reply_text=reply),
        )
        await handlers._with_waiting(handlers.start)(update, SimpleNamespace())
        reply.assert_awaited_once_with("Vui lòng chờ phản hồi")
        assert "Mr. Mission Bossible" in waiting.edit_text.await_args.args[0]
        waiting.delete.assert_not_awaited()

    asyncio.run(scenario())


def test_waiting_messages_do_not_cross_concurrent_chats(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "concurrent.db")
        database.initialize()
        subscriptions = SubscriptionService(
            UserRepository(database), WatchlistRepository(database), SettingsRepository(database)
        )
        handlers = BotHandlers(Settings("fake-token", database_path=tmp_path / "concurrent.db"),
                               SignalService(MockSignalProvider()), subscriptions, database)
        waiting = [SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock()) for _ in range(2)]
        updates = [SimpleNamespace(effective_chat=SimpleNamespace(id=index + 1),
                                   effective_message=SimpleNamespace(
                                       reply_text=AsyncMock(return_value=waiting[index])))
                   for index in range(2)]

        async def respond(update, context):
            await asyncio.sleep(0)
            await handlers._reply(update, f"chat {update.effective_chat.id}")

        await asyncio.gather(*(handlers._with_waiting(respond)(update, SimpleNamespace())
                               for update in updates))
        assert waiting[0].edit_text.await_args.args[0] == "chat 1"
        assert waiting[1].edit_text.await_args.args[0] == "chat 2"

    asyncio.run(scenario())


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
        assert "Cú pháp đúng: /soi <MÃ>" in reply.await_args.args[0]
        assert "Ví dụ: /soi FPT" in reply.await_args.args[0]

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


def test_start_uses_icons_for_commands() -> None:
    message = format_start()
    assert message.startswith("👋 ")
    assert "🔎 /analyze <MÃ>" in message
    assert "📡 /scan" in message
    assert "💼 /portfolio" in message
    assert "📈 /chart <MÃ>" in message


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
        provider.get_saved_signal = AsyncMock(return_value=signal)
        provider.get_signal = AsyncMock(side_effect=AssertionError("/soi must not crawl EOD data"))
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
        assert "Điểm: chưa tính được." in message
        assert "T: 100/100" not in message and "F: chưa có" not in message and "M: 50/100" not in message
        assert "10.100 VNĐ" in message
        assert markup.inline_keyboard[0][0].callback_data == "chart:AAA"
        assert markup.inline_keyboard[0][1].callback_data == "flow:AAA"
        assert markup.inline_keyboard[1][0].callback_data == "trade:buy:AAA"
        assert markup.inline_keyboard[1][1].callback_data == "trade:sell:AAA"
        assert markup.inline_keyboard[2][0].callback_data == "paper:account"

        reply.reset_mock()
        with patch("app.bot.handlers.fetch_company_news", return_value=[]), \
             patch("app.bot.handlers.DNSEMarketClient", side_effect=RuntimeError("offline")):
            await handlers.soi(update, SimpleNamespace(args=["AAA"]))
        assert "dùng dữ liệu đã lưu" in reply.await_args.args[0]

    asyncio.run(scenario())


def test_add_remove_buttons_and_trade_callback_include_selected_ticker(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "actions.db")
        database.initialize()
        subscriptions = SubscriptionService(
            UserRepository(database), WatchlistRepository(database), SettingsRepository(database))
        service = SignalService(MockSignalProvider())
        service.ticker_exists = AsyncMock(return_value=True)
        handlers = BotHandlers(Settings("fake-token", database_path=database.path),
                               service, subscriptions, database)
        reply = AsyncMock()
        message = SimpleNamespace(text="/add AAA", reply_text=reply)
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=7, type="private"),
                                 effective_user=SimpleNamespace(username="tester"),
                                 effective_message=message)

        await handlers.add(update, SimpleNamespace(args=["AAA"]))
        buttons = reply.await_args.kwargs["reply_markup"].inline_keyboard[0]
        assert [button.callback_data for button in buttons] == [
            "nav:portfolio", "nav:modelportfolio"]

        reply.reset_mock()
        await handlers.delete(update, SimpleNamespace(args=["AAA"]))
        buttons = reply.await_args.kwargs["reply_markup"].inline_keyboard[0]
        assert [button.callback_data for button in buttons] == [
            "nav:portfolio", "nav:modelportfolio"]

        reply.reset_mock()
        query = SimpleNamespace(data="trade:buy:AAA", answer=AsyncMock())
        callback_update = SimpleNamespace(
            callback_query=query,
            effective_chat=SimpleNamespace(id=7, type="private"),
            effective_message=SimpleNamespace(reply_text=reply),
        )
        await handlers.stock_action_callback(callback_update, SimpleNamespace())
        assert "/paperbuy AAA <SỐ_CP>" in reply.await_args.args[0]

    asyncio.run(scenario())


def test_removed_paper_note_commands_are_not_exposed() -> None:
    help_text = format_help().lower()
    assert "paperjournal" not in help_text
    assert "paperreview" not in help_text
    assert not hasattr(BotHandlers, "paper_journal")
    assert not hasattr(BotHandlers, "paper_review")


def test_manual_command_aliases_are_registered_and_unknown_commands_reply(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "manual_commands.db")
        database.initialize()
        subscriptions = SubscriptionService(
            UserRepository(database), WatchlistRepository(database), SettingsRepository(database))
        handlers = BotHandlers(Settings("fake-token", database_path=database.path),
                               SignalService(MockSignalProvider()), subscriptions, database)
        registered = []
        application = SimpleNamespace(add_handler=registered.append,
                                      add_error_handler=lambda callback: None)
        handlers.register(application)
        commands = set().union(*(item.commands for item in registered
                                 if isinstance(item, CommandHandler)))
        assert {"buy", "mua", "sell", "ban", "phanbo"} <= commands
        assert any(isinstance(item, MessageHandler) for item in registered)

        reply = AsyncMock()
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=9, type="private"),
            effective_user=SimpleNamespace(username="tester"),
            effective_message=SimpleNamespace(text="/khongco AAA", reply_text=reply),
        )
        await handlers.unknown_command(update, SimpleNamespace())
        assert "Không nhận ra lệnh /khongco" in reply.await_args.args[0]

        reply.reset_mock()
        update.effective_message.text = "/char"
        await handlers.unknown_command(update, SimpleNamespace())
        assert "Có phải bạn muốn dùng /chart?" in reply.await_args.args[0]
        assert "Ví dụ: /chart FPT" in reply.await_args.args[0]

    asyncio.run(scenario())


def test_invalid_command_keeps_usage_guidance_when_reply_has_network_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = Database(tmp_path / "command_error.db")
        database.initialize()
        subscriptions = SubscriptionService(
            UserRepository(database), WatchlistRepository(database), SettingsRepository(database))
        handlers = BotHandlers(Settings("fake-token", database_path=database.path),
                               SignalService(MockSignalProvider()), subscriptions, database)
        reply = AsyncMock()
        message = SimpleNamespace(text="/chart", reply_text=reply)
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=10, type="private"),
                                 effective_message=message)
        await handlers.error(update, SimpleNamespace(error=NetworkError("temporary")))
        response = reply.await_args.args[0]
        assert "Cú pháp đúng: /chart <MÃ>" in response
        assert "Ví dụ: /chart FPT" in response
        assert "Kết nối Telegram" not in response

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


def test_vn100_scan_excludes_incomplete_unchanged_and_non_members() -> None:
    async def scenario() -> None:
        from app.vn100 import VN100_SYMBOLS

        stamp = datetime(2026, 9, 24, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))

        def make(ticker: str, components: dict[str, float | None], change: float) -> Signal:
            return Signal(ticker, ticker, None, 10_000, 70, [], stamp, stamp,
                          "v1", "1D", SignalStatus.WATCH_ONLY, components, change)

        provider = StrategySignalProvider(engine=SimpleNamespace(), auto_refresh=False)
        provider.get_market_signals = AsyncMock(return_value=[
            make("ACB", {"T": 70, "F": 70, "M": 70}, 1.0),
            make("FPT", {"T": 70, "F": None, "M": 70}, -1.0),
            make("HPG", {"T": 70, "F": 70, "M": 70}, 0.0),
            make("AAA", {"T": 70, "F": 70, "M": 70}, 1.0),
        ])

        result = await SignalService(provider).scan(vn100_only=True)

        assert [signal.ticker for signal in result] == ["ACB"]
        assert provider.get_market_signals.await_args.kwargs["symbols"] == VN100_SYMBOLS

    asyncio.run(scenario())


def test_scan_reports_missing_eod_data_instead_of_empty_results(tmp_path: Path) -> None:
    class MissingEodProvider:
        name = "strategy"

        async def get_market_signals(self):
            raise ProviderError("Chưa có VNINDEX cuối ngày hợp lệ cho 2026-09-23")

    async def scenario() -> None:
        service = SignalService(MissingEodProvider())
        with pytest.raises(ProviderError, match="2026-09-23"):
            await service.scan()

        database = Database(tmp_path / "scan.db")
        database.initialize()
        subscriptions = SubscriptionService(
            UserRepository(database), WatchlistRepository(database), SettingsRepository(database)
        )
        handlers = BotHandlers(Settings("fake-token", database_path=tmp_path / "scan.db"),
                               service, subscriptions, database)
        reply = AsyncMock()
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=1),
            effective_user=SimpleNamespace(username="tester"),
            effective_message=SimpleNamespace(reply_text=reply),
        )
        await handlers.scan(update, SimpleNamespace())
        assert "2026-09-23" in reply.await_args.args[0]

    asyncio.run(scenario())


def test_scan_sends_separate_paper_trade_prompt_and_portfolio_button(tmp_path: Path) -> None:
    async def scenario() -> None:
        from app.market_store import MarketStore

        database = Database(tmp_path / "scan_prompt.db")
        database.initialize()
        store = MarketStore(database.path)
        store.initialize()
        with store.connect() as conn:
            conn.execute("INSERT INTO market_symbols VALUES('AAA','HSX','STOCK')")
            conn.execute("""INSERT INTO market_indices(symbol,trade_date,close,is_final)
                VALUES('VNINDEX','2026-09-24',1800,1)""")
        subscriptions = SubscriptionService(
            UserRepository(database), WatchlistRepository(database), SettingsRepository(database))
        provider = StrategySignalProvider(engine=VietcapStrategyEngine(store),
                                          now=datetime(2026, 9, 25, 10,
                                                       tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")))
        stamp = datetime(2026, 9, 24, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
        provider.get_market_signals = AsyncMock(return_value=[
            Signal("vn100-acb", "ACB", Action.BUY, 25_000, 82, [], stamp, stamp,
                   "v1", "1D", SignalStatus.SUCCESS,
                   {"T": 80, "F": 82, "M": 85}, 1.25)
        ])
        handler = BotHandlers(Settings("fake-token", database_path=database.path),
                              SignalService(provider), subscriptions, database)
        reply = AsyncMock()
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=1, type="private"),
            effective_user=SimpleNamespace(username="tester"),
            effective_message=SimpleNamespace(reply_text=reply),
        )
        await handler.scan(update, SimpleNamespace())
        assert reply.await_count == 2
        assert "VN100" in reply.await_args_list[0].args[0]
        assert "ACB: +1.25% · 82.00/100" in reply.await_args_list[0].args[0]
        assert "Bạn muốn mua/bán ảo" in reply.await_args_list[1].args[0]
        buttons = reply.await_args_list[1].kwargs["reply_markup"].inline_keyboard
        assert buttons[1][0].callback_data == "paper:account"

    asyncio.run(scenario())


def test_polling_retries_transient_telegram_bootstrap_errors(monkeypatch, tmp_path: Path) -> None:
    settings = Settings("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrst",
                        database_path=tmp_path / "bot.db")
    args = SimpleNamespace(env_file=".env", init_db=False, import_market_data=False,
                           check_config=False, check_token=False, run_alert_once=False)
    application = SimpleNamespace(run_polling=Mock())
    monkeypatch.setattr("app.main.parse_args", lambda: args)
    monkeypatch.setattr("app.main.Settings.from_env", lambda *a, **k: settings)
    monkeypatch.setattr("app.main.configure_logging", lambda level: None)
    monkeypatch.setattr("app.main.build_application", lambda value: application)

    assert main() == 0
    assert application.run_polling.call_args.kwargs["bootstrap_retries"] == 5
