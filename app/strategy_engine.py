"""Sample-data strategy bridge for the existing Fin_Bot scoring primitives.

The supplied workbook is a model sample, not a live market feed. Its target and
split columns are deliberately never loaded. BUY is gated until representative
market breadth is available; no backtest or trading claim is made here.
"""

from __future__ import annotations

import importlib.util
import math
from functools import lru_cache
from pathlib import Path

import pandas as pd

from app.market_store import MarketStore, NON_NEGATIVE_FUNDAMENTAL


BOT_DIR = Path(__file__).resolve().parents[1]
CRAWLER_DATA = BOT_DIR / "crawl data" / "Project_python3" / "data"
SAMPLE_BOOK = BOT_DIR / "yahoo_vn_model_sample_2020_2026.xlsx"
TECHNICAL_SOURCE = BOT_DIR / "Code chiến lược" / "Fin_Bot" / "technical_engine.py"
FUNDAMENTAL_SOURCE = BOT_DIR / "Code chiến lược" / "Fin_Bot" / "fundamental_engine.py"
PRICE_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume"]


class StrategyDataError(ValueError):
    """A required sample/market input is missing or unusable."""


@lru_cache(maxsize=2)
def _load_module(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise StrategyDataError(f"Không đọc được module chiến lược: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=2)
def _read_sample(path: str, mtime_ns: int) -> pd.DataFrame:
    del mtime_ns
    frame = pd.read_excel(path, sheet_name="Dữ liệu mô hình", usecols=PRICE_COLUMNS)
    frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for column in PRICE_COLUMNS[2:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=PRICE_COLUMNS).sort_values(["symbol", "date"])
    frame = frame.drop_duplicates(["symbol", "date"], keep="last")
    valid = (
        (frame["volume"] > 0)
        & (frame["low"] > 0)
        & (frame["low"] <= frame[["open", "close"]].min(axis=1))
        & (frame["high"] >= frame[["open", "close"]].max(axis=1))
        & (frame["adj_close"] > 0)
    )
    return frame.loc[valid].reset_index(drop=True)


@lru_cache(maxsize=4)
def _read_sector_map(path: str, mtime_ns: int) -> dict[str, str]:
    del mtime_ns
    source = Path(path)
    if source.suffix.lower() == ".xlsx":
        frame = pd.read_excel(source, sheet_name="Dữ liệu mô hình", usecols=["symbol", "sector_group"])
    else:
        frame = pd.read_csv(source, usecols=["symbol", "sector_group"])
    frame = frame.dropna(subset=["symbol", "sector_group"])
    return dict(zip(frame["symbol"].astype(str).str.upper().str.strip(),
                    frame["sector_group"].astype(str).str.strip()))


@lru_cache(maxsize=256)
def _read_fundamental_csv(path: str, mtime_ns: int) -> pd.DataFrame:
    del mtime_ns
    return pd.read_csv(path)


def calculate_total_score(trend: float, fundamental: float, momentum: float) -> float:
    """E is an entry gate, not a fourth weighted component."""
    values = (trend, fundamental, momentum)
    if not all(math.isfinite(value) and 0 <= value <= 100 for value in values):
        raise StrategyDataError("Điểm T, F, M phải nằm trong khoảng 0–100")
    return round(0.375 * trend + 0.3125 * fundamental + 0.3125 * momentum, 2)


def market_regime(index: pd.DataFrame, breadth: tuple[int, int] | None,
                  signal_date: pd.Timestamp) -> str:
    """V1 regime; missing same-day benchmark or breadth is never NEUTRAL."""
    if breadth is None or breadth[1] <= 0 or index.empty:
        return "UNKNOWN"
    frame = index.sort_values("date").drop_duplicates("date", keep="last").copy()
    frame["ema50"] = frame["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    frame["ema200"] = frame["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    row = frame.iloc[-1]
    if pd.Timestamp(row["date"]).normalize() != pd.Timestamp(signal_date).normalize():
        return "UNKNOWN"
    if any(pd.isna(row[key]) for key in ("close", "ema50", "ema200")):
        return "UNKNOWN"
    if row["ema50"] < row["ema200"]:
        return "BEAR"
    if row["close"] > row["ema50"] > row["ema200"] and breadth[0] / breadth[1] >= 0.5:
        return "BULL"
    return "NEUTRAL"


class SampleStrategyEngine:
    """Read the sample schema and expose T/F/M plus an independent E gate."""

    def __init__(self, sample_path: Path = SAMPLE_BOOK, data_dir: Path = CRAWLER_DATA,
                 valuation_verified: bool = False):
        self.sample_path = Path(sample_path)
        self.data_dir = Path(data_dir)
        self.valuation_verified = valuation_verified

    def sample(self) -> pd.DataFrame:
        if not self.sample_path.is_file():
            raise StrategyDataError("Thiếu workbook dữ liệu mẫu")
        return _read_sample(str(self.sample_path), self.sample_path.stat().st_mtime_ns)

    def universe(self) -> dict[str, str]:
        path = self.data_dir / "symbol_info_cache.csv"
        if not path.is_file():
            raise StrategyDataError("Thiếu danh sách mã Vietcap")
        frame = pd.read_csv(path, usecols=["symbol", "exchange", "security_type"])
        frame = frame[frame["security_type"].eq("STOCK")]
        return dict(zip(frame["symbol"].str.upper(), frame["exchange"].str.upper()))

    def prices(self, ticker: str, as_of: pd.Timestamp) -> tuple[pd.DataFrame, bool]:
        sample = self.sample()
        subset = sample.loc[sample["symbol"].eq(ticker) & sample["date"].le(as_of)].copy()
        is_sample = not subset.empty
        if not is_sample:
            path = self.data_dir / "market_data" / f"{ticker}.csv"
            if not path.is_file():
                raise StrategyDataError("Chưa có lịch sử giá của mã này")
            subset = pd.read_csv(path, usecols=["symbol", "trade_date", "open", "high", "low", "close", "volume", "is_final", "data_quality_flags"])
            subset = subset.rename(columns={"trade_date": "date"})
            subset["date"] = pd.to_datetime(subset["date"], errors="coerce")
            subset = subset[subset["date"].le(as_of)]
            subset = subset[subset["data_quality_flags"].isna()]
            subset = subset[(subset["date"].lt(as_of)) | subset["is_final"].astype(str).str.lower().eq("true")]
            subset["adj_close"] = subset["close"]  # Vietcap does not provide adjusted history.
        subset = subset.sort_values("date").drop_duplicates("date", keep="last")
        if len(subset) < 220:
            raise StrategyDataError("Cần ít nhất 220 phiên giá hợp lệ để tính EMA200 và momentum")
        subset = subset.tail(300).copy()
        for column in ("open", "high", "low", "close", "adj_close", "volume"):
            subset[column] = pd.to_numeric(subset[column], errors="coerce")
        subset = subset.dropna(subset=["date", "open", "high", "low", "close", "adj_close", "volume"])
        subset = subset[(subset["volume"] > 0) & (subset["close"] > 0) & (subset["adj_close"] > 0)]
        subset["display_close"] = subset["close"]
        # The adjusted close ratio is applied to the same day's OHLC. This is a
        # derived feature series; the original price remains the display price.
        factor = subset["adj_close"] / subset["close"]
        for column in ("open", "high", "low"):
            subset[column] = subset[column] * factor
        subset["close"] = subset["adj_close"]
        return subset.reset_index(drop=True), is_sample

    def index(self, as_of: pd.Timestamp) -> pd.DataFrame:
        path = self.data_dir / "index_data" / "VNINDEX.csv"
        if not path.is_file():
            raise StrategyDataError("Thiếu dữ liệu VNINDEX")
        frame = pd.read_csv(path, usecols=["trade_date", "close", "data_quality_flags"])
        frame = frame.rename(columns={"trade_date": "date"})
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        frame = frame[frame["date"].le(as_of) & frame["data_quality_flags"].isna()]
        return frame.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date").tail(300)

    def technical(self, prices: pd.DataFrame) -> dict:
        source = _load_module(str(TECHNICAL_SOURCE), "finbot_technical_engine")
        frame = prices.copy()
        frame["ema20"] = source.calculate_ema_by_symbol(frame, "close", 20)
        frame["ema50"] = source.calculate_ema_by_symbol(frame, "close", 50)
        frame["ema200"] = source.calculate_ema_by_symbol(frame, "close", 200)
        frame["rsi14"] = source.calculate_rsi_by_symbol(frame, "close", 14)
        frame["atr14"] = source.calculate_atr_by_symbol(frame, 14)
        frame["volume_ratio"] = source.calculate_volume_ratio_by_symbol(frame, 20)
        frame["breakout_level"] = source.calculate_breakout_level_by_symbol(frame, 20)
        row = frame.iloc[-1]
        required = ["ema20", "ema50", "ema200", "rsi14", "atr14", "volume_ratio", "breakout_level"]
        if any(not math.isfinite(float(row[key])) for key in required) or row["atr14"] <= 0:
            raise StrategyDataError("Không đủ dữ liệu để tính chỉ báo kỹ thuật")
        entry = source.evaluate_entry({
            "breakout": row["close"] > row["breakout_level"] and row["volume_ratio"] >= 1.5,
            "trend": row["close"] > row["ema20"] > row["ema50"],
            "rsi_ok": row["rsi14"] > 50,
            "distance_ok": 0 <= (row["close"] - row["ema20"]) / row["atr14"] <= 3,
        })
        trend = (
            30 * (row["close"] > row["ema20"])
            + 30 * (row["ema20"] > row["ema50"])
            + 25 * (row["ema50"] > row["ema200"])
            + 15 * (50 <= row["rsi14"] <= 70)
        )
        trend_exit = (len(frame) >= 2 and row["close"] < row["ema20"]
                      and frame.iloc[-2]["close"] < frame.iloc[-2]["ema20"]
                      and row["rsi14"] < 50)
        return {"trend": float(trend), "entry": entry, "volume_ratio": float(row["volume_ratio"]),
                "rsi": float(row["rsi14"]), "trend_exit": bool(trend_exit)}

    def momentum(self, prices: pd.DataFrame, index: pd.DataFrame) -> float:
        merged = prices[["date", "close"]].merge(index[["date", "close"]], on="date", suffixes=("_stock", "_index"))
        if len(merged) < 21 or merged.iloc[-1]["date"] != prices.iloc[-1]["date"]:
            raise StrategyDataError("VNINDEX chưa đủ phiên trùng với mã để tính sức mạnh tương đối")
        current = merged.iloc[-1]
        rel5 = current["close_stock"] / merged.iloc[-6]["close_stock"] - current["close_index"] / merged.iloc[-6]["close_index"]
        rel20 = current["close_stock"] / merged.iloc[-21]["close_stock"] - current["close_index"] / merged.iloc[-21]["close_index"]
        return float(50 * (rel5 > 0) + 50 * (rel20 > 0))

    def fundamental(self, ticker: str, as_of: pd.Timestamp, exchange: str) -> float:
        if exchange not in {"HSX", "HNX", "HOSE"}:
            raise StrategyDataError("UPCOM không có điểm fundamental trong phạm vi hiện tại")
        if ticker not in self._sector_map():
            raise StrategyDataError("Thiếu phân ngành đáng tin cậy; dùng nhánh Technical-only")
        frame = self._fundamental_frame(ticker)
        if frame.empty:
            raise StrategyDataError("Thiếu báo cáo tài chính cho mã này")
        frame = frame[frame["report_type"].astype(str).str.lower().eq("quarterly")].copy()
        frame["announcement_date"] = pd.to_datetime(frame["announcement_date"], errors="coerce", utc=True).dt.tz_convert(None).dt.normalize()
        frame = frame[frame["announcement_date"].le(as_of)].copy()
        frame = frame[frame["report_period"].astype(str).str.fullmatch(r"\d{4}Q[1-4]")]
        frame = frame.sort_values("report_period")
        if frame.empty:
            raise StrategyDataError("Chưa có báo cáo quý được công bố tại ngày phân tích")
        row = frame.iloc[-1]
        period = row["report_period"]
        previous_period = f"{int(period[:4]) - 1}{period[4:]}"
        previous = frame.loc[frame["report_period"].eq(previous_period)]
        if previous.empty:
            raise StrategyDataError("Thiếu báo cáo cùng quý năm trước để tính tăng trưởng")
        previous = previous.iloc[-1]
        is_bank = str(row.get("revenue_source_field", "")) == "isb38"
        required = ["revenue", "net_profit", "total_equity", "operating_cash_flow", "eps", "roe"]
        for key in required + ([] if is_bank else ["total_debt"]):
            if pd.isna(row[key]):
                raise StrategyDataError(f"Báo cáo {period} thiếu {key}; không quy đổi thành 0 điểm")
        for key in ("revenue", "net_profit"):
            if pd.isna(previous[key]) or previous[key] <= 0:
                raise StrategyDataError(f"Thiếu {key} cùng quý năm trước")
        debt_ratio = math.nan
        if not is_bank:
            source = _load_module(str(FUNDAMENTAL_SOURCE), "finbot_fundamental_engine")
            metric = pd.DataFrame([{"symbol": ticker, "equity": row["total_equity"], "debt": row["total_debt"]}])
            debt_ratio = float(source.calculate_debt_to_equity(metric).iloc[0]["debt_to_equity"])
        revenue_growth = row["revenue"] / previous["revenue"] - 1
        profit_growth = row["net_profit"] / previous["net_profit"] - 1
        raw = (
            20 * (revenue_growth >= 0.15)
            + 20 * (profit_growth >= 0.15)
            + 20 * (float(row["roe"]) >= 0.15)
            + 10 * (float(row["operating_cash_flow"]) > 0)
            + (0 if is_bank else 10 * (debt_ratio <= 1))
        )
        maximum = 70 if is_bank else 80
        pe_median, pb_median = self._sector_valuation_medians(ticker, period, as_of)
        for key, median in (("pe", pe_median), ("pb", pb_median)):
            value = pd.to_numeric(row.get(key), errors="coerce")
            if pd.notna(value) and value > 0 and pd.notna(median) and median > 0:
                maximum += 5
                raw += 5 if value <= median else 2
        return round(100 * raw / maximum, 2)

    def _sector_map(self) -> dict[str, str]:
        """Use sample workbook sectors or an explicit whole-market mapping."""
        path = BOT_DIR / "industry_map.csv"
        if path.is_file():
            return _read_sector_map(str(path), path.stat().st_mtime_ns)
        if self.sample_path.is_file():
            return _read_sector_map(str(self.sample_path), self.sample_path.stat().st_mtime_ns)
        return {}

    def _sector_valuation_medians(self, ticker: str, period: str,
                                  as_of: pd.Timestamp) -> tuple[float, float]:
        if not self.valuation_verified:
            return math.nan, math.nan
        sectors = self._sector_map()
        group = sectors.get(ticker)
        if not group:
            return math.nan, math.nan
        pe_values, pb_values = [], []
        for peer, peer_group in sectors.items():
            if peer_group != group or peer == ticker:
                continue
            frame = self._fundamental_frame(peer)
            if frame.empty or not {"report_type", "report_period", "announcement_date"}.issubset(frame):
                continue
            announced = pd.to_datetime(frame["announcement_date"], errors="coerce").dt.normalize()
            rows = frame.loc[frame["report_type"].astype(str).str.lower().eq("quarterly")
                             & frame["report_period"].eq(period) & announced.le(as_of)]
            if rows.empty:
                continue
            row = rows.iloc[-1]
            for key, values in (("pe", pe_values), ("pb", pb_values)):
                value = pd.to_numeric(row.get(key), errors="coerce")
                if pd.notna(value) and value > 0:
                    values.append(float(value))
        return (float(pd.Series(pe_values).median()) if pe_values else math.nan,
                float(pd.Series(pb_values).median()) if pb_values else math.nan)

    def _fundamental_frame(self, ticker: str) -> pd.DataFrame:
        path = self.data_dir / "fundamental" / f"{ticker}_fundamental.csv"
        if not path.is_file():
            return pd.DataFrame()
        return _read_fundamental_csv(str(path), path.stat().st_mtime_ns).copy()


class VietcapStrategyEngine(SampleStrategyEngine):
    """Same scoring primitives, with SQLite as the point-in-time input store."""

    INDEX_BY_EXCHANGE = {"HSX": "VNINDEX", "HOSE": "VNINDEX", "HNX": "HNXINDEX", "UPCOM": "UPCOMINDEX"}

    def __init__(self, store: MarketStore, valuation_verified: bool = False):
        self.store = store
        self.valuation_verified = valuation_verified

    def universe(self) -> dict[str, str]:
        return self.store.universe()

    def _sector_map(self) -> dict[str, str]:
        # The selected Yahoo sample is not a classification for all Vietcap stocks.
        path = BOT_DIR / "industry_map.csv"
        return _read_sector_map(str(path), path.stat().st_mtime_ns) if path.is_file() else {}

    def prices(self, ticker: str, as_of: pd.Timestamp) -> tuple[pd.DataFrame, bool]:
        subset = self.store.prices(ticker)
        if subset.empty:
            raise StrategyDataError("Chưa có lịch sử giá của mã này")
        subset = subset.rename(columns={"trade_date": "date"})
        subset["date"] = pd.to_datetime(subset["date"], errors="coerce")
        subset = subset[subset["date"].le(as_of)].copy()
        if len(subset) < 220:
            raise StrategyDataError("Cần ít nhất 220 phiên giá hợp lệ để tính EMA200 và momentum")
        adjusted = subset[["adjusted_open", "adjusted_high", "adjusted_low", "adjusted_close"]].notna().all(axis=1).all()
        subset["display_close"] = subset["close"]
        if adjusted:
            for key in ("open", "high", "low", "close"):
                subset[key] = subset[f"adjusted_{key}"]
        subset["symbol"] = ticker
        subset["has_adjusted_history"] = bool(adjusted)
        return subset.reset_index(drop=True), False

    def index(self, as_of: pd.Timestamp, exchange: str = "HSX") -> pd.DataFrame:
        name = self.INDEX_BY_EXCHANGE.get(exchange)
        if not name:
            raise StrategyDataError(f"Không có benchmark cho sàn {exchange}")
        frame = self.store.index(name).rename(columns={"trade_date": "date"})
        if frame.empty:
            raise StrategyDataError(f"Thiếu chỉ số {name}")
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        return frame[frame["date"].le(as_of)].sort_values("date")

    def _fundamental_frame(self, ticker: str) -> pd.DataFrame:
        frame = self.store.fundamentals(ticker)
        for key in ("revenue", "net_profit", "total_equity", "operating_cash_flow", "eps", "pe", "pb", "roe", "total_debt"):
            if key in frame:
                frame[key] = pd.to_numeric(frame[key], errors="coerce")
        invalid = pd.Series(False, index=frame.index)
        for key in NON_NEGATIVE_FUNDAMENTAL:
            if key in frame:
                invalid |= pd.to_numeric(frame[key], errors="coerce").lt(0)
        frame = frame.loc[~invalid].copy()
        return frame
