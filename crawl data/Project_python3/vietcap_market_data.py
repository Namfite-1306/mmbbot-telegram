"""
vietcap_market_data.py
========================

Crawl Market Data (Price/Volume/OHLCV) từ Vietcap, output theo schema:

    symbol, trade_date, open, high, low, close, volume, total_value,
    ref_price, ceiling, floor, foreign_buy_volume, foreign_sell_volume,
    put_through_volume, exchange, security_type, timeframe, currency,
    source, is_final, fetched_at

GIỚI HẠN QUAN TRỌNG (đọc trước khi dùng):
    Vietcap KHÔNG có endpoint miễn phí trả về ref_price / ceiling /
    floor / foreign_buy_volume / foreign_sell_volume / put_through_volume
    / total_value cho một NGÀY BẤT KỲ TRONG QUÁ KHỨ. Các field này chỉ
    tồn tại trong priceboard (bảng giá SỐNG của ngày hiện tại).

    => Với dữ liệu lịch sử (quá khứ): 6 field trên sẽ để trống (None).
    => Với ngày hôm nay: nếu bật --enrich-today, script sẽ gọi thêm
       priceboard và join vào để lấp đầy đủ các field này.
    => Muốn có đầy đủ field này cho NHIỀU NGÀY về sau: phải chạy job
       `auto` (hoặc cron) mỗi ngày để tự tích lũy - xem lệnh `auto`.

    exchange / security_type: lấy từ cache priceboard (gần như tĩnh,
    hiếm khi đổi), áp dụng được cho toàn bộ lịch sử của mã.

Chức năng CLI:
    list-symbols, history, priceboard, realtime, auto
    (giữ nguyên hành vi như bản trước, chỉ đổi format output của
    `history`/`auto` sang đúng schema Market Data ở trên)
"""

from __future__ import annotations

import argparse
import csv
import threading
import time

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from vietcap_common import (
    BaseVietcapClient,
    DEFAULT_SYMBOL_INFO_CACHE,
    write_csv,
    merge_csv_dedup,
    is_trading_hour,
    load_symbol_info_cache,
    get_logger,
)

try:
    import schedule  # pip install schedule
    SCHEDULE_AVAILABLE = True
except ImportError:
    SCHEDULE_AVAILABLE = False


log = get_logger("vietcap_market_data")

VN_TZ = timezone(timedelta(hours=7))


# ============================================================================
# ENDPOINTS
# ============================================================================

TRADING_BASE_URL = "https://trading.vietcap.com.vn/api/"
OHLC_ENDPOINT = TRADING_BASE_URL + "chart/OHLCChart/gap-chart"
INTRADAY_ENDPOINT = TRADING_BASE_URL + "market-watch/LEData/getAll"
PRICEBOARD_ENDPOINT = TRADING_BASE_URL + "price/v1/w/priceboard/tickers/price/group"
LISTING_ENDPOINT = (
    "https://iq.vietcap.com.vn/api/iq-insight-service/v2/company/search-bar?language=1"
)

INTERVAL_MAP = {"1m": "ONE_MINUTE", "1H": "ONE_HOUR", "1D": "ONE_DAY"}
TIMEFRAME_MAP = {"1m": "1m", "1H": "1h", "1D": "1d"}

PRICEBOARD_RAW_MAP = {
    "s": "symbol", "bo": "exchange", "st": "security_type",
    "ref": "ref_price", "cei": "ceiling", "flo": "floor",
    "va": "total_value_bn",
    "frbv": "foreign_buy_volume", "frsv": "foreign_sell_volume",
    "ptv": "put_through_volume",
}


# ============================================================================
# CLIENT
# ============================================================================

class VietcapMarketClient(BaseVietcapClient):

    def get_all_symbols(self) -> List[dict]:
        data = self._request("GET", LISTING_ENDPOINT)
        rows = data.get("data") if isinstance(data, dict) else []
        out, seen = [], set()
        for row in rows or []:
            sym = row.get("code")
            if not sym:
                continue
            sym = str(sym).strip().upper()
            if sym in seen:
                continue
            seen.add(sym)
            out.append({"symbol": sym, "organ_name": row.get("name")})
        return out

    def get_ohlcv_bars(
        self,
        symbol: str,
        start: date,
        end: Optional[date] = None,
        interval: str = "1D",
        count_back: Optional[int] = None,
    ) -> List[dict]:
        """Trả về list bar thô: {"trade_date","open","high","low","close","volume"}."""

        end = end or date.today()
        end_ts = int(datetime(end.year, end.month, end.day, 23, 59, 59).timestamp())

        if count_back is None:
            count_back = max(1, (end - start).days) + 10

        payload = {
            "timeFrame": INTERVAL_MAP.get(interval, "ONE_DAY"),
            "symbols": [symbol],
            "to": end_ts,
            "countBack": count_back,
        }

        data = self._request("POST", OHLC_ENDPOINT, json=payload)
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        if not data:
            return []

        series = data[0]
        rows = []
        for t, o, h, l, c, v in zip(
            series.get("t", []), series.get("o", []), series.get("h", []),
            series.get("l", []), series.get("c", []), series.get("v", []),
        ):
            try:
                dt = datetime.fromtimestamp(int(t))
            except Exception:
                continue
            if dt.date() < start or dt.date() > end:
                continue
            try:
                volume = int(v)
            except (TypeError, ValueError):
                volume = 0
            rows.append({
                "trade_date": dt.date().isoformat(),
                "open": o, "high": h, "low": l, "close": c, "volume": volume,
            })
        return rows

    def get_price_board_raw(self, group: str = "HOSE") -> List[dict]:
        """Priceboard thô (đầy đủ field) - dùng cho lệnh `priceboard` (giữ hành vi cũ)."""
        data = self._request("POST", PRICEBOARD_ENDPOINT, json={"group": group})
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        ts = datetime.now(VN_TZ).isoformat(timespec="seconds")
        rows = []
        for item in data or []:
            row = {"snapshot_time": ts}
            row.update(item)
            rows.append(row)
        return rows

    def get_price_board_normalized(self, group: str = "HOSE") -> List[dict]:
        """Return one normalized end-of-day snapshot for a board."""
        raw_rows = self.get_price_board_raw(group)
        return [normalize_priceboard_row(row) for row in raw_rows]

    def get_price_board_by_symbol(self, group: str = "HOSE") -> Dict[str, dict]:
        """Priceboard đã map field, index theo symbol - dùng để enrich OHLCV hôm nay."""
        data = self._request("POST", PRICEBOARD_ENDPOINT, json={"group": group})
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        out = {}
        for item in data or []:
            sym = item.get("s")
            if not sym:
                continue
            out[sym] = {
                "total_value": _bn_to_vnd(item.get("va")),
                "ref_price": item.get("ref"),
                "ceiling": item.get("cei"),
                "floor": item.get("flo"),
                "foreign_buy_volume": item.get("frbv"),
                "foreign_sell_volume": item.get("frsv"),
                "put_through_volume": item.get("ptv"),
                "exchange": item.get("bo"),
                "security_type": item.get("st"),
            }
        return out

    def get_matched_trades(self, symbol: str, limit: int = 100) -> List[dict]:
        payload = {"symbol": symbol, "limit": limit, "truncTime": None}
        data = self._request("POST", INTRADAY_ENDPOINT, json=payload)
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        rows = []
        for item in data or []:
            rows.append({
                "symbol": symbol,
                "time": item.get("truncTime"),
                "price": item.get("matchPrice"),
                "volume": item.get("matchVol"),
                "match_type": item.get("matchType"),
            })
        return rows


def _bn_to_vnd(value_bn) -> Optional[float]:
    """total_value trong priceboard trả về đơn vị tỷ VND ('va') -> đổi ra VND."""
    if value_bn is None:
        return None
    try:
        return float(value_bn) * 1_000_000_000
    except (TypeError, ValueError):
        return None


def normalize_priceboard_row(row: dict) -> dict:
    """Map Vietcap's abbreviated priceboard payload to stable column names."""
    return {
        "snapshot_time": row.get("snapshot_time"),
        "isin": row.get("co"),
        "symbol": row.get("s"),
        "ceiling": _to_float(row.get("cei")),
        "floor": _to_float(row.get("flo")),
        "ref_price": _to_float(row.get("ref")),
        "match_price": _to_float(row.get("c")),
        "match_volume": int(row.get("mv") or 0),
        "open": _to_float(row.get("op")),
        "high": _to_float(row.get("h")),
        "low": _to_float(row.get("l")),
        "foreign_buy_volume": int(row.get("frbv") or 0),
        "foreign_sell_volume": int(row.get("frsv") or 0),
        "foreign_room": int(row.get("frcrr") or 0),
        "total_volume": int(row.get("vo") or 0),
        "total_value": _bn_to_vnd(row.get("va")),
        "put_through_volume": _to_float(row.get("ptv")),
        "put_through_value": _to_float(row.get("pta")),
        "security_type": row.get("st"),
        "exchange": row.get("bo"),
        "source": "vietcap",
    }


# ============================================================================
# SCHEMA MAPPING
# ============================================================================

def build_market_data_record(
    symbol: str,
    bar: dict,
    symbol_info: Optional[dict] = None,
    today_snapshot: Optional[dict] = None,
    timeframe: str = "1d",
) -> dict:
    """
    Ghép 1 bar OHLCV thô thành 1 record đúng schema Market Data.

    symbol_info: {"exchange":..., "security_type":...} (gần như tĩnh,
        từ cache priceboard, áp dụng cho mọi ngày của mã này).
    today_snapshot: nếu bar["trade_date"] == hôm nay và có truyền
        priceboard snapshot cho mã này, sẽ lấp đầy 6 field
        ref_price/ceiling/floor/foreign_*/put_through/total_value.
        Nếu None (mặc định với dữ liệu quá khứ), các field này = None.
    """

    symbol_info = symbol_info or {}
    now_vn = datetime.now(VN_TZ)
    is_today = bar["trade_date"] == now_vn.date().isoformat()
    snap = today_snapshot if (is_today and today_snapshot) else {}
    is_final = (not is_today) or now_vn.time() >= datetime.strptime("15:15", "%H:%M").time()
    open_ = _to_float(bar.get("open"))
    high = _to_float(bar.get("high"))
    low = _to_float(bar.get("low"))
    close = _to_float(bar.get("close"))
    volume = int(bar.get("volume") or 0)
    flags = []
    if None in (open_, high, low, close):
        flags.append("missing_ohlc")
    elif low > min(open_, close) or high < max(open_, close):
        flags.append("ohlc_bounds_source_anomaly")
        if volume == 0:
            flags.append("zero_volume")

    return {
        "symbol": symbol,
        "trade_date": bar["trade_date"],
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "adjusted_open": None,
        "adjusted_high": None,
        "adjusted_low": None,
        "adjusted_close": None,
        "volume": volume,
        "total_value": snap.get("total_value"),
        "ref_price": snap.get("ref_price"),
        "ceiling": snap.get("ceiling"),
        "floor": snap.get("floor"),
        "foreign_buy_volume": snap.get("foreign_buy_volume"),
        "foreign_sell_volume": snap.get("foreign_sell_volume"),
        "put_through_volume": snap.get("put_through_volume"),
        "exchange": snap.get("exchange") or symbol_info.get("exchange"),
        "security_type": snap.get("security_type") or symbol_info.get("security_type"),
        "timeframe": timeframe,
        "currency": "VND",
        "source": "vietcap",
        "is_final": is_final,
        "data_quality_flags": "|".join(flags),
        "fetched_at": now_vn.isoformat(timespec="seconds"),
    }


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_market_stock_symbols(cache_path: Path, exchanges: List[str]) -> List[str]:
    aliases = {"HOSE": "HSX", "HSX": "HSX", "HNX": "HNX", "UPCOM": "UPCOM"}
    wanted = {aliases.get(exchange.upper(), exchange.upper()) for exchange in exchanges}
    with cache_path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle)
        return sorted({
            row["symbol"].strip().upper()
            for row in rows
            if row.get("exchange", "").upper() in wanted
            and row.get("security_type", "").upper() == "STOCK"
        })


def save_eod_priceboard(
    out_dir: Path,
    groups: List[str] = None,
    min_interval: float = 0.4,
) -> int:
    """Save a normalized daily snapshot; reruns replace that day's snapshot."""
    groups = groups or ["HOSE", "HNX", "UPCOM"]
    client = VietcapMarketClient(min_interval_sec=min_interval)
    rows: List[dict] = []
    for group in groups:
        rows.extend(client.get_price_board_normalized(group))
    trade_day = datetime.now(VN_TZ).date().isoformat()
    write_csv(rows, out_dir / f"priceboard_{trade_day}.csv", mode="w")
    return len(rows)


# ============================================================================
# COMMAND: LIST SYMBOLS
# ============================================================================

def cmd_list_symbols(args):
    client = VietcapMarketClient()
    symbols = client.get_all_symbols()
    log.info("Tổng số mã lấy được: %d", len(symbols))
    write_csv(symbols, Path(args.out))
    log.info("Đã lưu danh sách mã vào %s", args.out)


# ============================================================================
# CORE: crawl + build schema cho 1 hoặc nhiều mã
# ============================================================================

def crawl_market_data(
    symbols: List[str],
    start: date,
    end: date,
    interval: str,
    min_interval: float,
    workers: int,
    out_dir: Path,
    enrich_today: bool,
    write_mode: str = "w",
    symbol_cache_path: Path | None = None,
) -> Dict[str, int]:
    """
    Crawl OHLCV cho danh sách symbols, map sang schema Market Data,
    ghi mỗi mã 1 file <out_dir>/<symbol>.csv.

    write_mode "w": ghi đè (dùng cho crawl full lịch sử lần đầu).
    write_mode "merge": gộp/dedup theo trade_date (dùng cho update
        tăng trưởng hàng ngày qua `auto` hoặc cron).
    """

    out_dir.mkdir(parents=True, exist_ok=True)
    symbol_info_cache = load_symbol_info_cache(
        cache_path=symbol_cache_path or DEFAULT_SYMBOL_INFO_CACHE
    )

    today_snapshot_by_symbol: Dict[str, dict] = {}
    if enrich_today and (end.isoformat() >= date.today().isoformat() >= start.isoformat()):
        enrich_client = VietcapMarketClient(min_interval_sec=min_interval)
        for group in ("HOSE", "HNX", "UPCOM"):
            try:
                today_snapshot_by_symbol.update(
                    enrich_client.get_price_board_by_symbol(group)
                )
            except Exception as e:
                log.error("Không lấy được priceboard để enrich hôm nay (%s): %s", group, e)

    workers = max(1, min(workers, 10))
    worker_local = threading.local()

    def crawl_one(sym: str):
        client = getattr(worker_local, "client", None)
        if client is None:
            client = VietcapMarketClient(min_interval_sec=min_interval)
            worker_local.client = client
        try:
            bars = client.get_ohlcv_bars(sym, start=start, end=end, interval=interval)
            if not bars:
                return sym, "empty", 0, ""

            records = [
                build_market_data_record(
                    sym, bar,
                    symbol_info=symbol_info_cache.get(sym),
                    today_snapshot=today_snapshot_by_symbol.get(sym),
                    timeframe=TIMEFRAME_MAP.get(interval, "1d"),
                )
                for bar in bars
            ]

            out_file = out_dir / f"{sym}.csv"
            if write_mode == "merge":
                n = merge_csv_dedup(records, out_file, key_field="trade_date")
            else:
                write_csv(records, out_file, mode="w")
                n = len(records)

            return sym, "success", n, ""
        except Exception as e:
            return sym, "failed", 0, str(e)

    success = empty = failed = 0
    aborted = False
    batch_size = max(10, workers * 5)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for offset in range(0, len(symbols), batch_size):
            batch = symbols[offset:offset + batch_size]
            futures = {executor.submit(crawl_one, s): s for s in batch}
            network_failures = 0
            for i, future in enumerate(as_completed(futures), offset + 1):
                sym = futures[future]
                try:
                    sym, status, n, err = future.result()
                except Exception as e:
                    sym, status, n, err = sym, "failed", 0, str(e)

                if status == "success":
                    success += 1
                    log.info("[%d/%d] %s: %d dòng.", i, len(symbols), sym, n)
                elif status == "empty":
                    empty += 1
                    log.warning("[%d/%d] %s: không có dữ liệu.", i, len(symbols), sym)
                else:
                    failed += 1
                    network_failures += "Request thất bại" in err or "timed out" in err.lower()
                    log.error("[%d/%d] %s: lỗi - %s", i, len(symbols), sym, err)
            if network_failures >= 3:
                aborted = True
                log.error("Dừng crawl sớm: %d/%d request trong một nhóm lỗi mạng.", network_failures, len(batch))
                break

    log.info("Hoàn tất: %d thành công, %d rỗng, %d lỗi; dừng sớm=%s.", success, empty, failed, aborted)
    return {"success": success, "empty": empty, "failed": failed,
            "aborted": aborted, "unprocessed": max(0, len(symbols) - success - empty - failed)}


# ============================================================================
# COMMAND: HISTORY
# ============================================================================

def cmd_history(args):
    if args.all:
        exchanges = [value.strip() for value in args.exchanges.split(",") if value.strip()]
        symbols = load_market_stock_symbols(Path(args.symbol_cache), exchanges)
        log.info("Sẽ crawl lịch sử cho %d cổ phiếu trên %s.", len(symbols), exchanges)
    else:
        symbols = [s.strip().upper() for s in (args.symbols or "").split(",") if s.strip()]

    if not symbols:
        log.error("Chưa cung cấp mã. Dùng --symbols VCB,VNM,FPT hoặc --all.")
        return

    if args.update:
        end = date.today()
        start = end - timedelta(days=args.update_lookback)
        write_mode = "merge"
    else:
        if not args.start:
            log.error("Thiếu --start (YYYY-MM-DD). Hoặc dùng --update.")
            return
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()
        write_mode = "w"

    crawl_market_data(
        symbols=symbols,
        start=start,
        end=end,
        interval=args.interval,
        min_interval=args.min_interval,
        workers=args.workers,
        out_dir=Path(args.out),
        enrich_today=not args.no_enrich_today,
        write_mode=write_mode,
    )


def cmd_index(args):
    """Crawl benchmark indices separately from the equity universe."""
    symbols = [value.strip().upper() for value in args.symbols.split(",") if value.strip()]
    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()
    crawl_market_data(
        symbols=symbols,
        start=start,
        end=end,
        interval="1D",
        min_interval=args.min_interval,
        workers=min(args.workers, len(symbols) or 1),
        out_dir=Path(args.out),
        enrich_today=False,
        write_mode="merge" if args.update else "w",
    )


# ============================================================================
# COMMAND: PRICEBOARD (giữ hành vi cũ - bảng giá thô toàn sàn)
# ============================================================================

def cmd_priceboard(args):
    client = VietcapMarketClient(min_interval_sec=args.min_interval)
    groups = [g.strip().upper() for g in args.groups.split(",") if g.strip()]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    def one_pass():
        for group in groups:
            try:
                rows = client.get_price_board_raw(group)
                if rows:
                    write_csv(
                        rows, out_dir / f"priceboard_{group}.csv",
                        mode="a" if args.append else "w",
                    )
                    log.info("%s: %d mã.", group, len(rows))
            except Exception as e:
                log.error("%s: lỗi - %s", group, e)

    if args.loop:
        try:
            while True:
                if args.trading_hours_only and not is_trading_hour():
                    time.sleep(max(args.interval, 30))
                    continue
                one_pass()
                time.sleep(args.interval)
        except KeyboardInterrupt:
            log.info("Dừng theo yêu cầu người dùng.")
    else:
        one_pass()


# ============================================================================
# COMMAND: REALTIME
# ============================================================================

def cmd_realtime(args):
    client = VietcapMarketClient(min_interval_sec=args.min_interval)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    seen_keys = set()

    try:
        while True:
            if args.trading_hours_only and not is_trading_hour():
                time.sleep(max(args.interval, 30))
                continue
            for sym in symbols:
                try:
                    trades = client.get_matched_trades(sym, limit=args.limit)
                except Exception as e:
                    log.error("%s: lỗi - %s", sym, e)
                    continue
                new_rows = []
                for t in trades:
                    key = (t["symbol"], t["time"], t["price"], t["volume"])
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    new_rows.append(t)
                if new_rows:
                    write_csv(new_rows, out_dir / f"{sym}_realtime.csv", mode="a")
                    log.info("%s: +%d lệnh khớp mới.", sym, len(new_rows))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log.info("Dừng theo yêu cầu người dùng.")


# ============================================================================
# COMMAND: AUTO
# ============================================================================

def cmd_auto(args):
    if not SCHEDULE_AVAILABLE:
        log.error("Chưa cài thư viện 'schedule'. Chạy: pip install schedule")
        return

    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols else None
    )

    def job_update_history():
        log.info("=== [AUTO] Bắt đầu cập nhật Market Data hàng ngày ===")
        job_symbols = symbols or [
            s["symbol"] for s in VietcapMarketClient().get_all_symbols()
        ]
        crawl_market_data(
            symbols=job_symbols,
            start=date.today() - timedelta(days=args.update_lookback),
            end=date.today(),
            interval="1D",
            min_interval=args.min_interval,
            workers=args.workers,
            out_dir=Path(args.history_out),
            enrich_today=True,
            write_mode="merge",
        )
        log.info("=== [AUTO] Kết thúc ===")

    jobs = 0
    if args.enable_history:
        schedule.every().day.at(args.history_time).do(job_update_history)
        log.info("Đã đăng ký job cập nhật Market Data lúc %s hàng ngày.", args.history_time)
        jobs += 1
        if args.run_history_now:
            job_update_history()

    if jobs == 0:
        log.error("Chưa bật job nào. Dùng --enable-history.")
        return

    log.info("Scheduler đang chạy. Nhấn Ctrl+C để dừng.")
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Dừng scheduler theo yêu cầu người dùng.")


# ============================================================================
# ARGPARSE
# ============================================================================

def build_parser():
    p = argparse.ArgumentParser(description="Crawl Market Data từ Vietcap")
    sub = p.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-symbols")
    p_list.add_argument("--out", default="data/symbols.csv")
    p_list.set_defaults(func=cmd_list_symbols)

    p_hist = sub.add_parser("history", help="Crawl OHLCV theo đúng schema Market Data")
    p_hist.add_argument("--symbols")
    p_hist.add_argument("--all", action="store_true")
    p_hist.add_argument("--exchanges", default="HSX,HNX,UPCOM")
    p_hist.add_argument("--symbol-cache", default="data/symbol_info_cache.csv")
    p_hist.add_argument("--start", default=None)
    p_hist.add_argument("--end", default=None)
    p_hist.add_argument("--interval", default="1D", choices=list(INTERVAL_MAP.keys()))
    p_hist.add_argument("--out", default="data/market_data")
    p_hist.add_argument("--min-interval", type=float, default=0.4)
    p_hist.add_argument("--workers", type=int, default=5)
    p_hist.add_argument("--update", action="store_true",
                         help="Cập nhật tăng trưởng N ngày gần nhất, gộp/dedup theo trade_date")
    p_hist.add_argument("--update-lookback", type=int, default=5)
    p_hist.add_argument("--no-enrich-today", action="store_true",
                         help="Không join priceboard để lấp field của ngày hôm nay")
    p_hist.set_defaults(func=cmd_history)

    p_index = sub.add_parser("index", help="Crawl VN-Index và benchmark khác")
    p_index.add_argument("--symbols", default="VNINDEX")
    p_index.add_argument("--start", default="2020-01-01")
    p_index.add_argument("--end", default=None)
    p_index.add_argument("--out", default="data/index_data")
    p_index.add_argument("--update", action="store_true")
    p_index.add_argument("--workers", type=int, default=1)
    p_index.add_argument("--min-interval", type=float, default=0.4)
    p_index.set_defaults(func=cmd_index)

    p_pb = sub.add_parser("priceboard", help="Bảng giá thô toàn sàn (không map schema)")
    p_pb.add_argument("--groups", default="HOSE,HNX,UPCOM")
    p_pb.add_argument("--out", default="data/priceboard")
    p_pb.add_argument("--min-interval", type=float, default=0.4)
    p_pb.add_argument("--loop", action="store_true")
    p_pb.add_argument("--interval", type=float, default=5.0)
    p_pb.add_argument("--append", action="store_true")
    p_pb.add_argument("--trading-hours-only", action="store_true")
    p_pb.set_defaults(func=cmd_priceboard)

    p_auto = sub.add_parser("auto")
    p_auto.add_argument("--symbols", default=None)
    p_auto.add_argument("--enable-history", action="store_true")
    p_auto.add_argument("--history-time", default="15:45")
    p_auto.add_argument("--run-history-now", action="store_true")
    p_auto.add_argument("--update-lookback", type=int, default=5)
    p_auto.add_argument("--history-out", default="data/market_data")
    p_auto.add_argument("--workers", type=int, default=5)
    p_auto.add_argument("--min-interval", type=float, default=0.4)
    p_auto.set_defaults(func=cmd_auto)

    p_rt = sub.add_parser("realtime")
    p_rt.add_argument("--symbols", required=True)
    p_rt.add_argument("--interval", type=float, default=5.0)
    p_rt.add_argument("--limit", type=int, default=100)
    p_rt.add_argument("--out", default="data/realtime")
    p_rt.add_argument("--min-interval", type=float, default=0.4)
    p_rt.add_argument("--trading-hours-only", action="store_true")
    p_rt.set_defaults(func=cmd_realtime)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
