from __future__ import annotations

import asyncio
import csv
from datetime import date, datetime
from time import monotonic
from threading import Lock
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app.market_store import CRAWLER_DIR, MarketStore, previous_weekday
from app.main import _eod_refresh_loop, build_application
from app.config import Settings
from app.models import SignalStatus
from app.providers.base import ProviderError
from app.providers.strategy_signal_provider import StrategySignalProvider
from app.strategy_engine import VietcapStrategyEngine
from app.trading_calendar import is_trading_day, previous_trading_day


def _write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_exchange_holidays_are_not_expected_as_trading_sessions():
    assert previous_trading_day(date(2026, 1, 5)) == date(2025, 12, 31)
    assert previous_trading_day(date(2026, 9, 3)) == date(2026, 8, 28)
    assert previous_trading_day(date(2026, 9, 24)) == date(2026, 9, 23)
    assert not is_trading_day(date(2026, 8, 22))  # makeup Saturday is not a market session


def test_scan_uses_last_trading_day_across_exchange_holiday(tmp_path):
    store = MarketStore(tmp_path / "holiday.db")
    store.initialize()
    with store.connect() as conn:
        conn.execute("""INSERT INTO market_indices(symbol,trade_date,close,is_final)
            VALUES('VNINDEX','2026-08-28',1800,1)""")
    assert store.last_scan_session(date(2026, 9, 3)) == "2026-08-28"


def test_import_is_idempotent_and_rejects_invalid_ohlc(tmp_path):
    source = tmp_path / "source"
    _write_csv(source / "symbol_info_cache.csv", [
        {"symbol": "AAA", "exchange": "HSX", "security_type": "STOCK"},
        {"symbol": "BBB", "exchange": "HNX", "security_type": "STOCK"},
    ])
    _write_csv(source / "market_data" / "AAA.csv", [
        {"symbol": "AAA", "trade_date": "2026-09-17", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100, "is_final": True},
        {"symbol": "AAA", "trade_date": "2026-09-18", "open": 11, "high": 12, "low": 10, "close": 11, "volume": 100, "is_final": True},
        {"symbol": "AAA", "trade_date": "2026-09-19", "open": 11, "high": 8, "low": 10, "close": 11, "volume": 100, "is_final": True},
    ])
    _write_csv(source / "market_data" / "BBB.csv", [
        {"symbol": "BBB", "trade_date": "2026-09-17", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100, "is_final": True},
        {"symbol": "BBB", "trade_date": "2026-09-18", "open": 9, "high": 10, "low": 8, "close": 9, "volume": 100, "is_final": True},
    ])
    _write_csv(source / "index_data" / "VNINDEX.csv", [
        {"symbol": "VNINDEX", "trade_date": "2026-09-17", "close": 1000, "is_final": True},
        {"symbol": "VNINDEX", "trade_date": "2026-09-18", "close": 1001, "is_final": True},
    ])
    _write_csv(source / "fundamental" / "AAA_fundamental.csv", [
        {"symbol": "AAA", "report_type": "quarterly", "report_period": "2025Q1", "announcement_date": "2025-04-30", "revenue": 123,
         "operating_cash_flow": -5, "total_debt": 10, "data_quality_flags": "NEGATIVE_NUMERIC"},
        {"symbol": "AAA", "report_type": "yearly", "report_period": "2025Q1", "announcement_date": "2025-05-01", "revenue": 456,
         "operating_cash_flow": 5, "total_debt": -10, "data_quality_flags": "NEGATIVE_NUMERIC"},
    ])
    store = MarketStore(tmp_path / "market.db")
    first = store.import_directory(source)
    second = store.import_directory(source)
    assert first["prices"] == 4
    assert all(value == 0 for value in second.values())
    assert len(store.prices("AAA")) == 2
    fundamentals = store.fundamentals("AAA")
    assert len(fundamentals) == 2
    assert fundamentals.loc[fundamentals["report_type"].eq("quarterly"), "data_quality_flags"].iloc[0] == ""
    assert fundamentals.loc[fundamentals["report_type"].eq("yearly"), "data_quality_flags"].iloc[0] == "INVALID_NEGATIVE_BALANCE"
    assert store.breadth("2026-09-18") == (1, 1)  # HNX is excluded.
    assert store.last_scan_session(date(2026, 9, 19)) == "2026-09-18"
    assert store.last_scan_session(date(2026, 9, 18)) == "2026-09-17"
    assert store.latest_price_dates_through("2026-09-18") == {
        "AAA": "2026-09-18", "BBB": "2026-09-18"}


def test_scan_requires_exact_prior_weekday_and_final_rows_cannot_regress(tmp_path):
    source = tmp_path / "source"
    _write_csv(source / "market_data" / "AAA.csv", [
        {"symbol": "AAA", "trade_date": "2026-09-21", "open": 10, "high": 11,
         "low": 9, "close": 10, "volume": 100, "is_final": True},
    ])
    _write_csv(source / "index_data" / "VNINDEX.csv", [
        {"symbol": "VNINDEX", "trade_date": "2026-09-21", "close": 1799.67,
         "is_final": True},
    ])
    store = MarketStore(tmp_path / "market.db")
    store.import_directory(source)
    assert store.last_scan_session(date(2026, 9, 22)) == "2026-09-21"
    assert store.last_scan_session(date(2026, 9, 23)) is None
    with store.connect() as conn:
        conn.execute("""INSERT INTO market_indices(symbol,trade_date,close,is_final)
            VALUES('VNINDEX','2026-09-22',1800,1)""")
    assert store.last_scan_session(date(2026, 9, 23)) == "2026-09-22"
    _write_csv(source / "market_data" / "AAA.csv", [
        {"symbol": "AAA", "trade_date": "2026-09-21", "open": 10, "high": 11,
         "low": 9, "close": 10.5, "volume": 20, "is_final": False},
    ])
    _write_csv(source / "index_data" / "VNINDEX.csv", [
        {"symbol": "VNINDEX", "trade_date": "2026-09-21", "close": 1809.21,
         "is_final": False},
    ])
    store.import_directory(source)
    with store.connect() as conn:
        price = conn.execute("SELECT close,is_final FROM market_prices WHERE symbol='AAA'").fetchone()
        index = conn.execute("SELECT close,is_final FROM market_indices WHERE symbol='VNINDEX'").fetchone()
    assert tuple(price) == (10.0, 1)
    assert tuple(index) == (1799.67, 1)


def test_strategy_scan_uses_previous_session_without_network(tmp_path, monkeypatch):
    source = tmp_path / "source"
    _write_csv(source / "symbol_info_cache.csv", [
        {"symbol": "AAA", "exchange": "HSX", "security_type": "STOCK"},
        {"symbol": "BBB", "exchange": "UPCOM", "security_type": "STOCK"},
    ])
    _write_csv(source / "index_data" / "VNINDEX.csv", [
        {"symbol": "VNINDEX", "trade_date": "2026-09-18", "close": 1000, "is_final": True},
        {"symbol": "VNINDEX", "trade_date": "2026-09-21", "close": 1001, "is_final": True},
    ])
    store = MarketStore(tmp_path / "market.db")
    store.import_directory(source)
    monkeypatch.setattr(store, "refresh", lambda *args, **kwargs: pytest.fail("/scan must not crawl"))
    provider = StrategySignalProvider(
        engine=VietcapStrategyEngine(store),
        now=datetime(2026, 9, 21, 20, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        auto_refresh=True)
    signals = asyncio.run(provider.get_market_signals())
    assert {signal.ticker for signal in signals} == {"AAA", "BBB"}
    assert all(signal.status is SignalStatus.INSUFFICIENT_DATA for signal in signals)
    assert provider.last_scan_date == "2026-09-18"
    monkeypatch.setattr(provider, "_evaluate_market",
                        lambda *args: pytest.fail("Second /scan should use cached signals"))
    assert asyncio.run(provider.get_market_signals()) == signals


def test_manual_scan_falls_back_one_session_only_and_prefers_newer_data(tmp_path):
    store = MarketStore(tmp_path / "market.db")
    store.initialize()
    with store.connect() as conn:
        conn.execute("""INSERT INTO market_indices(symbol,trade_date,close,is_final)
            VALUES('VNINDEX','2026-09-23',1800,1)""")
    provider = StrategySignalProvider(
        engine=VietcapStrategyEngine(store),
        now=datetime(2026, 9, 25, 10, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")))
    with pytest.raises(ProviderError, match="2026-09-24"):
        asyncio.run(provider.get_market_signals())
    assert provider.last_scan_date is None

    asyncio.run(provider.get_market_signals(allow_previous_session=True))
    assert provider.last_scan_date == "2026-09-23"

    with store.connect() as conn:
        conn.execute("""INSERT INTO market_indices(symbol,trade_date,close,is_final)
            VALUES('VNINDEX','2026-09-24',1801,1)""")
    asyncio.run(provider.get_market_signals(allow_previous_session=True))
    assert provider.last_scan_date == "2026-09-24"

    with store.connect() as conn:
        conn.execute("DELETE FROM market_indices WHERE trade_date IN ('2026-09-23','2026-09-24')")
        conn.execute("""INSERT INTO market_indices(symbol,trade_date,close,is_final)
            VALUES('VNINDEX','2026-09-22',1799,1)""")
    with pytest.raises(ProviderError, match="phiên giao dịch liền trước"):
        asyncio.run(provider.get_market_signals(allow_previous_session=True))
    assert provider.last_scan_date is None


def test_soi_refreshes_only_requested_symbol(monkeypatch):
    calls = []

    class Store:
        def universe(self):
            return {"AAA": "HSX", "BBB": "HSX"}

        def refresh(self, symbol=None):
            calls.append(symbol)

    provider = StrategySignalProvider(engine=VietcapStrategyEngine(Store()), auto_refresh=True)
    provider._scan_cache = ("2026-09-18", monotonic(), [])
    monkeypatch.setattr(provider, "_build_signal", lambda ticker, error=None: ticker)
    assert asyncio.run(provider.get_signal("AAA")) == "AAA"
    assert calls == ["AAA"]
    assert provider._scan_cache is None


def test_scan_passes_previous_session_cutoff_to_every_symbol(monkeypatch):
    class Store:
        def universe(self):
            return {"AAA": "HSX", "BBB": "HNX"}

        def last_scan_session(self, before):
            assert before == date(2026, 9, 21)
            return "2026-09-18"

        def breadth(self, trade_day):
            assert trade_day == "2026-09-18"
            return 1, 2

        def latest_price_dates_through(self, trade_day):
            assert trade_day == "2026-09-18"
            return {"AAA": trade_day, "BBB": trade_day}

        def refresh(self, *args):
            pytest.fail("/scan must not call data sources")

    provider = StrategySignalProvider(
        engine=VietcapStrategyEngine(Store()),
        now=datetime(2026, 9, 21, 20, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        auto_refresh=True)
    cutoffs = []

    def build(ticker, refresh_error, breadth_snapshot, as_of, universe):
        cutoffs.append((ticker, as_of.date().isoformat(), breadth_snapshot[0]))
        return ticker

    monkeypatch.setattr(provider, "_build_signal", build)
    assert asyncio.run(provider.get_market_signals()) == ["AAA", "BBB"]
    assert sorted(cutoffs) == [("AAA", "2026-09-18", "2026-09-18"),
                               ("BBB", "2026-09-18", "2026-09-18")]


def test_full_refresh_skips_second_eod_crawl(tmp_path, monkeypatch):
    store = MarketStore(tmp_path / "market.db")
    store.initialize()
    today = previous_trading_day(datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()).isoformat()
    with store.connect() as conn:
        conn.execute("INSERT INTO market_refresh_state VALUES('last_full_eod',?)", (today,))
        conn.execute("INSERT INTO market_indices(symbol,trade_date,close,is_final) VALUES('VNINDEX',?,1000,1)", (today,))
        conn.execute("INSERT INTO market_symbols VALUES('AAA','HSX','STOCK')")
        conn.execute("""INSERT INTO market_prices(symbol,trade_date,close,volume,is_final)
            VALUES('AAA',?,100,100,1)""", (today,))
    monkeypatch.setattr("app.market_store.importlib.import_module",
                        lambda name: pytest.fail(f"Unexpected crawler import: {name}"))
    assert "skipped" in store.refresh()


def test_eod_completion_requires_index_and_market_coverage(tmp_path):
    store = MarketStore(tmp_path / "market.db")
    store.initialize()
    previous, target = "2026-09-23", "2026-09-24"
    symbols = [f"A{i}" for i in range(10)]
    with store.connect() as conn:
        conn.executemany("INSERT INTO market_symbols VALUES(?,'HSX','STOCK')",
                         [(symbol,) for symbol in symbols])
        conn.executemany("""INSERT INTO market_indices(symbol,trade_date,close,is_final)
            VALUES('VNINDEX',?,1000,1)""", [(previous,), (target,)])
        conn.executemany("""INSERT INTO market_prices(symbol,trade_date,close,volume,is_final)
            VALUES(?,?,100,100,1)""",
            [(symbol, previous) for symbol in symbols[:8]]
            + [(symbol, target) for symbol in symbols[:6]])
    status = store.record_eod_attempt(target, source="dnse", failed_symbols=["A9"])
    assert not status["ready"]
    assert status["required"] == 8  # 90% of eight valid bars in preceding session.
    assert status["covered"] == 6 and status["universe"] == 10
    assert status["failed_symbols"] == ["A9"]
    assert store.last_eod_refresh_date() is None
    assert store.last_eod_refresh_status()["missing_symbols"] == symbols[6:]

    with store.connect() as conn:
        conn.executemany("""INSERT INTO market_prices(symbol,trade_date,close,volume,is_final)
            VALUES(?,?,100,100,1)""", [(symbol, target) for symbol in symbols[6:8]])
    status = store.record_eod_attempt(target, source="dnse")
    assert status["ready"] and status["covered"] == 8
    assert store.last_eod_refresh_date() == target
    assert store.record_eod_attempt(target, source="dnse")["covered"] == 8


def test_full_refresh_uses_vietcap_backup_when_dnse_preflight_fails(tmp_path, monkeypatch):
    store = MarketStore(tmp_path / "market.db")
    store.initialize()
    with store.connect() as conn:
        conn.execute("INSERT INTO market_symbols VALUES('AAA','HSX','STOCK')")

    class TimedOutClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_ohlcv_bars(self, *args, **kwargs):
            from app.dnse_market_data import DNSEDataError
            raise DNSEDataError("DNSE timeout")

    monkeypatch.setattr("app.market_store.DNSEMarketClient", TimedOutClient)
    monkeypatch.setattr(store, "_refresh_vietcap_market", lambda: {"source": "vietcap_backup"})
    monkeypatch.setattr(store, "_finish_full_refresh", lambda day, report: report)
    assert store.refresh()["source"] == "vietcap_backup"


def test_full_refresh_uses_cafef_when_both_primary_sources_fail(tmp_path, monkeypatch):
    store = MarketStore(tmp_path / "market.db")
    store.initialize()
    with store.connect() as conn:
        conn.execute("INSERT INTO market_symbols VALUES('AAA','HSX','STOCK')")

    class TimedOutClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_ohlcv_bars(self, *args, **kwargs):
            from app.dnse_market_data import DNSEDataError
            raise DNSEDataError("DNSE timeout")

    monkeypatch.setattr("app.market_store.DNSEMarketClient", TimedOutClient)
    monkeypatch.setattr(store, "_refresh_vietcap_market",
                        lambda: (_ for _ in ()).throw(RuntimeError("VCI timeout")))
    monkeypatch.setattr(store, "_refresh_cafef_market",
                        lambda *args, **kwargs: {"source": "cafef_backup"})
    monkeypatch.setattr(store, "_finish_full_refresh", lambda day, report: report)
    assert store.refresh()["source"] == "cafef_backup"


def test_cafef_keeps_final_primary_price_and_adds_new_day(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    monkeypatch.setattr("app.market_store.RAW_DIR", raw)
    monkeypatch.syspath_prepend(str(CRAWLER_DIR))
    store = MarketStore(tmp_path / "market.db")
    store.initialize()
    with store.connect() as conn:
        conn.execute("INSERT INTO market_symbols VALUES('FPT','HSX','STOCK')")
        conn.execute("""INSERT INTO market_prices(symbol,trade_date,exchange,open,high,low,close,volume,is_final,source)
            VALUES('FPT','2026-09-18','HSX',65000,66000,64000,65200,100,1,'vietcap')""")

    class Client:
        def get_ohlcv_bars(self, *args, **kwargs):
            return [
                {"trade_date": "2026-09-18", "open": 65.0, "high": 66.0,
                 "low": 64.0, "close": 65.2, "volume": 200},
                {"trade_date": "2026-09-21", "open": 66.5, "high": 66.8,
                 "low": 65.0, "close": 66.4, "volume": 300},
            ]

    assert store._refresh_cafef_symbol("FPT", date(2026, 9, 18), date(2026, 9, 21),
                                       client=Client()) == 1
    store.import_directory(raw)
    with store.connect() as conn:
        rows = conn.execute("SELECT trade_date,close,source FROM market_prices ORDER BY trade_date").fetchall()
    assert [tuple(row) for row in rows] == [
        ("2026-09-18", 65200.0, "vietcap"), ("2026-09-21", 66400.0, "cafef")]


def test_cafef_uses_stale_historical_anchor_to_verify_scale(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    monkeypatch.setattr("app.market_store.RAW_DIR", raw)
    monkeypatch.syspath_prepend(str(CRAWLER_DIR))
    store = MarketStore(tmp_path / "market.db")
    store.initialize()
    with store.connect() as conn:
        conn.execute("INSERT INTO market_symbols VALUES('AME','HNX','STOCK')")
        conn.execute("""INSERT INTO market_prices(symbol,trade_date,exchange,open,high,low,close,volume,is_final,source)
            VALUES('AME','2026-09-03','HNX',6800,6800,6800,6800,100,1,'vietcap')""")

    class Client:
        def get_ohlcv_bars(self, symbol, exchange, start, end, *, index=False):
            if end == date(2026, 9, 21):
                return [{"trade_date": "2026-09-21", "open": 7.0, "high": 7.2,
                         "low": 6.9, "close": 7.1, "volume": 1000}]
            assert end == date(2026, 9, 4)
            return [{"trade_date": "2026-09-03", "open": 6.8, "high": 6.8,
                     "low": 6.8, "close": 6.8, "volume": 100}]

    assert store._refresh_cafef_symbol("AME", date(2026, 9, 14), date(2026, 9, 21),
                                       client=Client()) == 1
    store.import_directory(raw)
    with store.connect() as conn:
        rows = conn.execute("SELECT trade_date,close,source FROM market_prices ORDER BY trade_date").fetchall()
    assert [tuple(row) for row in rows] == [
        ("2026-09-03", 6800.0, "vietcap"), ("2026-09-21", 7100.0, "cafef")]


def test_cafef_rejects_unmatched_historical_anchor(tmp_path, monkeypatch):
    from app.cafef_market_data import CafeFDataError

    store = MarketStore(tmp_path / "market.db")
    store.initialize()
    with store.connect() as conn:
        conn.execute("INSERT INTO market_symbols VALUES('AME','HNX','STOCK')")
        conn.execute("""INSERT INTO market_prices(symbol,trade_date,exchange,open,high,low,close,volume,is_final,source)
            VALUES('AME','2026-09-03','HNX',6800,6800,6800,6800,100,1,'vietcap')""")

    class Client:
        def get_ohlcv_bars(self, symbol, exchange, start, end, *, index=False):
            if end == date(2026, 9, 21):
                return [{"trade_date": "2026-09-21", "open": 7.0, "high": 7.2,
                         "low": 6.9, "close": 7.1, "volume": 1000}]
            return []

    with pytest.raises(CafeFDataError, match="phiên lịch sử"):
        store._refresh_cafef_symbol("AME", date(2026, 9, 14), date(2026, 9, 21),
                                    client=Client())


def test_market_scan_does_not_stop_on_stocks_without_recent_bars(tmp_path, monkeypatch):
    from app.cafef_market_data import CafeFNoDataError
    from app.market_store import MarketNoDataError

    store = MarketStore(tmp_path / "market.db")
    store.initialize()
    with store.connect() as conn:
        conn.executemany("INSERT INTO market_symbols VALUES(?,'HSX','STOCK')",
                         [(f"A{i}",) for i in range(6)])
        conn.execute("""INSERT INTO market_prices(symbol,trade_date,exchange,open,high,low,close,volume,is_final)
            VALUES('A0',?,'HSX',100,100,100,100,1,1)""",
            (previous_weekday(datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()).isoformat(),))

    class EmptyStocksClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_ohlcv_bars(self, symbol, *args, **kwargs):
            assert args[1] == previous_trading_day(datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date())
            return ([{"trade_date": args[1].isoformat(), "open": 100, "high": 100,
                      "low": 100, "close": 100, "volume": 1}]
                    if kwargs.get("index") else [])

    monkeypatch.setattr("app.market_store.DNSEMarketClient", EmptyStocksClient)
    monkeypatch.setattr(store, "_refresh_vietcap_symbol",
                        lambda *args, **kwargs: (_ for _ in ()).throw(MarketNoDataError("no bars")))
    monkeypatch.setattr(store, "_refresh_cafef_symbol",
                        lambda *args, **kwargs: (_ for _ in ()).throw(CafeFNoDataError("no bars")))
    def save_bars(symbol, bars, *, index):
        if index and symbol == "VNINDEX":
            with store.connect() as conn:
                conn.execute("""INSERT INTO market_indices(symbol,trade_date,close,is_final)
                    VALUES('VNINDEX',?,100,1)""",
                    (previous_weekday(datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()).isoformat(),))
        return 1

    monkeypatch.setattr(store, "_save_dnse_bars", save_bars)
    monkeypatch.setattr(store, "_refresh_vietcap_or_cafef_market",
                        lambda *args, **kwargs: pytest.fail("No-data is not a provider outage"))
    monkeypatch.setattr(store, "import_directory", lambda *args, **kwargs: {})
    monkeypatch.setattr("app.market_store.importlib.import_module",
                        lambda name: SimpleNamespace(crawl_fundamental_data=lambda *a, **k: {"failed": 0}))
    with pytest.raises(RuntimeError, match="chưa đủ dữ liệu"):
        store.refresh()
    status = store.last_eod_refresh_status()
    assert status["covered"] == 1 and status["universe"] == 6
    assert store.last_eod_refresh_date() is None


def test_cafef_market_backup_skips_empty_symbols_before_valid_one(tmp_path, monkeypatch):
    from app.cafef_market_data import CafeFNoDataError

    store = MarketStore(tmp_path / "market.db")
    store.initialize()
    with store.connect() as conn:
        conn.executemany("INSERT INTO market_symbols VALUES(?,'HSX','STOCK')",
                         [(f"A{i}",) for i in range(6)])
    monkeypatch.setattr("app.market_store.sleep", lambda seconds: None)
    monkeypatch.setattr(store, "import_directory", lambda *args, **kwargs: {})

    def refresh_one(symbol, *args, **kwargs):
        if symbol != "A5" and symbol not in {"VNINDEX", "HNXINDEX", "UPCOMINDEX"}:
            raise CafeFNoDataError("no bars")
        return 1

    monkeypatch.setattr(store, "_refresh_cafef_symbol", refresh_one)
    result = store._refresh_cafef_market(date(2026, 9, 14), date(2026, 9, 21),
                                         vietcap_error="VCI unavailable")
    assert result["cafef_prices"] == 1
    assert result["empty"] == [f"A{i}" for i in range(5)]
    assert result["failed"] == []


def test_dnse_price_scale_is_verified_before_upsert(tmp_path, monkeypatch):
    from app.dnse_market_data import DNSEDataError

    raw = tmp_path / "raw"
    monkeypatch.setattr("app.market_store.RAW_DIR", raw)
    monkeypatch.syspath_prepend(str(CRAWLER_DIR))
    store = MarketStore(tmp_path / "market.db")
    store.initialize()
    with store.connect() as conn:
        conn.execute("INSERT INTO market_symbols VALUES('AAA','HSX','STOCK')")
        conn.execute("""INSERT INTO market_prices(symbol,trade_date,exchange,open,high,low,close,volume,is_final,source)
            VALUES('AAA','2026-09-18','HSX',65000,66000,64000,65200,100,1,'vietcap')""")
    store._save_dnse_bars("AAA", [{"trade_date": "2026-09-18", "open": 65.0,
                                     "high": 66.0, "low": 64.0, "close": 65.2, "volume": 200}], index=False)
    store.import_directory(raw)
    with store.connect() as conn:
        row = conn.execute("SELECT close,source FROM market_prices WHERE symbol='AAA'").fetchone()
    assert tuple(row) == (65200.0, "dnse")
    with pytest.raises(DNSEDataError, match="không khớp"):
        store._save_dnse_bars("AAA", [{"trade_date": "2026-09-18", "open": 2.0,
                                         "high": 2.1, "low": 1.9, "close": 2.0, "volume": 200}], index=False)


def test_startup_tasks_wait_until_polling_is_running(tmp_path, monkeypatch):
    class Service:
        async def scan(self):
            return []

    runtime = SimpleNamespace(signal_service=Service(), subscriptions=None,
                              database=None,
                              notifications=SimpleNamespace(send_daily_digest=AsyncMock(return_value=0)))
    settings = Settings(telegram_bot_token="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrst",
                        signal_provider="strategy", database_path=tmp_path / "bot.db")
    application = build_application(settings, runtime)
    monkeypatch.setattr(type(application), "create_task",
                        lambda *args, **kwargs: pytest.fail("Application.create_task called before start"))
    monkeypatch.setattr(type(application.bot), "set_my_commands", AsyncMock())

    async def scenario():
        await application.post_init(application)
        await asyncio.sleep(0)
        assert "startup_scan_task" not in application.bot_data
        application._running = True
        await asyncio.wait_for(application.bot_data["background_start_task"], 1)
        await application.bot_data["startup_scan_task"]
        assert application.bot_data["startup_scan_count"] == 0
        application._running = False
        await application.post_shutdown(application)

    asyncio.run(scenario())


def test_eod_health_alerts_once_on_failure_and_on_recovery(monkeypatch):
    expected = previous_trading_day(datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()).isoformat()

    class Store:
        ready = False
        attempts = 0

        def last_eod_refresh_date(self):
            return expected if self.ready else None

        def last_scan_session(self, today):
            return expected if self.ready else None

        def refresh(self, *, include_fundamentals):
            assert include_fundamentals is False
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("temporary")
            self.ready = True
            return {}

    async def scenario():
        store = Store()
        provider = SimpleNamespace(engine=SimpleNamespace(store=store), _refresh_lock=Lock(),
                                   _scan_revision=0, _scan_cache=None)
        health = {}
        messages = []

        async def notify(message):
            messages.append(message)

        sleeps = 0

        async def stop_after_two_loops(delay):
            nonlocal sleeps
            sleeps += 1
            if sleeps == 2:
                raise asyncio.CancelledError

        monkeypatch.setattr("app.main.asyncio.sleep", stop_after_two_loops)
        with pytest.raises(asyncio.CancelledError):
            await _eod_refresh_loop(provider, health, notify)
        assert store.attempts == 2
        assert health["state"] == "ready"
        assert len(messages) == 2
        assert messages[0].startswith("⚠️") and messages[1].startswith("✅")

    asyncio.run(scenario())
