from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.bot.formatter import format_signal
from app.models import Action, SignalStatus
from app.providers.strategy_signal_provider import StrategySignalProvider
from app.services import SignalService
from app.strategy_engine import (SAMPLE_BOOK, SampleStrategyEngine, StrategyDataError,
                                 VietcapStrategyEngine, calculate_total_score, market_regime)

import pandas as pd


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def test_total_score_excludes_entry() -> None:
    assert calculate_total_score(85, 80, 75) == 80.31
    with pytest.raises(StrategyDataError):
        calculate_total_score(101, 80, 75)


def test_momentum_uses_equal_five_and_twenty_session_relative_strength() -> None:
    engine = SampleStrategyEngine()
    dates = pd.bdate_range("2026-01-01", periods=21)
    stock = [100.0] + [90.0] * 14 + [80.0] * 5 + [90.0]
    prices = pd.DataFrame({"date": dates, "close": stock})
    index = pd.DataFrame({"date": dates, "close": [100.0] * 21})
    assert engine.momentum(prices, index) == 50.0


def test_missing_breadth_or_benchmark_is_unknown_not_neutral() -> None:
    dates = pd.bdate_range("2025-01-01", periods=220)
    index = pd.DataFrame({"date": dates, "close": range(100, 320)})
    assert market_regime(index, None, dates[-1]) == "UNKNOWN"
    assert market_regime(index.iloc[:-1], (60, 100), dates[-1]) == "UNKNOWN"
    assert market_regime(index, (60, 100), dates[-1]) == "BULL"


def test_live_technical_only_buy_requires_entry_regime_and_hose() -> None:
    class Store:
        breadth_value = (60, 100)

        def breadth(self, trade_day):
            return self.breadth_value

    class Engine(VietcapStrategyEngine):
        def __init__(self):
            self.store = Store()
            self.exchange = "HSX"
            self.entry = "ENTRY"
            self.exit = False
            self.lag_days = 0

        def universe(self):
            return {"AAA": self.exchange}

        def prices(self, ticker, as_of):
            return pd.DataFrame([{"date": as_of - pd.Timedelta(days=self.lag_days), "close": 100, "display_close": 100,
                                  "has_adjusted_history": True}]), False

        def technical(self, prices):
            return {"trend": 80.0, "entry": {"status": self.entry, "reasons": []},
                    "trend_exit": self.exit}

        def index(self, as_of, exchange="HSX"):
            dates = pd.bdate_range(end=as_of, periods=220)
            return pd.DataFrame({"date": dates, "close": range(100, 320)})

        def fundamental(self, ticker, as_of, exchange):
            raise StrategyDataError("Không có F hợp lệ")

    engine = Engine()
    provider = StrategySignalProvider(engine=engine, now=datetime(2026, 9, 21, 16, tzinfo=VN_TZ), auto_refresh=False)
    assert provider._build_signal("AAA").action is Action.BUY
    assert provider._build_signal("AAA", refresh_error="fundamental unavailable").action is None
    engine.entry = "NO_ENTRY"
    assert provider._build_signal("AAA").action is None
    engine.entry = "ENTRY"
    engine.exchange = "HNX"
    assert provider._build_signal("AAA").action is None
    engine.exchange = "HSX"
    engine.store.breadth_value = (0, 0)
    assert provider._build_signal("AAA").action is None
    engine.exit = True
    assert provider._build_signal("AAA").action is Action.SELL
    engine.lag_days = 1
    stale = provider._build_signal("AAA", breadth_snapshot=("2026-09-21", (60, 100)),
                                   as_of=pd.Timestamp("2026-09-21"))
    assert stale.status is SignalStatus.STALE_DATA
    assert stale.action is None


def test_industry_valuation_uses_only_announced_same_period_peers() -> None:
    reports = {
        "AAA": pd.DataFrame([
            {"report_type": "quarterly", "report_period": "2024Q1", "announcement_date": "2024-05-01",
             "revenue": 100, "net_profit": 10, "total_equity": 100, "operating_cash_flow": 5,
             "eps": 1, "pe": 20, "pb": 3, "roe": 0.2, "total_debt": 50},
            {"report_type": "quarterly", "report_period": "2025Q1", "announcement_date": "2025-05-01",
             "revenue": 120, "net_profit": 12, "total_equity": 100, "operating_cash_flow": 5,
             "eps": 1, "pe": 20, "pb": 3, "roe": 0.2, "total_debt": 50},
        ]),
        "BBB": pd.DataFrame([
            {"report_type": "quarterly", "report_period": "2025Q1", "announcement_date": "2025-05-15",
             "pe": 15, "pb": 2},
        ]),
    }

    class Engine(SampleStrategyEngine):
        def _sector_map(self):
            return {"AAA": "Technology", "BBB": "Technology"}

        def _fundamental_frame(self, ticker):
            return reports[ticker].copy()

    engine = Engine(valuation_verified=True)
    before_peer = engine.fundamental("AAA", pd.Timestamp("2025-05-10"), "HSX")
    after_peer = engine.fundamental("AAA", pd.Timestamp("2025-05-20"), "HSX")
    assert before_peer == 100.0  # Valuation is omitted, not scored as zero.
    assert after_peer < before_peer  # BBB's lower ratios enter only after announcement.
    assert Engine().fundamental("AAA", pd.Timestamp("2025-05-20"), "HSX") == 100.0
    with pytest.raises(StrategyDataError):
        engine.fundamental("AAA", pd.Timestamp("2025-04-30"), "HSX")


def test_workbook_loader_never_reads_future_targets() -> None:
    if not SAMPLE_BOOK.is_file():
        pytest.skip("User sample workbook not present")
    frame = SampleStrategyEngine().sample()
    assert len(frame["symbol"].unique()) == 15
    assert "target_return_5d" not in frame.columns
    assert "target_up_5d" not in frame.columns
    assert "split" not in frame.columns


def test_sample_provider_watch_only_without_live_orders() -> None:
    if not SAMPLE_BOOK.is_file():
        pytest.skip("User sample workbook not present")

    async def scenario() -> None:
        historic = StrategySignalProvider(engine=SampleStrategyEngine(), now=datetime(2026, 9, 11, 16, tzinfo=VN_TZ), auto_refresh=False)
        fpt = await historic.get_signal("FPT")
        assert fpt.status is SignalStatus.WATCH_ONLY
        assert fpt.action is None
        assert 0 <= fpt.score <= 100
        assert "E=" in " ".join(fpt.reasons)

        current = StrategySignalProvider(engine=SampleStrategyEngine(), now=datetime(2026, 9, 21, 16, tzinfo=VN_TZ), auto_refresh=False)
        stale = await current.get_signal("FPT")
        assert stale.status is SignalStatus.STALE_DATA
        assert stale.action is None
        assert "Dữ liệu đã cũ" in format_signal(stale)

        upcom = await current.get_signal("A32")
        assert upcom.status is SignalStatus.WATCH_ONLY
        assert upcom.action is None
        assert "Theo dõi" in format_signal(upcom)

        service = SignalService(current)
        lookup = await service.lookup("FPT")
        assert lookup.signal is not None
        assert lookup.error_code is None
        assert fpt.action is None  # Review mode cannot issue BUY/SELL.

    asyncio.run(scenario())
