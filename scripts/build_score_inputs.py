"""Build verified F/M inputs without inventing sector or index observations.

Examples:
  python scripts/build_score_inputs.py industry
  python scripts/build_score_inputs.py hnx-index --start 2026-07-01 --end 2026-09-21

The HNX host currently has an incomplete TLS chain on some machines. Only if
that is the observed failure, pass --allow-insecure-hnx; PDF date and OHLC
checks remain mandatory. The generated cleaned CSV is imported by MarketStore.
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import ssl
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CRAWLER = ROOT / "crawl data" / "Project_python3"
sys.path.insert(0, str(ROOT))
INDUSTRY_URL = "https://iq.vietcap.com.vn/api/iq-insight-service/v2/company/search-bar?language=1"
HNX_URL = "https://owa.hnx.vn/ftp/THONGKEGIAODICH/{day}/INDEX/{day}_ID_Thong_ke_thong_tin_chi_so.pdf"
NUMBER = r"[\d.,]+"
HNX_ROW = re.compile(rf"(?m)^\s*\d+\s+HNX Index\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+({NUMBER})\s+")


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def industry_map() -> None:
    sys.path.insert(0, str(CRAWLER))
    from vietcap_market_data import VietcapMarketClient

    from app.market_store import MarketStore

    store = MarketStore(ROOT / "data" / "fintech_bot.db")
    universe = {symbol for symbol, exchange in store.universe().items() if exchange in {"HSX", "HNX"}}
    if not universe:
        raise RuntimeError("SQLite chưa có danh sách cổ phiếu HSX/HNX")
    response = VietcapMarketClient()._request("GET", INDUSTRY_URL)
    items = response.get("data", []) if isinstance(response, dict) else []
    if not isinstance(items, list):
        raise RuntimeError("Vietcap không trả danh sách doanh nghiệp")
    sectors: dict[str, str] = {}
    duplicates: set[str] = set()
    for item in items:
        symbol = str(item.get("code") or "").strip().upper()
        if symbol not in universe:
            continue
        code = str((item.get("icbLv2") or {}).get("code") or "").strip()
        if not code:
            continue
        group = f"ICB2:{code}"
        if symbol in sectors:
            duplicates.add(symbol)
            if sectors[symbol] != group:
                raise RuntimeError(f"Vietcap trả nhóm ngành mâu thuẫn cho {symbol}")
        sectors[symbol] = group
    missing = universe - sectors.keys()
    if missing:
        raise RuntimeError(f"Thiếu phân ngành cho {len(missing)} mã: {', '.join(sorted(missing)[:20])}")
    rows = [{"symbol": symbol, "sector_group": sectors[symbol]} for symbol in sorted(sectors)]
    _write_csv(ROOT / "industry_map.csv", ["symbol", "sector_group"], rows)
    print(f"industry_map.csv: {len(rows)} HSX/HNX symbols; {len(duplicates)} duplicate source rows; URL: {INDUSTRY_URL}")
    print(f"retrieved_at_utc: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")


def _vn_number(value: str) -> float:
    return float(value.replace(".", "").replace(",", "."))


def parse_hnx_report(text: str, day: date) -> dict:
    if day.strftime("%d/%m/%Y") not in text:
        raise ValueError(f"PDF không xác nhận ngày {day}")
    matches = HNX_ROW.findall(text)
    if len(matches) != 1:
        raise ValueError(f"PDF {day}: cần đúng một dòng HNX Index, thấy {len(matches)}")
    op, high, low, close = map(_vn_number, matches[0])
    if not (low > 0 and low <= min(op, close) and high >= max(op, close)):
        raise ValueError(f"PDF {day}: OHLC không hợp lệ")
    day_key = day.strftime("%Y%m%d")
    return {
        "ticker": "HNXINDEX", "trade_date": day.isoformat(), "open": op,
        "high": high, "low": low, "close": close, "is_final": "True",
        "data_quality_flags": "", "source": "HNX", "source_url": HNX_URL.format(day=day_key),
    }


def _fetch_hnx_day(day: date, allow_insecure: bool) -> dict | None:
    from pypdf import PdfReader

    url = HNX_URL.format(day=day.strftime("%Y%m%d"))
    context = ssl._create_unverified_context() if allow_insecure else ssl.create_default_context()
    try:
        with urllib.request.urlopen(url, timeout=30, context=context) as response:
            content = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 404}:
            # HNX returns 403 for some dates with no downloadable report.
            # Missing dates are reported below; they are never synthesized.
            return None
        raise
    if not content.startswith(b"%PDF"):
        raise ValueError(f"HNX {day}: phản hồi không phải PDF")
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(content)).pages)
    return parse_hnx_report(text, day)


def hnx_history(start: date, end: date, allow_insecure: bool) -> None:
    from app.market_store import MarketStore

    if end < start or end > date.today() or (end - start).days > 730:
        raise ValueError("Khoảng ngày HNX phải hợp lệ, không vượt quá hôm nay hoặc 730 ngày")
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)
            if (start + timedelta(days=offset)).weekday() < 5]
    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = list(pool.map(lambda day: _fetch_hnx_day(day, allow_insecure), days))
    rows = sorted((row for row in fetched if row is not None), key=lambda row: row["trade_date"])
    missing = [day.isoformat() for day, row in zip(days, fetched) if row is None]
    if len(rows) < 21 or rows[-1]["trade_date"] != end.isoformat():
        raise RuntimeError(f"HNXINDEX chỉ có {len(rows)} phiên, phiên cuối {rows[-1]['trade_date'] if rows else 'none'}; không ghi CSV")
    path = ROOT / "cleaned data" / "cleaned_v2" / "index_data" / "cleaned_HNXINDEX.csv"
    store = MarketStore(ROOT / "data" / "fintech_bot.db")
    vn_sessions = set(store.index("VNINDEX", limit=600).get("trade_date", []))
    observed = {row["trade_date"] for row in rows}
    missing_trading_dates = sorted(day for day in vn_sessions - observed
                                   if start.isoformat() <= day <= end.isoformat())
    _write_csv(path, list(rows[0]), rows)
    print(f"{path}: {len(rows)} verified sessions, {rows[0]['trade_date']}..{rows[-1]['trade_date']}; HNX PDF source")
    print(f"unavailable_report_dates: {','.join(missing) if missing else 'none'}")
    print(f"missing_against_vnindex_calendar: {','.join(missing_trading_dates) if missing_trading_dates else 'none'}")
    print(f"retrieved_at_utc: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("industry")
    hnx = sub.add_parser("hnx-index")
    hnx.add_argument("--start", type=date.fromisoformat, required=True)
    hnx.add_argument("--end", type=date.fromisoformat, required=True)
    hnx.add_argument("--allow-insecure-hnx", action="store_true")
    args = parser.parse_args()
    if args.command == "industry":
        industry_map()
    else:
        hnx_history(args.start, args.end, args.allow_insecure_hnx)


if __name__ == "__main__":
    main()
