from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo
import asyncio

import pytest

from app.backtest_v1 import BacktestConfig
from app.market_store import MarketStore
from app.models import Action, Signal, SignalStatus
from app.paper_broker import (PaperBroker, PaperOrderError, format_paper_account,
                              format_paper_orders, render_paper_portfolio)
from app.storage import Database, UserRepository


TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def setup(tmp_path: Path):
    db = Database(tmp_path / "paper.db")
    db.initialize()
    MarketStore(db.path).initialize()
    users = UserRepository(db)
    users.upsert(101, "first")
    users.upsert(202, "second")
    clock = [datetime(2026, 9, 22, 10, tzinfo=TZ)]
    broker = PaperBroker(db, BacktestConfig(), now=lambda: clock[0])
    broker.ensure_account(101)
    broker.ensure_account(202)
    with db.connect() as conn:
        conn.executemany("INSERT INTO market_symbols VALUES(?,?,'STOCK')", [("AAA", "HSX"), ("BBB", "HSX")])
    add_bar(db, "AAA", "2026-09-21", 10_000, 10_000)
    add_bar(db, "BBB", "2026-09-21", 20_000, 20_000)
    return db, broker, clock


def add_bar(db: Database, ticker: str, day: str, opened: int, closed: int):
    with db.connect() as conn:
        conn.execute("""INSERT INTO market_prices
            (symbol,trade_date,exchange,open,high,low,close,volume,is_final,quality_flags,source)
            VALUES(?,?,'HSX',?,?,?,?,1000000,1,NULL,'test')""",
            (ticker, day, opened, max(opened, closed), min(opened, closed), closed))


def test_buy_fill_sell_is_idempotent_and_private(tmp_path: Path) -> None:
    db, broker, clock = setup(tmp_path)
    buy = broker.place(101, "AAA", "BUY", 100)
    assert broker.process(101) == []
    assert broker.snapshot(101)["cash"] == 100_000_000
    add_bar(db, "AAA", "2026-09-23", 10_500, 10_800)
    clock[0] = datetime(2026, 9, 23, 17, tzinfo=TZ)
    assert broker.process(101)[0]["id"] == buy
    assert broker.process(101) == []
    account = broker.snapshot(101)
    assert account["positions"][0]["shares"] == 100
    assert account["cash"] < 100_000_000
    assert broker.snapshot(202)["positions"] == []
    assert broker.orders(202) == []
    account_text = format_paper_account(account)
    assert "AAA" in account_text
    assert "/papercapital" not in account_text
    assert "/paperposition" not in account_text
    assert "/paperorders" not in account_text
    assert "/paperhistory" not in account_text
    assert "/papercancel" not in account_text
    sell = broker.place(101, "AAA", "SELL", 100)
    add_bar(db, "AAA", "2026-09-24", 11_000, 11_100)
    clock[0] = datetime(2026, 9, 24, 17, tzinfo=TZ)
    assert broker.process(101)[0]["id"] == sell
    assert broker.snapshot(101)["positions"] == []
    assert broker.orders(101)[0]["realized_pnl"] is not None


def test_current_api_quote_fills_immediately_and_updates_portfolio(tmp_path: Path) -> None:
    _, broker, clock = setup(tmp_path)
    event = broker.execute_at_quote(
        101, "AAA", "BUY", 100, 10_250, clock[0], "DNSE")

    assert event["status"] == "FILLED"
    assert event["fill_price"] == 10_250
    assert broker.orders(101, pending_only=True) == []
    snapshot = broker.snapshot(101)
    assert snapshot["positions"][0]["ticker"] == "AAA"
    assert snapshot["positions"][0]["shares"] == 100
    assert snapshot["cash"] < 100_000_000
    order = broker.orders(101)[0]
    assert order["quote_source"] == "DNSE"
    assert order["quote_time"] == clock[0].isoformat(timespec="seconds")

    sell = broker.execute_at_quote(
        101, "AAA", "SELL", 100, 11_000, clock[0], "DNSE")
    assert sell["realized_pnl"] is not None
    assert broker.snapshot(101)["positions"] == []


def test_reject_overcommit_oversell_and_bad_lot(tmp_path: Path) -> None:
    _, broker, _ = setup(tmp_path)
    with pytest.raises(PaperOrderError, match="bội số"):
        broker.place(101, "AAA", "BUY", 25)
    with pytest.raises(PaperOrderError, match="không bán khống"):
        broker.place(101, "AAA", "SELL", 100)
    broker.place(101, "AAA", "BUY", 9_000)
    with pytest.raises(PaperOrderError, match="Tiền mặt khả dụng"):
        broker.place(101, "BBB", "BUY", 1_000)


def test_cancel_only_own_pending_order(tmp_path: Path) -> None:
    _, broker, _ = setup(tmp_path)
    order = broker.place(101, "AAA", "BUY", 100)
    assert broker.cancel(202, order) is False
    assert broker.cancel(101, order) is True
    assert broker.cancel(101, order) is False
    assert broker.orders(101, pending_only=True) == []
    assert broker.place(101, "AAA", "BUY", 100) > order


def test_cancel_pending_orders_by_ticker_only_in_own_account(tmp_path: Path) -> None:
    _, broker, _ = setup(tmp_path)
    broker.place(101, "AAA", "BUY", 100)
    broker.place(101, "AAA", "BUY", 100)
    broker.place(101, "BBB", "BUY", 100)
    broker.place(202, "AAA", "BUY", 100)

    assert broker.cancel_ticker(101, "AAA") == 2
    assert [order["ticker"] for order in broker.orders(101, pending_only=True)] == ["BBB"]
    assert [order["ticker"] for order in broker.orders(202, pending_only=True)] == ["AAA"]
    assert broker.cancel_ticker(101, "AAA") == 0


def test_paper_journal_preserves_signal_and_is_private(tmp_path: Path) -> None:
    db, broker, clock = setup(tmp_path)
    stamp = datetime(2026, 9, 21, 15, tzinfo=TZ)
    signal = Signal("s1", "AAA", Action.BUY, 10000, 80, [], stamp, stamp,
                    "v1", "1D", SignalStatus.SUCCESS, {"T": 80, "F": 80, "M": 80})
    order_id = broker.place(101, "AAA", "BUY", 100, signal)
    broker.record_plan(101, order_id, "Xu hướng tăng", "Giảm dưới hỗ trợ")
    with pytest.raises(PaperOrderError, match="không tìm thấy|Không tìm thấy"):
        broker.record_plan(202, order_id, "Sai", "Sai")
    with pytest.raises(PaperOrderError, match="đã khớp"):
        broker.record_review(101, order_id, "Đánh giá")
    add_bar(db, "AAA", "2026-09-23", 10_500, 10_800)
    clock[0] = datetime(2026, 9, 23, 17, tzinfo=TZ)
    broker.process(101)
    broker.record_review(101, order_id, "Tuân thủ kế hoạch")
    order = broker.orders(101)[0]
    assert order["signal_score"] == 80
    assert order["thesis"] == "Xu hướng tăng"
    assert order["reflection"] == "Tuân thủ kế hoạch"
    text = format_paper_orders([order], False)
    assert "Tín hiệu lúc đặt: BUY · 80.0/100" in text
    assert "Lý do: Xu hướng tăng" in text
    assert "Đánh giá sau lệnh: Tuân thủ kế hoạch" in text


def test_price_gap_can_reject_without_negative_cash(tmp_path: Path) -> None:
    db, broker, clock = setup(tmp_path)
    broker.place(101, "AAA", "BUY", 9_000)
    add_bar(db, "AAA", "2026-09-23", 20_000, 20_000)
    clock[0] = datetime(2026, 9, 23, 17, tzinfo=TZ)
    event = broker.process(101)[0]
    assert event["status"] == "REJECTED"
    assert broker.snapshot(101)["cash"] == 100_000_000
    assert broker.snapshot(101)["positions"] == []


def test_virtual_capital_and_imported_position_are_audited_and_private(tmp_path: Path) -> None:
    db, broker, _ = setup(tmp_path)
    broker.set_capital(101, 120_000_000)
    broker.set_position(101, "AAA", 200, 9_000)
    snapshot = broker.snapshot(101)
    assert snapshot["initial_cash"] == 121_800_000
    assert snapshot["cash"] == 120_000_000
    assert snapshot["positions"][0]["cost_basis"] == 1_800_000
    assert render_paper_portfolio(snapshot).getvalue().startswith(b"\x89PNG")
    assert broker.snapshot(202)["positions"] == []
    broker.set_position(101, "AAA", 100, 8_000)
    assert broker.snapshot(101)["initial_cash"] == 120_800_000
    with db.connect() as conn:
        adjustments = conn.execute("SELECT kind FROM paper_adjustments WHERE chat_id=101 ORDER BY id").fetchall()
    assert [row[0] for row in adjustments] == ["CAPITAL", "POSITION", "POSITION"]
    broker.set_position(101, "AAA", 0, 0)
    assert broker.snapshot(101)["positions"] == []
    assert broker.snapshot(101)["initial_cash"] == 120_000_000


def test_capital_cannot_remove_reserved_cash_and_position_cannot_change_with_pending_order(tmp_path: Path) -> None:
    _, broker, _ = setup(tmp_path)
    broker.place(101, "AAA", "BUY", 100)
    with pytest.raises(PaperOrderError, match="tiền mặt"):
        broker.set_capital(101, 100)
    with pytest.raises(PaperOrderError, match="lệnh chờ"):
        broker.set_position(101, "AAA", 100, 10_000)
    assert broker.snapshot(101)["cash"] == 100_000_000


def test_background_paper_notification_follows_finalized_open_and_is_once(tmp_path: Path) -> None:
    from app.main import _process_paper_orders_once

    async def scenario() -> None:
        db, broker, clock = setup(tmp_path)
        bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
        broker.place(101, "AAA", "BUY", 100)
        await _process_paper_orders_once(bot, broker)
        bot.send_message.assert_not_awaited()
        add_bar(db, "AAA", "2026-09-23", 10_500, 10_800)
        clock[0] = datetime(2026, 9, 23, 17, tzinfo=TZ)
        await _process_paper_orders_once(bot, broker)
        assert "đã khớp mô phỏng theo Open" in bot.send_message.await_args.kwargs["text"]
        assert "AAA" in bot.send_message.await_args.kwargs["text"]
        bot.send_photo.assert_awaited_once()
        assert broker.snapshot(101)["positions"][0]["shares"] == 100
        await _process_paper_orders_once(bot, broker)
        bot.send_message.assert_awaited_once()

    asyncio.run(scenario())


def test_rejected_virtual_order_is_reported_without_changing_balance(tmp_path: Path) -> None:
    from app.main import _process_paper_orders_once

    async def scenario() -> None:
        db, broker, clock = setup(tmp_path)
        bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
        broker.place(101, "AAA", "BUY", 9_000)
        add_bar(db, "AAA", "2026-09-23", 20_000, 20_000)
        clock[0] = datetime(2026, 9, 23, 17, tzinfo=TZ)
        await _process_paper_orders_once(bot, broker)
        assert "không khớp mô phỏng" in bot.send_message.await_args.kwargs["text"]
        bot.send_photo.assert_not_awaited()
        assert broker.snapshot(101)["cash"] == 100_000_000

    asyncio.run(scenario())


def test_telegram_paper_commands_use_local_paper_broker(tmp_path: Path) -> None:
    from app.bot.handlers import BotHandlers
    from app.config import Settings
    from app.providers.strategy_signal_provider import StrategySignalProvider
    from app.services import SignalService, SubscriptionService
    from app.storage import SettingsRepository, WatchlistRepository
    from app.strategy_engine import VietcapStrategyEngine

    async def scenario() -> None:
        db, legacy_broker, _ = setup(tmp_path)
        store = MarketStore(db.path)
        provider = StrategySignalProvider(engine=VietcapStrategyEngine(store), auto_refresh=False)
        subscriptions = SubscriptionService(UserRepository(db), WatchlistRepository(db), SettingsRepository(db))
        handler = BotHandlers(Settings("fake-token", signal_provider="strategy", database_path=db.path),
                              SignalService(provider), subscriptions, db)
        reply = AsyncMock()
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=101),
                                 effective_user=SimpleNamespace(username="first"),
                                 effective_message=SimpleNamespace(reply_text=reply))
        bot = SimpleNamespace(send_photo=AsyncMock(), send_document=AsyncMock())
        live_client = Mock()
        live_client.get_latest_trade.return_value = {
            "symbol": "AAA", "price_vnd": 10_250,
            "time": datetime(2026, 9, 22, 10, tzinfo=TZ), "source": "DNSE",
        }
        with (patch("app.bot.handlers.PaperBroker",
                    side_effect=lambda database, config: PaperBroker(
                        database, config, now=lambda: datetime(2026, 9, 22, 10, tzinfo=TZ))),
              patch("app.bot.handlers.DNSEMarketClient", return_value=live_client)):
            await handler.paper(update, SimpleNamespace(args=[], bot=bot))
            assert "TÀI KHOẢN GIAO DỊCH MÔ PHỎNG" in reply.await_args.args[0]
            bot.send_photo.assert_awaited_once()
            pending_id = legacy_broker.place(101, "BBB", "BUY", 100)
            legacy_broker.place(101, "BBB", "BUY", 100)
            await handler.paper_orders(update, SimpleNamespace(args=[]))
            cancel_button = reply.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
            callback_id = int(cancel_button.callback_data.rsplit(":", 1)[1])
            callback_update = SimpleNamespace(
                callback_query=SimpleNamespace(
                    data=f"paper:cancel:{callback_id}", answer=AsyncMock()),
                effective_chat=update.effective_chat,
                effective_user=update.effective_user,
                effective_message=update.effective_message,
            )
            await handler.paper_callback(callback_update, SimpleNamespace())
            assert f"Đã hủy lệnh chờ #{callback_id}." in reply.await_args.args[0]
            assert any(order["id"] == pending_id
                       for order in legacy_broker.orders(101, pending_only=True))
            await handler.paper_cancel(update, SimpleNamespace(args=["BBB"]))
            assert "Đã hủy 1 lệnh của BBB." in reply.await_args.args[0]
            assert legacy_broker.orders(101, pending_only=True) == []
            await handler.paper_buy(update, SimpleNamespace(args=["AAA", "100"]))
            assert "Đã khớp lệnh ảo" in reply.await_args.args[0]
            assert "Giá API: 10,250 VNĐ" in reply.await_args.args[0]
            assert "AAA: 100 cp" in reply.await_args.args[0]
            await handler.paper_capital(update, SimpleNamespace(args=["120000000"]))
            assert "120,000,000" in reply.await_args.args[0]
            await handler.paper_position(update, SimpleNamespace(args=["BBB", "200", "19000"]))
            assert "số liệu nhập tay" in reply.await_args.args[0]
            await handler.paper_history(update, SimpleNamespace(args=[]))
            assert "BUY AAA 100 cp" in reply.await_args.args[0]
            assert "DNSE 10,250" in reply.await_args.args[0]
            assert "Lý do:" not in reply.await_args.args[0]

    asyncio.run(scenario())
