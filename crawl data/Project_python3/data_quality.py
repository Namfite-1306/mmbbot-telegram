"""Read-only quality checks for the crawled Vietcap dataset."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Iterable, List

from vietcap_common import write_csv
from vietcap_fundamental_data import REQUIRED_MODEL_FIELDS


def _rows(path: Path) -> List[dict]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_missing(value) -> bool:
    return value in (None, "")


def build_quality_report(data_dir: Path) -> dict:
    quality_dir = data_dir / "quality"
    quality_dir.mkdir(parents=True, exist_ok=True)
    cache = _rows(data_dir / "symbol_info_cache.csv")
    stocks = {row["symbol"]: row["exchange"] for row in cache if row["security_type"] == "STOCK"}
    fundamental_expected = {symbol for symbol, exchange in stocks.items() if exchange in {"HSX", "HNX"}}

    market_issues: List[dict] = []
    market_files = {path.stem: path for path in (data_dir / "market_data").glob("*.csv")}
    market_rows = 0
    for symbol in sorted(stocks):
        path = market_files.get(symbol)
        if not path:
            market_issues.append({"symbol": symbol, "issue": "missing_file", "detail": ""})
            continue
        rows = _rows(path)
        market_rows += len(rows)
        dates = [row.get("trade_date") for row in rows]
        if len(dates) != len(set(dates)):
            market_issues.append({"symbol": symbol, "issue": "duplicate_trade_date", "detail": ""})
        if len(rows) < 250:
            market_issues.append({"symbol": symbol, "issue": "less_than_250_bars", "detail": str(len(rows))})
        invalid_positive_volume = 0
        invalid_zero_volume = 0
        for row in rows:
            open_, high, low, close = map(_float, (row.get("open"), row.get("high"), row.get("low"), row.get("close")))
            if None in (open_, high, low, close):
                invalid_positive_volume += 1
            elif low > min(open_, close) or high < max(open_, close):
                if int(float(row.get("volume") or 0)) > 0:
                    invalid_positive_volume += 1
                else:
                    invalid_zero_volume += 1
        if invalid_positive_volume:
            market_issues.append(
                {"symbol": symbol, "issue": "ohlc_bounds_positive_volume", "detail": str(invalid_positive_volume)}
            )
        if invalid_zero_volume:
            market_issues.append(
                {"symbol": symbol, "issue": "ohlc_bounds_zero_volume", "detail": str(invalid_zero_volume)}
            )

    fundamental_issues: List[dict] = []
    fundamental_missing = Counter()
    fundamental_latest_missing = Counter()
    fundamental_rows = 0
    fundamental_files = {
        path.name.removesuffix("_fundamental.csv"): path
        for path in (data_dir / "fundamental").glob("*_fundamental.csv")
    }
    for symbol in sorted(fundamental_expected):
        path = fundamental_files.get(symbol)
        if not path:
            fundamental_issues.append({"symbol": symbol, "issue": "missing_file", "detail": ""})
            continue
        rows = _rows(path)
        fundamental_rows += len(rows)
        keys = [(row.get("report_type"), row.get("report_period")) for row in rows]
        if len(keys) != len(set(keys)):
            fundamental_issues.append({"symbol": symbol, "issue": "duplicate_period", "detail": ""})
        for row in rows:
            for field in REQUIRED_MODEL_FIELDS:
                if _is_missing(row.get(field)):
                    fundamental_missing[field] += 1
        latest = max(
            rows,
            key=lambda row: (int(row.get("year_report") or 0), int(row.get("length_report") or 0)),
        )
        missing_latest = [field for field in REQUIRED_MODEL_FIELDS if _is_missing(latest.get(field))]
        for field in missing_latest:
            fundamental_latest_missing[field] += 1
        if missing_latest:
            fundamental_issues.append(
                {
                    "symbol": symbol,
                    "issue": "latest_period_missing",
                    "detail": "|".join(missing_latest),
                }
            )

    index_path = data_dir / "index_data" / "VNINDEX.csv"
    index_rows = len(_rows(index_path)) if index_path.exists() else 0
    snapshot_files = sorted((data_dir / "priceboard_daily").glob("*.csv"))
    snapshot_rows = len(_rows(snapshot_files[-1])) if snapshot_files else 0

    report = {
        "as_of_date": date.today().isoformat(),
        "universe": {
            "stocks_total": len(stocks),
            "by_exchange": dict(Counter(stocks.values())),
            "fundamental_expected_hsx_hnx": len(fundamental_expected),
        },
        "market": {
            "files": len(market_files),
            "rows": market_rows,
            "missing_symbols": sorted(set(stocks) - set(market_files)),
            "issues": len(market_issues),
        },
        "index": {"symbol": "VNINDEX", "rows": index_rows},
        "priceboard": {"latest_daily_rows": snapshot_rows},
        "fundamental": {
            "files": len(fundamental_files),
            "rows": fundamental_rows,
            "missing_symbols": sorted(fundamental_expected - set(fundamental_files)),
            "missing_cells_by_field": dict(fundamental_missing),
            "latest_period_missing_by_field": dict(fundamental_latest_missing),
            "issues": len(fundamental_issues),
        },
    }
    if market_issues:
        write_csv(market_issues, quality_dir / "market_issues.csv", mode="w")
    if fundamental_issues:
        write_csv(fundamental_issues, quality_dir / "fundamental_issues.csv", mode="w")
    (quality_dir / f"data_quality_{date.today().isoformat()}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    base = Path(__file__).resolve().parent / "data"
    print(json.dumps(build_quality_report(base), ensure_ascii=False, indent=2))
