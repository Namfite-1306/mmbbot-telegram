from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, time
from pathlib import Path
from threading import Lock
from time import monotonic
from zoneinfo import ZoneInfo

import pandas as pd

from app.models import Action, Signal, SignalStatus
from app.market_store import BOT_DIR, CLEAN_DIR, MarketStore, previous_weekday
from app.providers.base import ProviderError, SignalProvider, TickerNotFoundError
from app.strategy_engine import (SampleStrategyEngine, StrategyDataError, VietcapStrategyEngine,
                                 calculate_total_score, market_regime)


logger = logging.getLogger(__name__)


class StrategySignalProvider(SignalProvider):
    """Bridge the strategy source and sample workbook to the Telegram contract."""

    name = "strategy"
    version = "vietcap_tfm_v1"
    timezone = ZoneInfo("Asia/Ho_Chi_Minh")

    def __init__(self, engine: SampleStrategyEngine | None = None, now: datetime | None = None,
                 database_path: Path | None = None, auto_refresh: bool = True):
        if engine is None:
            store = MarketStore(database_path or BOT_DIR / "data" / "fintech_bot.db")
            store.initialize()
            store.import_directory(CLEAN_DIR)
            engine = VietcapStrategyEngine(store)
        self.engine = engine
        self._now = now
        self.auto_refresh = auto_refresh and isinstance(engine, VietcapStrategyEngine)
        self._refresh_lock = Lock()
        self._scan_lock = asyncio.Lock()
        self._scan_cache: tuple[str, float, list[Signal]] | None = None
        self._scan_revision = 0
        self.last_refresh_error: str | None = None
        self.last_breadth: tuple[int, int] | None = None
        self.last_scan_date: str | None = None

    def _current_time(self) -> datetime:
        return self._now or datetime.now(self.timezone)

    def _make_signal(
        self, ticker: str, data_date: pd.Timestamp, status: SignalStatus,
        action: Action | None, price: float, score: float, reasons: list[str],
        score_components: dict[str, float | None] | None = None,
    ) -> Signal:
        data_time = datetime.combine(data_date.date(), time(15, 0), tzinfo=self.timezone)
        return Signal(
            signal_id=f"{self.version}-{ticker}-{data_date.date()}-{status.value.lower()}",
            ticker=ticker,
            action=action,
            price=price,
            score=score,
            reasons=reasons,
            data_time=data_time,
            generated_at=self._current_time(),
            strategy_version=self.version,
            timeframe="1D",
            status=status,
            score_components=score_components,
        )

    def _build_signal(self, ticker: str, refresh_error: str | None = None,
                      breadth_snapshot: tuple[str, tuple[int, int]] | None = None,
                      as_of: pd.Timestamp | None = None,
                      universe: dict[str, str] | None = None) -> Signal:
        normalized = ticker.strip().upper()
        if universe is None:
            try:
                universe = self.engine.universe()
            except StrategyDataError as exc:
                raise ProviderError(str(exc)) from exc
        if normalized not in universe:
            raise TickerNotFoundError(normalized)
        as_of = as_of if as_of is not None else pd.Timestamp(self._current_time().date())
        exchange = universe[normalized]
        try:
            prices, is_sample = self.engine.prices(normalized, as_of)
            last = prices.iloc[-1]
            technical = self.engine.technical(prices)
            reasons = [
                f"T={technical['trend']:.0f}/100; E={technical['entry']['status']}",
                *technical["entry"]["reasons"],
            ]
            status = SignalStatus.WATCH_ONLY
            action = None
            score = technical["trend"]
            fundamental_valid = False
            momentum_valid = False
            fundamental_score = None
            momentum_score = None
            benchmark = None
            regime = "UNKNOWN"
            if isinstance(self.engine, VietcapStrategyEngine):
                try:
                    benchmark = self.engine.index(last["date"], exchange)
                    if breadth_snapshot is None:
                        breadth = self.engine.store.breadth(last["date"].date().isoformat())
                    else:
                        breadth = (breadth_snapshot[1] if breadth_snapshot[0] == last["date"].date().isoformat()
                                   else None)
                    regime = market_regime(benchmark, breadth, last["date"])
                except StrategyDataError as exc:
                    reasons.append(f"Market regime UNKNOWN: {exc}")
                reasons.append(f"Market regime: {regime}")
            if exchange == "UPCOM":
                reasons.insert(0, "UPCOM: chỉ theo dõi kỹ thuật, không phát BUY")
            else:
                try:
                    fundamental_score = self.engine.fundamental(normalized, last["date"], exchange)
                    fundamental_valid = True
                except StrategyDataError as exc:
                    reasons.append(f"F chưa tính được: {exc}")
                try:
                    if benchmark is None:
                        benchmark = (self.engine.index(last["date"], exchange)
                                     if isinstance(self.engine, VietcapStrategyEngine)
                                     else self.engine.index(last["date"]))
                    momentum_score = self.engine.momentum(prices, benchmark)
                    momentum_valid = True
                except StrategyDataError as exc:
                    reasons.append(f"M chưa tính được: {exc}")
                if fundamental_valid and momentum_valid:
                    score = calculate_total_score(technical["trend"], fundamental_score, momentum_score)
                    reasons.insert(0, f"T={technical['trend']:.0f}, F={fundamental_score:.2f}, M={momentum_score:.0f}")
                else:
                    reasons.insert(0, "Chưa có điểm tổng T/F/M; hiển thị điểm T kỹ thuật")
            if isinstance(self.engine, VietcapStrategyEngine):
                if not bool(last["has_adjusted_history"]):
                    reasons.append("Chưa có adjusted OHLC; tín hiệu trên giá thô, không đồng nhất với PnL thực")
                if exchange not in {"HSX", "HOSE"}:
                    reasons.append("Ngoài HOSE: chỉ theo dõi, không phát BUY V1")
                elif technical["trend_exit"]:
                    status, action = SignalStatus.SUCCESS, Action.SELL
                    reasons.insert(0, "Trend Exit xác nhận tại Close; điều kiện thoát nếu đang nắm giữ, dự kiến Open phiên kế tiếp")
                elif (regime in {"BULL", "NEUTRAL"} and technical["entry"]["status"] == "ENTRY"
                      and ((fundamental_valid and momentum_valid and score >= 75)
                           or (not fundamental_valid and technical["trend"] >= 75))):
                    status, action = SignalStatus.SUCCESS, Action.BUY
                    reasons.insert(0, "Đạt cổng BUY V1 tại Close; giá thực hiện là Open phiên kế tiếp, không phải giá hiển thị")
                elif 60 <= score < 75:
                    reasons.append("WATCH V1: điểm trong khoảng 60–75")
                elif score >= 75:
                    reasons.append("Điểm đạt 75 nhưng cổng E/regime hoặc dữ liệu M chưa hợp lệ")
                if regime == "UNKNOWN":
                    reasons.append("Thiếu breadth/benchmark cùng phiên: không phát BUY mới")
                if fundamental_valid:
                    reasons.append("Valuation P/E, P/B chỉ được tính khi có ngành và peer cùng kỳ hợp lệ")
            else:
                reasons.append("Dữ liệu mẫu; không phát lệnh tự động")
            if refresh_error:
                reasons.append(f"Không làm mới được dữ liệu: {refresh_error}")
                if action is Action.BUY:
                    status, action = SignalStatus.WATCH_ONLY, None
                    reasons.insert(0, "Cập nhật chưa đủ; tạm chặn BUY mới")
            if (pd.Timestamp(self._current_time().date()) - last["date"]).days > 5:
                status = SignalStatus.STALE_DATA
                action = None
                reasons.insert(0, "Dữ liệu giá đã cũ; không phát tín hiệu giao dịch")
            if breadth_snapshot is not None and last["date"] != as_of:
                status = SignalStatus.STALE_DATA
                action = None
                reasons.insert(0, "Mã chưa có giá đúng phiên quét; không phát BUY/SELL")
            if is_sample:
                reasons.append("Giá từ workbook Yahoo mẫu; không phải dữ liệu thị trường trực tiếp")
            return self._make_signal(
                normalized, last["date"], status,
                action,
                float(last["display_close"]),
                score, reasons,
                {"T": technical["trend"], "F": fundamental_score, "M": momentum_score},
            )
        except StrategyDataError as exc:
            return self._make_signal(
                normalized, as_of, SignalStatus.INSUFFICIENT_DATA,
                None, 0, 0, [str(exc)],
            )
        except (OSError, ValueError, TypeError, KeyError, ImportError, sqlite3.Error) as exc:
            raise ProviderError(f"Không xử lý được dữ liệu chiến lược cho {normalized}") from exc

    async def get_signal(self, ticker: str) -> Signal:
        normalized = ticker.strip().upper()
        if normalized not in await asyncio.to_thread(self.engine.universe):
            raise TickerNotFoundError(normalized)
        return await asyncio.to_thread(self._refresh_and_build, normalized)

    async def get_saved_signal(self, ticker: str) -> Signal:
        """Evaluate a watchlist symbol from stored data without triggering a crawl."""
        return await asyncio.to_thread(self._build_signal, ticker)

    def _refresh_and_build(self, ticker: str) -> Signal:
        error = None
        if self.auto_refresh:
            try:
                with self._refresh_lock:
                    self.engine.store.refresh(ticker)
            except (OSError, RuntimeError, ValueError) as exc:
                logger.warning("Ticker refresh failed for %s: %s", ticker, exc)
                error = str(exc)[:150]
            finally:
                # A failed refresh may still have imported some valid bars.
                self._scan_revision += 1
                self._scan_cache = None
        return self._build_signal(ticker, error)

    async def get_market_signals(self) -> list[Signal]:
        async with self._scan_lock:
            scan_day = None
            if isinstance(self.engine, VietcapStrategyEngine):
                scan_day = await asyncio.to_thread(
                    self.engine.store.last_scan_session, self._current_time().date())
                if not scan_day:
                    expected = previous_weekday(self._current_time().date())
                    raise ProviderError(f"Chưa có VNINDEX cuối ngày hợp lệ cho {expected}; "
                                        "không dùng phiên cũ để quét")
                self.last_scan_date = scan_day
                cached = self._scan_cache
                if cached and cached[0] == scan_day and monotonic() - cached[1] < 300:
                    return list(cached[2])
            revision = self._scan_revision
            signals = await self._evaluate_market(scan_day)
            if scan_day and revision == self._scan_revision:
                self._scan_cache = (scan_day, monotonic(), signals)
            return signals

    async def _evaluate_market(self, scan_day: str | None) -> list[Signal]:
        try:
            universe = await asyncio.to_thread(self.engine.universe)
            tickers = sorted(universe)
            self.last_refresh_error = None
            if isinstance(self.engine, VietcapStrategyEngine):
                assert scan_day is not None
                scan_as_of = pd.Timestamp(scan_day)
                breadth = await asyncio.to_thread(self.engine.store.breadth, scan_day)
                self.last_breadth = breadth if breadth[1] else None
                breadth_snapshot = (scan_day, breadth)
                latest_price_dates = await asyncio.to_thread(
                    self.engine.store.latest_price_dates_through, scan_day)
            else:
                self.last_scan_date = None
                scan_as_of = None
                breadth_snapshot = None
                latest_price_dates = None
        except (OSError, ValueError, StrategyDataError) as exc:
            raise ProviderError("Không đọc được danh sách toàn thị trường") from exc
        def evaluate_all() -> list[Signal]:
            def evaluate_one(ticker: str) -> Signal:
                try:
                    if latest_price_dates is not None:
                        last_day = latest_price_dates.get(ticker)
                        if last_day != scan_day:
                            return self._make_signal(
                                ticker, pd.Timestamp(last_day or scan_day),
                                SignalStatus.STALE_DATA if last_day else SignalStatus.INSUFFICIENT_DATA,
                                None, 0, 0,
                                ["Mã chưa có giá hợp lệ đúng phiên quét; không phát BUY/SELL"],
                            )
                    return self._build_signal(ticker, None, breadth_snapshot,
                                              scan_as_of, universe)
                except ProviderError as exc:
                    logger.warning("Strategy evaluation failed for %s: %s", ticker, exc)
                    return self._make_signal(
                        ticker, pd.Timestamp(self._current_time().date()),
                        SignalStatus.PROVIDER_ERROR, None, 0, 0, [str(exc)],
                    )
            # Each ticker opens SQLite readers and calls pandas indicators. On
            # this workload, eight Python workers were slower than one worker
            # because they contend for SQLite/file-cache access; the entire
            # scan already runs in asyncio.to_thread, keeping Telegram responsive.
            return [evaluate_one(ticker) for ticker in tickers]
        return await asyncio.to_thread(evaluate_all)

    async def supports_ticker(self, ticker: str) -> bool:
        try:
            return ticker.strip().upper() in await asyncio.to_thread(self.engine.universe)
        except StrategyDataError as exc:
            raise ProviderError(str(exc)) from exc

    def latest_data_time(self) -> str:
        try:
            if isinstance(self.engine, VietcapStrategyEngine):
                trade_day, covered, universe = self.engine.store.latest_coverage()
                return f"{trade_day} ({covered}/{universe} mã cuối ngày, SQLite DNSE/VCI/CafeF)"
            return self.engine.sample()["date"].max().date().isoformat() + " (workbook mẫu)"
        except (OSError, ValueError, StrategyDataError):
            return "Chưa có dữ liệu mẫu"
