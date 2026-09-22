"""Point-in-time sample portfolio backtest. Run with ``python -m app.backtest_v1``.

The supplied Yahoo workbook is a selected sample, not an investable historical
universe. This module never reads its target/split columns. It runs independent
TFM and technical-only portfolios; neither is connected to live Telegram alerts.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from app.strategy_engine import (
    BOT_DIR, CRAWLER_DATA, SAMPLE_BOOK, TECHNICAL_SOURCE,
    SampleStrategyEngine, StrategyDataError, _load_module, calculate_total_score,
)


INDEX_BY_EXCHANGE = {"HSX": "VNINDEX", "HOSE": "VNINDEX", "HNX": "HNXINDEX", "UPCOM": "UPCOMINDEX"}
PRICE_COLS = ["symbol", "sector_group", "date", "open", "high", "low", "close", "adj_close", "volume"]


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 100_000_000
    risk_fraction: float = 0.01
    max_positions: int = 3
    max_sector_fraction: float = 0.35
    max_order_adv_fraction: float = 0.05
    lot_size: int = 100
    buy_fee_fraction: float = 0.0015
    sell_fee_fraction: float = 0.0015
    sell_tax_fraction: float = 0.001
    slippage_fraction: float = 0.0005
    minimum_composite_score: float = 75.0
    minimum_technical_only_score: float = 75.0

    @classmethod
    def from_json(cls, path: Path) -> "BacktestConfig":
        values = json.loads(path.read_text(encoding="utf-8"))
        unknown = set(values) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Cấu hình không nhận diện: {sorted(unknown)}")
        result = cls(**values)
        if result.initial_capital <= 0 or result.lot_size <= 0 or result.max_positions <= 0:
            raise ValueError("Vốn, lô và số vị thế phải dương")
        for name in ("risk_fraction", "max_sector_fraction", "max_order_adv_fraction"):
            if not 0 < getattr(result, name) <= 1:
                raise ValueError(f"{name} phải trong (0, 1]")
        for name in ("buy_fee_fraction", "sell_fee_fraction", "sell_tax_fraction", "slippage_fraction"):
            if not 0 <= getattr(result, name) < 1:
                raise ValueError(f"{name} phải trong [0, 1)")
        return result


def adjusted_prices(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep actual execution prices separate from adjusted indicator prices."""
    frame = frame[PRICE_COLS].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    for col in PRICE_COLS[3:]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=PRICE_COLS)
    frame = frame[(frame["close"] > 0) & (frame["adj_close"] > 0) & (frame["volume"] > 0)]
    frame["raw_open"] = frame["open"]
    frame["raw_close"] = frame["close"]
    factor = frame["adj_close"] / frame["close"]
    frame["adjustment_factor"] = factor
    for col in ("open", "high", "low"):
        frame[col] *= factor
    frame["close"] = frame["adj_close"]
    frame = frame[(frame["low"] > 0) & (frame["low"] <= frame[["open", "close"]].min(axis=1))
                  & (frame["high"] >= frame[["open", "close"]].max(axis=1))]
    return frame.sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"], keep="last")


def load_market_breadth(data_dir: Path, end_date: pd.Timestamp) -> pd.DataFrame:
    """Advancers / HOSE common stocks traded on both adjacent index sessions."""
    universe = pd.read_csv(data_dir / "symbol_info_cache.csv", usecols=["symbol", "exchange", "security_type"])
    allowed = set(universe.loc[universe["security_type"].eq("STOCK")
                               & universe["exchange"].isin(["HSX", "HOSE"]), "symbol"])
    index_dates = pd.read_csv(data_dir / "index_data" / "VNINDEX.csv",
                              usecols=["trade_date", "is_final", "data_quality_flags"])
    index_dates["date"] = pd.to_datetime(index_dates["trade_date"], errors="coerce").dt.normalize()
    index_dates = index_dates[index_dates["date"].notna()
                              & index_dates["is_final"].astype(str).str.lower().eq("true")
                              & index_dates["data_quality_flags"].isna()]
    sessions = index_dates[["date"]].drop_duplicates().sort_values("date")
    sessions["previous_session"] = sessions["date"].shift(1)
    parts = []
    for path in (data_dir / "market_data").glob("*.csv"):
        if path.stem not in allowed:
            continue
        try:
            item = pd.read_csv(path, usecols=["trade_date", "close", "is_final", "data_quality_flags"])
        except (OSError, ValueError):
            continue
        item["date"] = pd.to_datetime(item["trade_date"], errors="coerce").dt.normalize()
        item["close"] = pd.to_numeric(item["close"], errors="coerce")
        item = item[item["date"].le(end_date) & item["date"].notna() & item["close"].gt(0)
                    & item["data_quality_flags"].isna()
                    & item["is_final"].astype(str).str.lower().eq("true")]
        item = item.sort_values("date").drop_duplicates("date", keep="last")
        item["previous"] = item["close"].shift(1)
        item["previous_date"] = item["date"].shift(1)
        item = item.merge(sessions, on="date", how="inner")
        item = item[item["previous_date"].eq(item["previous_session"])]
        if not item.empty:
            parts.append(item[["date", "close", "previous"]])
    if not parts:
        raise StrategyDataError("Không có dữ liệu breadth toàn thị trường")
    all_prices = pd.concat(parts, ignore_index=True)
    all_prices["advanced"] = all_prices["close"].gt(all_prices["previous"])
    breadth = all_prices.groupby("date").agg(breadth=("advanced", "mean"), breadth_symbols=("advanced", "size"))
    return breadth.reset_index()


def load_index(data_dir: Path, exchange: str, breadth: pd.DataFrame) -> pd.DataFrame:
    name = INDEX_BY_EXCHANGE[exchange]
    path = data_dir / "index_data" / f"{name}.csv"
    if not path.is_file():
        raise StrategyDataError(f"Thiếu benchmark {name} cho sàn {exchange}")
    index = pd.read_csv(path, usecols=["trade_date", "close", "is_final", "data_quality_flags"])
    index["date"] = pd.to_datetime(index["trade_date"], errors="coerce").dt.normalize()
    index["index_close"] = pd.to_numeric(index["close"], errors="coerce")
    index = index[index["date"].notna() & index["index_close"].gt(0)
                  & index["data_quality_flags"].isna()
                  & index["is_final"].astype(str).str.lower().eq("true")]
    index = index.sort_values("date").drop_duplicates("date", keep="last")
    index["index_ema50"] = index["index_close"].ewm(span=50, adjust=False, min_periods=50).mean()
    index["index_ema200"] = index["index_close"].ewm(span=200, adjust=False, min_periods=200).mean()
    index = index.merge(breadth, on="date", how="left")
    index["regime"] = "NEUTRAL"
    index.loc[index["index_ema50"].lt(index["index_ema200"]), "regime"] = "BEAR"
    bull = index["index_close"].gt(index["index_ema50"]) & index["index_ema50"].gt(index["index_ema200"]) & index["breadth"].ge(0.5)
    index.loc[bull, "regime"] = "BULL"
    index.loc[index["index_ema200"].isna() | index["breadth"].isna(), "regime"] = "UNKNOWN"
    return index[["date", "index_close", "breadth", "breadth_symbols", "regime"]]


def build_features(prices: pd.DataFrame, index: pd.DataFrame) -> pd.DataFrame:
    technical = _load_module(str(TECHNICAL_SOURCE), "finbot_technical_engine")
    frames = []
    for symbol, item in prices.groupby("symbol", sort=True):
        item = item.sort_values("date").copy()
        item["ema20"] = technical.calculate_ema_by_symbol(item, "close", 20)
        item["ema50"] = technical.calculate_ema_by_symbol(item, "close", 50)
        item["ema200"] = technical.calculate_ema_by_symbol(item, "close", 200)
        item["rsi14"] = technical.calculate_rsi_by_symbol(item, "close", 14)
        item["atr14"] = technical.calculate_atr_by_symbol(item, 14)
        item["volume_ratio"] = technical.calculate_volume_ratio_by_symbol(item, 20)
        item["breakout_level"] = technical.calculate_breakout_level_by_symbol(item, 20)
        item["adv20"] = (item["raw_close"] * item["volume"]).shift(1).rolling(20, min_periods=20).mean()
        item = item.merge(index, on="date", how="left", validate="one_to_one")
        item["stock_ret5"] = item["close"] / item["close"].shift(5) - 1
        item["stock_ret20"] = item["close"] / item["close"].shift(20) - 1
        item["index_ret5"] = item["index_close"] / item["index_close"].shift(5) - 1
        item["index_ret20"] = item["index_close"] / item["index_close"].shift(20) - 1
        item["trend_score"] = (30 * item["close"].gt(item["ema20"]) + 30 * item["ema20"].gt(item["ema50"])
                               + 25 * item["ema50"].gt(item["ema200"]) + 15 * item["rsi14"].between(50, 70))
        item["momentum_score"] = (50 * item["stock_ret5"].gt(item["index_ret5"])
                                  + 50 * item["stock_ret20"].gt(item["index_ret20"]))
        distance = (item["close"] - item["ema20"]) / item["atr14"]
        item["entry"] = (item["close"].gt(item["breakout_level"]) & item["volume_ratio"].ge(1.5)
                         & item["close"].gt(item["ema20"]) & item["ema20"].gt(item["ema50"])
                         & item["rsi14"].gt(50) & distance.between(0, 3))
        item["trend_exit"] = (item["close"].lt(item["ema20"])
                              & item["close"].shift(1).lt(item["ema20"].shift(1))
                              & item["rsi14"].lt(50))
        item["ready"] = item[["ema200", "rsi14", "atr14", "breakout_level", "adv20",
                               "index_ret20", "breadth"]].notna().all(axis=1)
        frames.append(item)
    return pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"]).reset_index(drop=True)


def order_shares(row: pd.Series, cash: float, equity: float, sector_exposure: float,
                 entry_price: float, cfg: BacktestConfig) -> int:
    """Apply risk, cash, industry and ADV caps to an entry at next-session open."""
    factor = float(row.get("signal_adjustment_factor", 1.0))
    atr = float(row["signal_atr"]) / factor if factor > 0 else math.nan
    if not math.isfinite(atr) or atr <= 0 or entry_price <= 0:
        return 0
    scale = 0.5 if row["signal_regime"] == "NEUTRAL" else 1.0
    risk_cap = equity * cfg.risk_fraction * scale / (2 * atr)
    sector_cap = max(0, equity * cfg.max_sector_fraction - sector_exposure) / entry_price
    adv_cap = cfg.max_order_adv_fraction * float(row["signal_adv20"]) / entry_price
    cash_cap = cash / (entry_price * (1 + cfg.buy_fee_fraction))
    shares = min(risk_cap, sector_cap, adv_cap, cash_cap)
    return max(0, int(shares // cfg.lot_size) * cfg.lot_size) if math.isfinite(shares) else 0


def simulate(features: pd.DataFrame, cfg: BacktestConfig, branch: str,
             fundamental_engine: SampleStrategyEngine) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Signal on close T; entry and both exit types execute at next valid open."""
    cash = float(cfg.initial_capital)
    positions: dict[str, dict] = {}
    pending_entry: dict[str, dict] = {}
    pending_exit: dict[str, str] = {}
    trades: list[dict] = []
    equity_rows: list[dict] = []
    latest_close: dict[str, float] = {}
    fund_cache: dict[tuple[str, str], float | None] = {}
    for date, day in features.groupby("date", sort=True):
        current = {str(row["symbol"]): row for _, row in day.iterrows()}
        for symbol in list(pending_exit):
            if symbol not in current or symbol not in positions:
                continue
            row = current[symbol]
            open_price = float(row.get("raw_open", row["open"]))
            if not math.isfinite(open_price) or open_price <= 0:
                continue
            position = positions.pop(symbol)
            fill = open_price * (1 - cfg.slippage_fraction)
            proceeds = position["shares"] * fill * (1 - cfg.sell_fee_fraction - cfg.sell_tax_fraction)
            cash += proceeds
            trades.append({"branch": branch, "symbol": symbol, "sector": position["sector"],
                           "signal_date": position["signal_date"], "entry_date": position["entry_date"],
                           "exit_date": date, "entry_price": position["entry_price"], "exit_price": fill,
                           "shares": position["shares"], "entry_cost": position["entry_cost"],
                           "net_pnl": proceeds - position["entry_cost"], "exit_reason": pending_exit.pop(symbol),
                           "score": position["score"]})
        for symbol in list(pending_entry):
            if symbol not in current:
                continue
            order = pending_entry.pop(symbol)
            if symbol in positions or len(positions) >= cfg.max_positions:
                continue
            row = current[symbol]
            if pd.isna(row["sector_group"]):
                continue
            open_price = float(row.get("raw_open", row["open"]))
            if not math.isfinite(open_price) or open_price <= 0:
                continue
            fill = open_price * (1 + cfg.slippage_fraction)
            equity = cash + sum(p["shares"] * (p["entry_price"] if p["entry_date"] == date else
                                                  latest_close.get(sym, p["entry_price"]))
                                for sym, p in positions.items())
            sector_exposure = sum(p["shares"] * (p["entry_price"] if p["entry_date"] == date else
                                                        latest_close.get(sym, p["entry_price"]))
                                  for sym, p in positions.items() if p["sector"] == row["sector_group"])
            sizing = pd.Series({"signal_atr": order["atr"], "signal_adjustment_factor": order["factor"],
                                "signal_adv20": order["adv20"],
                                "signal_regime": order["regime"]})
            shares = order_shares(sizing, cash, equity, sector_exposure, fill, cfg)
            if not shares:
                continue
            cost = shares * fill * (1 + cfg.buy_fee_fraction)
            cash -= cost
            positions[symbol] = {"shares": shares, "sector": row["sector_group"], "entry_price": fill,
                                 "entry_cost": cost, "entry_date": date, "signal_date": order["date"],
                                 "stop_adjusted": fill * order["factor"] - 2 * order["atr"],
                                 "score": order["score"]}
        # Today's close becomes observable only after next-open orders execute.
        candidates: list[tuple[float, str, pd.Series]] = []
        for symbol, row in current.items():
            latest_close[symbol] = float(row.get("raw_close", row["close"]))
        for symbol, position in positions.items():
            if symbol not in current or symbol in pending_exit:
                continue
            row = current[symbol]
            if float(row["close"]) <= position["stop_adjusted"]:
                pending_exit[symbol] = "STOP_CLOSE"
            elif bool(row["trend_exit"]):
                pending_exit[symbol] = "TREND_EXIT"
        equity = cash + sum(p["shares"] * latest_close.get(sym, p["entry_price"])
                            for sym, p in positions.items())
        equity_rows.append({"date": date, "branch": branch, "cash": cash, "equity": equity,
                            "positions": len(positions)})
        for symbol, row in current.items():
            if symbol in positions or symbol in pending_entry or symbol in pending_exit:
                continue
            if not bool(row["ready"]) or not bool(row["entry"]) or row["regime"] not in {"BULL", "NEUTRAL"}:
                continue
            if branch == "TFM":
                key = (symbol, str(date.date()))
                if key not in fund_cache:
                    try:
                        fund_cache[key] = fundamental_engine.fundamental(symbol, date, "HSX")
                    except StrategyDataError:
                        fund_cache[key] = None
                if fund_cache[key] is None:
                    continue
                score = calculate_total_score(float(row["trend_score"]), fund_cache[key],
                                              float(row["momentum_score"]))
                threshold = cfg.minimum_composite_score
            else:
                key = (symbol, str(date.date()))
                if key not in fund_cache:
                    try:
                        fund_cache[key] = fundamental_engine.fundamental(symbol, date, "HSX")
                    except StrategyDataError:
                        fund_cache[key] = None
                if fund_cache[key] is not None:
                    continue  # Technical-only is for stocks without a usable F score.
                score = float(row["trend_score"])
                threshold = cfg.minimum_technical_only_score
            if score < threshold:
                continue
            candidates.append((score, symbol, row))
        # V1 priority: score, then 20-day average traded value, then symbol.
        for score, symbol, row in sorted(candidates, key=lambda item: (-item[0], -float(item[2]["adv20"]), item[1])):
            pending_entry[symbol] = {"date": date, "atr": float(row["atr14"]),
                                     "factor": float(row.get("adjustment_factor", 1.0)),
                                     "adv20": float(row["adv20"]), "regime": row["regime"], "score": score}
    equity_df = pd.DataFrame(equity_rows)
    trades_df = pd.DataFrame(trades)
    start, final = cfg.initial_capital, float(equity_df.iloc[-1]["equity"])
    curve = equity_df["equity"] / equity_df["equity"].cummax() - 1
    metrics = {"branch": branch, "initial_capital": start, "final_equity": final,
               "total_return_pct": 100 * (final / start - 1), "max_drawdown_pct": 100 * float(curve.min()),
               "closed_trades": len(trades_df), "open_positions": len(positions),
               "win_rate_pct": 100 * float(trades_df["net_pnl"].gt(0).mean()) if len(trades_df) else None,
               "fee_tax_slippage_assumed_zero": all(getattr(cfg, k) == 0 for k in
                   ("buy_fee_fraction", "sell_fee_fraction", "sell_tax_fraction", "slippage_fraction"))}
    return trades_df, equity_df, metrics


def run(config_path: Path, output_dir: Path) -> list[dict]:
    cfg = BacktestConfig.from_json(config_path)
    engine = SampleStrategyEngine()
    sample = pd.read_excel(SAMPLE_BOOK, sheet_name="Dữ liệu mô hình", usecols=PRICE_COLS)
    universe = engine.universe()
    sample = sample[sample["symbol"].map(universe).isin(["HSX", "HOSE"])]
    prices = adjusted_prices(sample)
    if prices.empty:
        raise StrategyDataError("Không có giá adjusted OHLC hợp lệ trong workbook mẫu")
    breadth = load_market_breadth(CRAWLER_DATA, prices["date"].max())
    index = load_index(CRAWLER_DATA, "HOSE", breadth)
    features = build_features(prices, index)
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for branch in ("TFM", "TECHNICAL_ONLY"):
        trades, equity, metrics = simulate(features, cfg, branch, engine)
        trades.to_csv(output_dir / f"trades_{branch.lower()}.csv", index=False, encoding="utf-8-sig")
        equity.to_csv(output_dir / f"equity_{branch.lower()}.csv", index=False, encoding="utf-8-sig")
        results.append(metrics)
    manifest = {"config": asdict(cfg), "data_source": str(SAMPLE_BOOK), "breadth_source": str(CRAWLER_DATA),
                "price_symbols": prices["symbol"].nunique(), "breadth_symbol_count_latest": int(breadth.iloc[-1]["breadth_symbols"]),
                "date_start": str(prices["date"].min().date()), "date_end": str(prices["date"].max().date()),
                "limitations": ["Selected 15-symbol sample has survivorship/selection bias.",
                                "Broad market breadth uses unadjusted Vietcap close because adjusted history is absent.",
                                "Signals use adjusted OHLC; fills and PnL use raw prices, without a corporate-action cash ledger.",
                                "Historical fundamental revisions and ratio point-in-time provenance are not verified.",
                                "Brokerage, tax and slippage are research assumptions and require sensitivity tests."],
                "results": results}
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Sample HOSE TFM/technical-only backtest")
    parser.add_argument("--config", type=Path, default=BOT_DIR / "backtest_config.json")
    parser.add_argument("--output", type=Path, default=BOT_DIR / "backtest_results")
    args = parser.parse_args()
    print(json.dumps(run(args.config, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
