from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from app.backtest_v1 import BacktestConfig
from app.model_portfolio import format_model_portfolio, propose_portfolio, render_model_portfolio
from app.models import Action, Signal, SignalStatus


DAY = "2026-09-21"
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def signal(ticker: str) -> Signal:
    return Signal(f"id-{ticker}", ticker, Action.BUY, 10_000, 85,
                  ["Market regime: BULL"], datetime(2026, 9, 21, 15, tzinfo=TZ),
                  datetime(2026, 9, 22, tzinfo=TZ), "test", "1D", SignalStatus.SUCCESS)


def engine(sector: str | None, adjusted: bool = True):
    dates = pd.date_range(end=DAY, periods=30, freq="D")
    frame = pd.DataFrame({"date": dates, "open": 9_800.0, "high": 10_200.0,
                          "low": 9_700.0, "close": 10_000.0, "display_close": 10_000.0,
                          "volume": 1_000_000, "has_adjusted_history": adjusted})
    return SimpleNamespace(universe=lambda: {"AAA": "HSX"},
                           _sector_map=lambda: {"AAA": sector} if sector else {},
                           prices=lambda ticker, as_of: (frame, False))


def test_missing_classification_keeps_cash_and_renders() -> None:
    result = propose_portfolio([signal("AAA")], engine(None), BacktestConfig(), DAY)
    assert not result.positions
    assert result.cash == 100_000_000
    assert result.missing_sector == 1
    assert "Thiếu ngành" in format_model_portfolio(result)
    assert render_model_portfolio(result).getvalue().startswith(b"\x89PNG")


def test_missing_adjusted_prices_never_allocates() -> None:
    result = propose_portfolio([signal("AAA")], engine("Banking", False), BacktestConfig(), DAY)
    assert not result.positions
    assert result.missing_adjusted == 1


def test_proposal_obeys_lot_and_sector_limit() -> None:
    cfg = BacktestConfig()
    result = propose_portfolio([signal("AAA")], engine("Banking"), cfg, DAY)
    assert len(result.positions) == 1
    item = result.positions[0]
    assert item.shares % cfg.lot_size == 0
    assert item.estimated_cost <= cfg.initial_capital * cfg.max_sector_fraction
    assert item.estimated_cost + result.cash == cfg.initial_capital


def test_old_signal_is_not_used_for_new_session() -> None:
    result = propose_portfolio([signal("AAA")], engine("Banking"), BacktestConfig(), "2026-09-22")
    assert result.buy_count == 0
    assert not result.positions
