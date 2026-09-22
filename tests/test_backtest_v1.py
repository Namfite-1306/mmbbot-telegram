from __future__ import annotations

import pandas as pd
import pytest

from app.backtest_v1 import BacktestConfig, adjusted_prices, order_shares, simulate


def test_adjusted_ohlc_uses_same_day_ratio() -> None:
    source = pd.DataFrame([{"symbol": "FPT", "sector_group": "Technology", "date": "2024-01-02",
                            "open": 100, "high": 110, "low": 90, "close": 100, "adj_close": 50, "volume": 1000}])
    result = adjusted_prices(source).iloc[0]
    assert (result["open"], result["high"], result["low"], result["close"]) == (50, 55, 45, 50)
    assert (result["raw_open"], result["raw_close"], result["adjustment_factor"]) == (100, 100, 0.5)


def test_missing_adjusted_close_is_not_filled_from_raw() -> None:
    source = pd.DataFrame([{"symbol": "FPT", "sector_group": "Technology", "date": "2024-01-02",
                            "open": 100, "high": 110, "low": 90, "close": 100, "adj_close": None, "volume": 1000}])
    assert adjusted_prices(source).empty


def test_order_sizing_respects_lot_sector_and_liquidity() -> None:
    cfg = BacktestConfig()
    row = pd.Series({"signal_atr": 1.0, "signal_adv20": 100_000, "signal_regime": "BULL"})
    assert order_shares(row, 100_000, 100_000, 0, 10, cfg) == 500
    assert order_shares(row, 100_000, 100_000, 34_500, 10, cfg) == 0


class _Fundamental:
    def fundamental(self, symbol: str, date: pd.Timestamp, exchange: str) -> float:
        assert exchange == "HSX"
        return 100.0


def _row(date: str, close: float, open_price: float, entry: bool, trend_exit: bool = False) -> dict:
    return {"date": pd.Timestamp(date), "symbol": "FPT", "sector_group": "Technology",
            "open": open_price, "close": close, "ready": True, "entry": entry,
            "regime": "BULL", "trend_score": 100.0, "momentum_score": 100.0,
            "atr14": 1.0, "adv20": 1_000_000, "trend_exit": trend_exit}


def test_close_stop_executes_only_at_next_open() -> None:
    cfg = BacktestConfig()
    features = pd.DataFrame([_row("2024-01-02", 10, 10, True),
                             _row("2024-01-03", 7, 10, False),
                             _row("2024-01-04", 9, 9, False)])
    trades, equity, metrics = simulate(features, cfg, "TFM", _Fundamental())
    assert len(trades) == 1
    assert trades.iloc[0]["entry_date"] == pd.Timestamp("2024-01-03")
    assert trades.iloc[0]["exit_date"] == pd.Timestamp("2024-01-04")
    assert trades.iloc[0]["exit_price"] == pytest.approx(9 * (1 - cfg.slippage_fraction))
    assert trades.iloc[0]["exit_reason"] == "STOP_CLOSE"
    assert metrics["closed_trades"] == 1


def test_trend_exit_executes_at_next_open() -> None:
    features = pd.DataFrame([_row("2024-01-02", 10, 10, True),
                             _row("2024-01-03", 10, 10, False),
                             _row("2024-01-04", 10, 10, False, True),
                             _row("2024-01-05", 11, 11, False)])
    trades, _, _ = simulate(features, BacktestConfig(), "TFM", _Fundamental())
    assert len(trades) == 1
    assert trades.iloc[0]["exit_date"] == pd.Timestamp("2024-01-05")
    assert trades.iloc[0]["exit_reason"] == "TREND_EXIT"


def test_backtest_fills_at_raw_open_not_adjusted_open() -> None:
    first = _row("2024-01-02", 5, 5, True)
    second = _row("2024-01-03", 4, 5, False)
    third = _row("2024-01-04", 4, 5, False)
    for row in (first, second, third):
        row.update(raw_open=20.0, raw_close=18.0, adjustment_factor=0.5)
    trades, _, _ = simulate(pd.DataFrame([first, second, third]), BacktestConfig(), "TFM", _Fundamental())
    assert len(trades) == 1
    assert trades.iloc[0]["entry_price"] == pytest.approx(20 * (1 + BacktestConfig().slippage_fraction))
    assert trades.iloc[0]["exit_price"] == pytest.approx(20 * (1 - BacktestConfig().slippage_fraction))


def test_invalid_fee_rejected(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"buy_fee_fraction": -0.01}', encoding="utf-8")
    with pytest.raises(ValueError):
        BacktestConfig.from_json(path)
