from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo
import asyncio

import pytest

from app.backtest_v1 import BacktestConfig
from app.market_store import MarketStore
from app.paper_broker import PaperBroker, PaperOrderError, format_paper_account
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
    assert "AAA" in format_paper_account(account)
    sell = broker.place(101, "AAA", "SELL", 100)
    add_bar(db, "AAA", "2026-09-24", 11_000, 11_100)
    clock[0] = datetime(2026, 9, 24, 17, tzinfo=TZ)
    assert broker.process(101)[0]["id"] == sell
    assert broker.snapshot(101)["positions"] == []
    assert broker.orders(101)[0]["realized_pnl"] is not None


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


def test_price_gap_can_reject_without_negative_cash(tmp_path: Path) -> None:
    db, broker, clock = setup(tmp_path)
    broker.place(101, "AAA", "BUY", 9_000)
    add_bar(db, "AAA", "2026-09-23", 20_000, 20_000)
    clock[0] = datetime(2026, 9, 23, 17, tzinfo=TZ)
    event = broker.process(101)[0]
    assert event["status"] == "REJECTED"
    assert broker.snapshot(101)["cash"] == 100_000_000
    assert broker.snapshot(101)["positions"] == []


def test_telegram_paper_commands_use_local_paper_broker(tmp_path: Path) -> None:
    from app.bot.handlers import BotHandlers
    from app.config import Settings
    from app.providers.strategy_signal_provider import StrategySignalProvider
    from app.services import SignalService, SubscriptionService
    from app.storage import SettingsRepository, WatchlistRepository
    from app.strategy_engine import VietcapStrategyEngine

    async def scenario() -> None:
        db, _, _ = setup(tmp_path)
        store = MarketStore(db.path)
        provider = StrategySignalProvider(engine=VietcapStrategyEngine(store), auto_refresh=False)
        subscriptions = SubscriptionService(UserRepository(db), WatchlistRepository(db), SettingsRepository(db))
        handler = BotHandlers(Settings("fake-token", signal_provider="strategy", database_path=db.path),
                              SignalService(provider), subscriptions, db)
        reply = AsyncMock()
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=101),
                                 effective_user=SimpleNamespace(username="first"),
                                 effective_message=SimpleNamespace(reply_text=reply))
        with patch("app.bot.handlers.PaperBroker",
                   side_effect=lambda database, config: PaperBroker(
                       database, config, now=lambda: datetime(2026, 9, 22, 10, tzinfo=TZ))):
            await handler.paper(update, SimpleNamespace(args=[]))
            assert "TÀI KHOẢN GIAO DỊCH MÔ PHỎNG" in reply.await_args.args[0]
            await handler.paper_buy(update, SimpleNamespace(args=["AAA", "100"]))
            assert "Đã xếp lệnh ảo" in reply.await_args.args[0]
            await handler.paper_orders(update, SimpleNamespace(args=[]))
            assert "BUY AAA 100 cp" in reply.await_args.args[0]

    asyncio.run(scenario())
