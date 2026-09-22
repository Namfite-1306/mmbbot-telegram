"""Validated raw CSV -> SQLite market store used by the bot.

The crawler merges new observations into its existing raw CSVs; import never
edits those files. MODEL READY files are intentionally not used: they contain
future labels and omit the last sessions of each series.
"""

from __future__ import annotations

import csv
import importlib
import json
import logging
import math
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

import pandas as pd

from app.cafef_market_data import CafeFDataError, CafeFMarketClient, CafeFNoDataError
from app.dnse_market_data import DNSEConfigurationError, DNSEDataError, DNSEMarketClient, DNSENoDataError


BOT_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = BOT_DIR / "crawl data" / "Project_python3" / "data"
CLEAN_DIR = BOT_DIR / "cleaned data" / "cleaned_v2"
CRAWLER_DIR = BOT_DIR / "crawl data" / "Project_python3"
LOG = logging.getLogger(__name__)


class MarketNoDataError(RuntimeError):
    """A provider responded normally, but has no bars in the requested window."""


class MarketPriceValidationError(DNSEDataError):
    """Price units could not be verified; this is not a provider outage."""


def previous_weekday(day: date) -> date:
    """Conservative EOD cutoff; exchange holidays require a separate calendar."""
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


NON_NEGATIVE_FUNDAMENTAL = (
    "current_assets", "non_current_assets", "total_assets", "total_liabilities",
    "total_capital_source", "revenue", "total_debt", "market_cap", "shares_outstanding",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_symbols (
  symbol TEXT PRIMARY KEY, exchange TEXT NOT NULL, security_type TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS market_prices (
  symbol TEXT NOT NULL, trade_date TEXT NOT NULL, exchange TEXT,
  open REAL, high REAL, low REAL, close REAL, volume REAL,
  adjusted_open REAL, adjusted_high REAL, adjusted_low REAL, adjusted_close REAL,
  is_final INTEGER NOT NULL, quality_flags TEXT, source TEXT,
  PRIMARY KEY(symbol, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_market_prices_date ON market_prices(trade_date);
CREATE TABLE IF NOT EXISTS market_fundamentals (
  symbol TEXT NOT NULL, report_type TEXT NOT NULL, report_period TEXT NOT NULL,
  announcement_date TEXT, payload TEXT NOT NULL,
  PRIMARY KEY(symbol, report_type, report_period)
);
CREATE INDEX IF NOT EXISTS idx_market_fundamental_date
  ON market_fundamentals(symbol, announcement_date);
CREATE TABLE IF NOT EXISTS market_indices (
  symbol TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL NOT NULL,
  is_final INTEGER NOT NULL, quality_flags TEXT,
  PRIMARY KEY(symbol, trade_date)
);
CREATE TABLE IF NOT EXISTS market_imports (
  path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
  rows INTEGER NOT NULL, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS market_refresh_state (
  key TEXT PRIMARY KEY, value TEXT NOT NULL
);
"""


def _number(value: str | None) -> float | None:
    try:
        number = float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
    return number if number is not None and math.isfinite(number) else None


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _price_row(row: dict[str, str], fallback_symbol: str) -> tuple | None:
    symbol = (row.get("ticker") or row.get("symbol") or fallback_symbol).strip().upper()
    trade_day = _date(row.get("trade_date") or row.get("date"))
    values = [_number(row.get(key)) for key in ("open", "high", "low", "close", "volume")]
    if not symbol or not trade_day or any(value is None for value in values):
        return None
    op, high, low, close, volume = values
    if low <= 0 or volume < 0 or low > min(op, close) or high < max(op, close):
        return None
    adjusted = [_number(row.get(f"adjusted_{key}")) for key in ("open", "high", "low", "close")]
    if any(value is None for value in adjusted) or not (
        adjusted[2] > 0 and adjusted[2] <= min(adjusted[0], adjusted[3])
        and adjusted[1] >= max(adjusted[0], adjusted[3])
    ):
        adjusted = [None] * 4
    return (
        symbol, trade_day, row.get("exchange"), op, high, low, close, volume,
        *adjusted, int(str(row.get("is_final", "true")).lower() in {"true", "1"}),
        row.get("data_quality_flags") or None, row.get("source") or "vietcap",
    )


class MarketStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
        return conn

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def _import_file(self, conn: sqlite3.Connection, path: Path, kind: str) -> int:
        stat = path.stat()
        key = str(path.resolve()) + ("#fundamental-clean-v2" if kind == "fundamental" else "")
        old = conn.execute("SELECT size, mtime_ns FROM market_imports WHERE path=?", (key,)).fetchone()
        if old and (old["size"], old["mtime_ns"]) == (stat.st_size, stat.st_mtime_ns):
            return 0
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if kind == "symbols":
                rows = [((r.get("ticker") or r.get("symbol") or "").upper(),
                         (r.get("exchange") or "").upper(), (r.get("security_type") or "").upper())
                        for r in reader]
                rows = [r for r in rows if r[0] and r[1] and r[2] == "STOCK"]
                conn.executemany("INSERT INTO market_symbols VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET exchange=excluded.exchange,security_type=excluded.security_type", rows)
            elif kind == "prices":
                rows = [r for source in reader if (r := _price_row(source, path.stem.replace("cleaned_", "")))]
                conn.executemany("""INSERT INTO market_prices VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(symbol,trade_date) DO UPDATE SET exchange=excluded.exchange,
                    open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                    volume=excluded.volume,adjusted_open=excluded.adjusted_open,
                    adjusted_high=excluded.adjusted_high,adjusted_low=excluded.adjusted_low,
                    adjusted_close=excluded.adjusted_close,is_final=excluded.is_final,
                    quality_flags=excluded.quality_flags,source=excluded.source
                    WHERE excluded.is_final >= market_prices.is_final""", rows)
            elif kind == "index":
                rows = []
                for source in reader:
                    symbol = (source.get("ticker") or source.get("symbol") or path.stem.replace("cleaned_", "")).upper()
                    trade_day = _date(source.get("trade_date"))
                    close = _number(source.get("close"))
                    if trade_day and close is not None and close > 0:
                        rows.append((symbol, trade_day, close,
                                     int(str(source.get("is_final", "true")).lower() in {"true", "1"}),
                                     source.get("data_quality_flags") or None))
                conn.executemany("""INSERT INTO market_indices VALUES(?,?,?,?,?)
                    ON CONFLICT(symbol,trade_date) DO UPDATE SET close=excluded.close,
                    is_final=excluded.is_final,quality_flags=excluded.quality_flags
                    WHERE excluded.is_final >= market_indices.is_final""", rows)
            else:
                rows = []
                for source in reader:
                    source = dict(source)
                    symbol = (source.get("ticker") or source.get("symbol") or "").upper()
                    period = source.get("report_period") or ""
                    report_type = (source.get("report_type") or "").lower()
                    if symbol and period and report_type:
                        flags = [flag for flag in (source.get("data_quality_flags") or "").split("|")
                                 if flag and flag != "NEGATIVE_NUMERIC"]
                        if any((_number(source.get(key)) or 0) < 0 for key in NON_NEGATIVE_FUNDAMENTAL):
                            flags.append("INVALID_NEGATIVE_BALANCE")
                        source["data_quality_flags"] = "|".join(dict.fromkeys(flags))
                        rows.append((symbol, report_type, period,
                                     _date(source.get("announcement_date")), json.dumps(source, ensure_ascii=False)))
                conn.executemany("""INSERT INTO market_fundamentals VALUES(?,?,?,?,?)
                    ON CONFLICT(symbol,report_type,report_period) DO UPDATE SET
                    announcement_date=excluded.announcement_date,payload=excluded.payload""", rows)
        conn.execute("""INSERT INTO market_imports(path,size,mtime_ns,rows) VALUES(?,?,?,?)
            ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,
            rows=excluded.rows,imported_at=CURRENT_TIMESTAMP""", (key, stat.st_size, stat.st_mtime_ns, len(rows)))
        return len(rows)

    def import_directory(self, root: Path = CLEAN_DIR, symbols: list[str] | None = None) -> dict[str, int]:
        """Idempotently import cleaned_v2 or freshly crawled raw CSV files."""
        self.initialize()
        source = Path(root)
        prefixes = {"market_data": "prices", "fundamental": "fundamental", "index_data": "index"}
        result = {"symbols": 0, "prices": 0, "fundamental": 0, "index": 0}
        with self.connect() as conn:
            cache = source / "symbol_info_cache" / "cleaned_symbol_info_cache.csv"
            if not cache.is_file():
                cache = source / "symbol_info_cache.csv"
            if cache.is_file():
                result["symbols"] += self._import_file(conn, cache, "symbols")
            for folder, kind in prefixes.items():
                location = source / folder
                if not location.is_dir():
                    continue
                if symbols is None or kind == "index":
                    paths = sorted(location.glob("*.csv"))
                else:
                    paths = []
                    for symbol in symbols:
                        paths.extend((location / f"{symbol}.csv", location / f"cleaned_{symbol}.csv",
                                      location / f"{symbol}_fundamental.csv", location / f"cleaned_{symbol}_fundamental.csv"))
                    paths = [p for p in paths if p.is_file()]
                for path in paths:
                    result[kind] += self._import_file(conn, path, kind)
        return result

    def universe(self) -> dict[str, str]:
        with self.connect() as conn:
            return {r[0]: r[1] for r in conn.execute("SELECT symbol,exchange FROM market_symbols WHERE security_type='STOCK'")}

    def prices(self, symbol: str, limit: int = 350) -> pd.DataFrame:
        with self.connect() as conn:
            frame = pd.read_sql_query("""SELECT * FROM (SELECT * FROM market_prices WHERE symbol=?
                AND is_final=1 AND quality_flags IS NULL AND volume>0 ORDER BY trade_date DESC LIMIT ?)
                ORDER BY trade_date""", conn, params=(symbol, limit))
        return frame

    def index(self, symbol: str, limit: int = 350) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query("""SELECT * FROM (SELECT * FROM market_indices WHERE symbol=?
                AND is_final=1 AND quality_flags IS NULL ORDER BY trade_date DESC LIMIT ?)
                ORDER BY trade_date""", conn, params=(symbol, limit))

    def fundamentals(self, symbol: str) -> pd.DataFrame:
        with self.connect() as conn:
            payloads = [json.loads(r[0]) for r in conn.execute(
                "SELECT payload FROM market_fundamentals WHERE symbol=? ORDER BY report_period", (symbol,))]
        return pd.DataFrame(payloads)

    def stock_overview(self, symbol: str, signal_date: str | None = None) -> dict:
        """Latest observed quote and only reports public by the signal date."""
        with self.connect() as conn:
            quote = conn.execute("""SELECT * FROM market_prices WHERE symbol=?
                AND quality_flags IS NULL AND close>0 ORDER BY trade_date DESC LIMIT 1""",
                (symbol,)).fetchone()
            if quote is None:
                return {}
            previous = conn.execute("""SELECT close FROM market_prices WHERE symbol=?
                AND trade_date<? AND is_final=1 AND quality_flags IS NULL AND close>0
                ORDER BY trade_date DESC LIMIT 1""", (symbol, quote["trade_date"])).fetchone()
            financial = conn.execute("""SELECT payload FROM market_fundamentals
                WHERE symbol=? AND report_type='quarterly' AND announcement_date<=?
                ORDER BY announcement_date DESC, report_period DESC LIMIT 1""",
                (symbol, signal_date or quote["trade_date"])).fetchone()
        return {
            "quote": dict(quote),
            "previous_close": previous[0] if previous else None,
            "financial": json.loads(financial[0]) if financial else None,
        }

    def close_before(self, symbol: str, trade_day: str) -> float | None:
        """Final reference close strictly before the live trade's session."""
        with self.connect() as conn:
            row = conn.execute("""SELECT close FROM market_prices WHERE symbol=? AND trade_date<?
                AND is_final=1 AND quality_flags IS NULL AND close>0
                ORDER BY trade_date DESC LIMIT 1""", (symbol, trade_day)).fetchone()
        return float(row[0]) if row else None

    def latest_date(self) -> str:
        with self.connect() as conn:
            return conn.execute("SELECT MAX(trade_date) FROM market_prices WHERE is_final=1 AND quality_flags IS NULL").fetchone()[0] or "chưa có"

    def last_scan_session(self, before: date) -> str | None:
        """Require the prior weekday's finalized VNINDEX; never silently use older data."""
        expected = previous_weekday(before).isoformat()
        with self.connect() as conn:
            row = conn.execute("""SELECT trade_date FROM market_indices
                WHERE symbol='VNINDEX' AND trade_date=? AND is_final=1
                AND quality_flags IS NULL""", (expected,)).fetchone()
            return row[0] if row else None

    def last_eod_refresh_date(self) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM market_refresh_state WHERE key='last_full_eod'").fetchone()
            return row[0] if row else None

    def latest_price_dates_through(self, trade_day: str) -> dict[str, str]:
        """One database query to classify missing/stale symbols before a scan."""
        with self.connect() as conn:
            return {symbol: last_day for symbol, last_day in conn.execute(
                """SELECT symbol, MAX(trade_date) FROM market_prices
                WHERE trade_date<=? AND is_final=1 AND quality_flags IS NULL
                AND volume>0 GROUP BY symbol""", (trade_day,))}

    def latest_coverage(self) -> tuple[str, int, int]:
        today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
        trade_day = self.last_scan_session(today) or previous_weekday(today).isoformat()
        with self.connect() as conn:
            covered = conn.execute("""SELECT COUNT(*) FROM market_prices
                WHERE trade_date=? AND is_final=1 AND quality_flags IS NULL AND volume>0""", (trade_day,)).fetchone()[0]
            universe = conn.execute("SELECT COUNT(*) FROM market_symbols WHERE security_type='STOCK'").fetchone()[0]
        return trade_day, covered, universe

    def breadth(self, trade_day: str | None = None) -> tuple[int, int]:
        """HOSE common-stock advancers with valid trades on both index sessions."""
        trade_day = trade_day or self.latest_date()
        advancing = total = 0
        with self.connect() as conn:
            previous = conn.execute("""SELECT MAX(trade_date) FROM market_indices WHERE symbol='VNINDEX'
                AND trade_date<? AND is_final=1 AND quality_flags IS NULL""", (trade_day,)).fetchone()[0]
            current = conn.execute("""SELECT 1 FROM market_indices WHERE symbol='VNINDEX'
                AND trade_date=? AND is_final=1 AND quality_flags IS NULL""", (trade_day,)).fetchone()
            if not previous or not current:
                return 0, 0
            symbols = [r[0] for r in conn.execute("""SELECT symbol FROM market_symbols
                WHERE security_type='STOCK' AND exchange IN ('HSX','HOSE')""")]
            for symbol in symbols:
                recent = conn.execute("""SELECT trade_date,close FROM market_prices
                    WHERE symbol=? AND trade_date<=? AND is_final=1 AND quality_flags IS NULL
                    AND volume>0 AND close>0 ORDER BY trade_date DESC LIMIT 2""", (symbol, trade_day)).fetchall()
                if len(recent) == 2 and recent[0]["trade_date"] == trade_day and recent[1]["trade_date"] == previous:
                    total += 1
                    advancing += recent[0]["close"] > recent[1]["close"]
        return advancing, total

    def refresh(self, symbol: str | None = None, *, include_fundamentals: bool = True) -> dict:
        """DNSE OHLC first, Vietcap backup, then CafeF price-history backup."""
        self.initialize()
        if str(CRAWLER_DIR) not in sys.path:
            sys.path.insert(0, str(CRAWLER_DIR))
        now = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
        target_day = previous_weekday(now.date()) if symbol is None else now.date()
        if symbol is None:
            with self.connect() as conn:
                row = conn.execute("SELECT value FROM market_refresh_state WHERE key='last_full_eod'").fetchone()
            if row and row[0] == target_day.isoformat() and self.last_scan_session(now.date()):
                return {"skipped": f"Dữ liệu cuối ngày {target_day} đã được cập nhật"}
        try:
            dnse = DNSEMarketClient(BOT_DIR / ".env")
        except DNSEConfigurationError as exc:
            # Misconfiguration is not a provider outage: do not quietly switch to VCI.
            raise RuntimeError(str(exc)) from exc
        start, end = target_day - timedelta(days=7), target_day
        targets = [symbol] if symbol else sorted(self.universe())
        if not targets:
            raise RuntimeError("Chưa có danh sách mã cổ phiếu trong database")
        already_final = 0
        if symbol is None:
            with self.connect() as conn:
                completed = {row[0] for row in conn.execute("""SELECT symbol FROM market_prices
                    WHERE trade_date=? AND is_final=1 AND quality_flags IS NULL""",
                    (target_day.isoformat(),))}
            already_final = len(completed)
            targets = [ticker for ticker in targets if ticker not in completed]
        # Preflight before a market-wide crawl. If DNSE is down, the existing
        # Vietcap pipeline has its own circuit breaker and preserves cached data.
        if symbol is None:
            try:
                if not dnse.get_ohlcv_bars("VNINDEX", start, end, index=True):
                    raise DNSEDataError("DNSE chưa có nến VNINDEX trong 7 ngày qua")
            except DNSEConfigurationError as exc:
                raise RuntimeError(str(exc)) from exc
            except DNSEDataError as exc:
                LOG.warning("DNSE preflight failed; using Vietcap backup: %s", exc)
                return self._refresh_vietcap_or_cafef_market(start, end)
        success = fallback = cafef_fallback = empty = failed = consecutive_dnse_failures = 0
        errors = []
        for ticker in targets:
            try:
                bars = dnse.get_ohlcv_bars(ticker, start, end)
                if not bars:
                    raise DNSENoDataError(f"DNSE không có nến {ticker}")
                self._save_dnse_bars(ticker, bars, index=False)
                success += 1
                consecutive_dnse_failures = 0
            except DNSEConfigurationError as exc:
                raise RuntimeError(str(exc)) from exc
            except DNSEDataError as exc:
                if isinstance(exc, (DNSENoDataError, MarketPriceValidationError)):
                    consecutive_dnse_failures = 0
                else:
                    consecutive_dnse_failures += 1
                LOG.warning("DNSE %s failed; trying Vietcap backup: %s", ticker, exc)
                try:
                    self._refresh_vietcap_symbol(ticker, fundamentals=False, end_date=end)
                    fallback += 1
                except RuntimeError as vietcap_exc:
                    LOG.warning("Vietcap %s failed; trying CafeF: %s", ticker, vietcap_exc)
                    try:
                        self._refresh_cafef_symbol(ticker, start, end)
                        cafef_fallback += 1
                    except CafeFNoDataError as cafef_exc:
                        if isinstance(vietcap_exc, MarketNoDataError) and isinstance(exc, DNSENoDataError):
                            empty += 1
                            LOG.info("Không nguồn nào có phiên mới cho %s: %s", ticker, cafef_exc)
                        else:
                            failed += 1
                            errors.append(ticker)
                            LOG.warning("CafeF %s has no data after source errors: %s", ticker, cafef_exc)
                    except CafeFDataError as cafef_exc:
                        LOG.warning("CafeF %s failed: %s", ticker, cafef_exc)
                        failed += 1
                        errors.append(ticker)
            if symbol is None and consecutive_dnse_failures >= 5:
                LOG.warning("DNSE failed for five consecutive symbols; switching to market backups")
                return self._refresh_vietcap_or_cafef_market(start, end)
            sleep(0.1)
        if symbol is None:
            index_failed = False
            for index_symbol in ("VNINDEX", "HNXINDEX", "UPCOMINDEX"):
                try:
                    bars = dnse.get_ohlcv_bars(index_symbol, start, end, index=True)
                    if bars:
                        self._save_dnse_bars(index_symbol, bars, index=True)
                    elif index_symbol == "VNINDEX":
                        index_failed = True
                except DNSEDataError as exc:
                    if index_symbol == "VNINDEX":
                        index_failed = True
                    LOG.warning("DNSE index %s unavailable: %s", index_symbol, exc)
            # DNSE Market Data does not supply the financial-statement fields
            # needed for F. Keep the existing Vietcap collector for those only.
            fundamental_result = None
            try:
                if include_fundamentals:
                    fundamentals = importlib.import_module("vietcap_fundamental_data")
                    fundamental_symbols = [s for s, exchange in self.universe().items()
                                           if exchange in {"HSX", "HOSE", "HNX"}]
                    fundamental_result = fundamentals.crawl_fundamental_data(
                        fundamental_symbols, RAW_DIR / "fundamental", workers=2)
            finally:
                imported = self.import_directory(RAW_DIR)
        else:
            try:
                fundamental_result = self._refresh_fundamental(symbol)
            finally:
                imported = self.import_directory(RAW_DIR, [symbol])
            index_failed = False
        if failed or index_failed or (symbol is not None and empty) or (fundamental_result and fundamental_result.get("failed")):
            raise RuntimeError(f"Cập nhật chưa đủ: {failed} mã giá lỗi, "
                               f"{empty} mã không có phiên mới, "
                               f"{(fundamental_result or {}).get('failed', 0)} mã fundamental lỗi, "
                               f"index lỗi={index_failed}")
        if symbol is None and self.last_scan_session(now.date()) != target_day.isoformat():
            raise RuntimeError(f"Chưa có VNINDEX cuối ngày hợp lệ cho {target_day}; không xác nhận cập nhật EOD")
        if symbol is None:
            with self.connect() as conn:
                conn.execute("""INSERT INTO market_refresh_state(key,value) VALUES('last_full_eod',?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (target_day.isoformat(),))
        return {"dnse": success, "vietcap_backup": fallback, "cafef_backup": cafef_fallback,
                "already_final": already_final, "empty": empty,
                "failed": errors, "fundamental": fundamental_result, "imported": imported}

    def _save_dnse_bars(self, symbol: str, bars: list[dict], *, index: bool,
                        source: str = "dnse") -> int:
        from vietcap_common import merge_csv_dedup
        from vietcap_market_data import build_market_data_record

        # Existing VCI history is in VND. Choose a scale only when an observed
        # overlapping close makes units unambiguous; never silently mix units.
        scale = 1.0
        if not index:
            with self.connect() as conn:
                matches = [conn.execute("SELECT close FROM market_prices WHERE symbol=? AND trade_date=?",
                                        (symbol, bar["trade_date"])).fetchone() for bar in bars]
            ratios = [row[0] / bar["close"] for bar, row in zip(bars, matches)
                      if row and row[0] and bar["close"]]
            if not ratios:
                raise MarketPriceValidationError(f"{symbol}: chưa có phiên trùng để xác nhận đơn vị giá {source}")
            errors = {candidate: min(abs(math.log(ratio / candidate)) for ratio in ratios)
                      for candidate in (1.0, 1000.0)}
            scale = min(errors, key=errors.get)
            if errors[scale] > math.log(1.5):
                raise MarketPriceValidationError(f"{symbol}: giá {source} không khớp lịch sử, không ghi đè")
        info = {"exchange": self.universe().get(symbol), "security_type": "STOCK"}
        protected_dates = set()
        if source == "cafef":
            # A last-resort source must not replace valid, final DNSE/VCI rows
            # (including their adjusted fields and priceboard enrichments).
            table = "market_indices" if index else "market_prices"
            with self.connect() as conn:
                protected_dates = {r[0] for r in conn.execute(
                    f"SELECT trade_date FROM {table} WHERE symbol=? AND is_final=1 AND quality_flags IS NULL",
                    (symbol,))}
        records = []
        for bar in bars:
            if bar["trade_date"] in protected_dates:
                continue
            scaled = {**bar, **{key: bar[key] * scale for key in ("open", "high", "low", "close")}}
            record = build_market_data_record(symbol, scaled, symbol_info=info)
            record["source"] = source
            records.append(record)
        if not records:
            return 0
        folder = RAW_DIR / ("index_data" if index else "market_data")
        folder.mkdir(parents=True, exist_ok=True)
        merge_csv_dedup(records, folder / f"{symbol}.csv", key_field="trade_date")
        return len(records)

    def _refresh_cafef_symbol(self, symbol: str, start: date, end: date,
                              *, index: bool = False, client: CafeFMarketClient | None = None) -> int:
        client = client or CafeFMarketClient()
        exchange = self.universe().get(symbol, "")
        bars = client.get_ohlcv_bars(symbol, exchange, start, end, index=index)
        if not bars:
            raise CafeFNoDataError(f"CafeF không có giá cho {symbol}")
        if not index:
            # A stale local series may have no dates in the new seven-day
            # window. Query one old, already-stored session solely to prove
            # CafeF's thousands-of-VND scale; never guess the conversion.
            with self.connect() as conn:
                recent_dates = {bar["trade_date"] for bar in bars}
                overlap = conn.execute(
                    "SELECT 1 FROM market_prices WHERE symbol=? AND trade_date IN ("
                    + ",".join("?" for _ in recent_dates) + ") LIMIT 1",
                    (symbol, *recent_dates)).fetchone()
                anchor = None if overlap else conn.execute(
                    "SELECT trade_date FROM market_prices WHERE symbol=? AND close>0 "
                    "AND is_final=1 AND quality_flags IS NULL ORDER BY trade_date DESC LIMIT 1",
                    (symbol,)).fetchone()
            if not overlap and anchor:
                anchor_day = date.fromisoformat(anchor[0])
                history = client.get_ohlcv_bars(
                    symbol, exchange, anchor_day - timedelta(days=6), anchor_day + timedelta(days=1))
                matching = [bar for bar in history if bar["trade_date"] == anchor[0]]
                if not matching:
                    raise CafeFDataError(f"{symbol}: CafeF không có phiên lịch sử để kiểm tra đơn vị")
                bars = [*bars, matching[0]]
        try:
            return self._save_dnse_bars(symbol, bars, index=index, source="cafef")
        except DNSEDataError as exc:
            raise CafeFDataError(str(exc)) from exc

    def _refresh_vietcap_or_cafef_market(self, start: date, end: date) -> dict:
        try:
            return self._refresh_vietcap_market()
        except RuntimeError as exc:
            LOG.warning("Vietcap market backup failed; trying CafeF: %s", exc)
            return self._refresh_cafef_market(start, end, vietcap_error=str(exc))

    def _refresh_cafef_market(self, start: date, end: date, *, vietcap_error: str) -> dict:
        """Last-resort price refresh; never claim fundamentals were refreshed."""
        client = CafeFMarketClient()
        targets = sorted(self.universe())
        if not targets:
            raise RuntimeError("Chưa có danh sách mã để cập nhật CafeF")
        success, errors, empty = 0, [], []
        for ticker in targets:
            try:
                self._refresh_cafef_symbol(ticker, start, end, client=client)
                success += 1
            except CafeFNoDataError:
                empty.append(ticker)
            except CafeFDataError as exc:
                LOG.warning("CafeF %s unavailable: %s", ticker, exc)
                errors.append(ticker)
                if len(errors) >= 5 and success == 0:
                    break  # Avoid flooding CafeF during a broad outage.
            sleep(0.8)
        index_errors = []
        if success:
            for index_symbol in ("VNINDEX", "HNXINDEX", "UPCOMINDEX"):
                try:
                    self._refresh_cafef_symbol(index_symbol, start, end, index=True, client=client)
                except CafeFDataError as exc:
                    index_errors.append(index_symbol)
                    LOG.warning("CafeF index %s unavailable: %s", index_symbol, exc)
                sleep(0.8)
        imported = self.import_directory(RAW_DIR)
        if not success:
            raise RuntimeError(f"DNSE/Vietcap/CafeF đều không thể cập nhật; Vietcap: {vietcap_error}")
        return {"source": "cafef_backup", "cafef_prices": success,
                "failed": errors, "empty": empty, "index_failed": index_errors,
                "fundamental_warning": "Fundamental chưa được làm mới vì Vietcap lỗi",
                "vietcap_error": vietcap_error, "imported": imported}

    def _refresh_vietcap_symbol(self, symbol: str, *, fundamentals: bool,
                                end_date: date | None = None) -> dict:
        market = importlib.import_module("vietcap_market_data")
        end_date = end_date or datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
        result = market.crawl_market_data(
            [symbol], end_date - timedelta(days=7), end_date, "1D", 0.8, 1,
            RAW_DIR / "market_data", False, "merge",
            symbol_cache_path=RAW_DIR / "symbol_info_cache.csv",
        )
        if result["failed"]:
            raise RuntimeError(f"Vietcap không có giá mới cho {symbol}")
        if result["empty"]:
            raise MarketNoDataError(f"Vietcap không có giá mới cho {symbol}")
        if fundamentals:
            self._refresh_fundamental(symbol)
        return result

    def _refresh_fundamental(self, symbol: str) -> dict | None:
        if self.universe().get(symbol) not in {"HSX", "HOSE", "HNX"}:
            return None
        fundamentals = importlib.import_module("vietcap_fundamental_data")
        return fundamentals.crawl_fundamental_data([symbol], RAW_DIR / "fundamental", workers=1)

    def _refresh_vietcap_market(self) -> dict:
        pipeline = importlib.import_module("daily_data_pipeline")
        target_day = previous_weekday(datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date())
        try:
            report = pipeline.run_pipeline("update", date(2020, 1, 1), 7, 2, 0.8, True,
                                           end_date=target_day)
        finally:
            imported = self.import_directory(RAW_DIR)
        failures = report["market"]["failed"] + report["index"]["failed"]
        if report["fundamental"]:
            failures += report["fundamental"]["failed"]
        if failures:
            raise RuntimeError(f"Vietcap backup cập nhật thiếu {failures} phần; dữ liệu cũ được giữ lại")
        if self.last_scan_session(datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()) != target_day.isoformat():
            raise RuntimeError(f"Vietcap chưa có VNINDEX cuối ngày hợp lệ cho {target_day}")
        with self.connect() as conn:
            conn.execute("""INSERT INTO market_refresh_state(key,value) VALUES('last_full_eod',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""", (target_day.isoformat(),))
        return {"source": "vietcap_backup", "crawl": report, "imported": imported}
