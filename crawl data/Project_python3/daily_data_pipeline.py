"""Bootstrap and end-of-day data pipeline for the Telegram stock project."""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from vietcap_common import load_symbol_info_cache, write_csv
from data_quality import build_quality_report
from vietcap_fundamental_data import crawl_fundamental_data, load_stock_symbols
from vietcap_market_data import (
    VietcapMarketClient,
    crawl_market_data,
    load_market_stock_symbols,
    save_eod_priceboard,
)


PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
VNINDEX_SYMBOL = "VNINDEX"


def _write_report(name: str, payload: dict) -> Path:
    out_dir = DATA_DIR / "quality"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}_{date.today().isoformat()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def refresh_universe() -> dict:
    cache_path = DATA_DIR / "symbol_info_cache.csv"
    info = load_symbol_info_cache(cache_path=cache_path, refresh=True)
    return info


def save_latest_and_daily_priceboard(min_interval: float) -> int:
    latest_dir = DATA_DIR / "priceboard"
    latest_dir.mkdir(parents=True, exist_ok=True)
    client = VietcapMarketClient(min_interval_sec=min_interval)
    for group in ("HOSE", "HNX", "UPCOM"):
        write_csv(
            client.get_price_board_raw(group),
            latest_dir / f"priceboard_{group}.csv",
            mode="w",
        )
    return save_eod_priceboard(DATA_DIR / "priceboard_daily", min_interval=min_interval)


def run_pipeline(
    mode: str,
    start_date: date,
    lookback_days: int,
    workers: int,
    min_interval: float,
    include_fundamental: bool,
) -> dict:
    started = datetime.now()
    refresh_universe()
    cache_path = DATA_DIR / "symbol_info_cache.csv"
    market_symbols = load_market_stock_symbols(cache_path, ["HSX", "HNX", "UPCOM"])
    fundamental_symbols = load_stock_symbols(cache_path, ["HSX", "HNX"])
    snapshot_rows = save_latest_and_daily_priceboard(min_interval)

    end_date = date.today()
    market_start = start_date if mode == "bootstrap" else end_date - timedelta(days=lookback_days)
    market_summary = crawl_market_data(
        symbols=market_symbols,
        start=market_start,
        end=end_date,
        interval="1D",
        min_interval=min_interval,
        workers=workers,
        out_dir=DATA_DIR / "market_data",
        enrich_today=True,
        write_mode="w" if mode == "bootstrap" else "merge",
        symbol_cache_path=cache_path,
    )
    if market_summary.get("aborted"):
        raise RuntimeError(
            f"Dừng cập nhật Vietcap vì lỗi mạng diện rộng; "
            f"đã bỏ qua {market_summary['unprocessed']} mã còn lại"
        )
    index_summary = crawl_market_data(
        symbols=[VNINDEX_SYMBOL],
        start=market_start,
        end=end_date,
        interval="1D",
        min_interval=min_interval,
        workers=1,
        out_dir=DATA_DIR / "index_data",
        enrich_today=False,
        write_mode="w" if mode == "bootstrap" else "merge",
        symbol_cache_path=cache_path,
    )

    fundamental_summary = None
    if include_fundamental:
        fundamental_summary = crawl_fundamental_data(
            fundamental_symbols,
            DATA_DIR / "fundamental",
            min_interval=min_interval,
            workers=workers,
            failure_path=DATA_DIR / "quality" / "fundamental_failures.csv",
        )

    quality = build_quality_report(DATA_DIR)

    report = {
        "mode": mode,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "market_universe": len(market_symbols),
        "fundamental_universe": len(fundamental_symbols),
        "priceboard_rows": snapshot_rows,
        "market": market_summary,
        "index": index_summary,
        "fundamental": fundamental_summary,
        "quality": quality,
    }
    report_path = _write_report(mode, report)
    report["report_path"] = str(report_path)
    return report


def run_scheduler(args) -> None:
    last_run = None
    while True:
        now = datetime.now()
        due = now.strftime("%H:%M") >= args.time and now.weekday() < 5
        if due and last_run != now.date():
            run_pipeline(
                mode="update",
                start_date=date.fromisoformat(args.start),
                lookback_days=args.lookback_days,
                workers=args.workers,
                min_interval=args.min_interval,
                include_fundamental=not args.skip_fundamental,
            )
            last_run = now.date()
        time.sleep(30)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pipeline dữ liệu Vietcap cuối ngày")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("bootstrap", "update", "schedule"):
        command = sub.add_parser(name)
        command.add_argument("--start", default="2020-01-01")
        command.add_argument("--lookback-days", type=int, default=7)
        command.add_argument("--workers", type=int, default=4)
        command.add_argument("--min-interval", type=float, default=0.2)
        command.add_argument("--skip-fundamental", action="store_true")
        if name == "schedule":
            command.add_argument("--time", default="16:00")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "schedule":
        run_scheduler(args)
        return 0
    report = run_pipeline(
        mode=args.command,
        start_date=date.fromisoformat(args.start),
        lookback_days=args.lookback_days,
        workers=args.workers,
        min_interval=args.min_interval,
        include_fundamental=not args.skip_fundamental,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    failed = report["market"]["failed"] + report["index"]["failed"]
    if report["fundamental"]:
        failed += report["fundamental"]["failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
