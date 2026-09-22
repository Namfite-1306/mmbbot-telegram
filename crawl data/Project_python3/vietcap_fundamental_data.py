"""Crawl and normalize fundamental data from Vietcap IQ.

The crawler joins balance sheet, income statement, cash flow and financial
ratios by reporting period. It keeps announcement dates so downstream model
code can perform point-in-time joins without look-ahead bias.

Default universe: listed STOCK instruments on HSX and HNX only.
"""

from __future__ import annotations

import argparse
import csv
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from vietcap_common import BaseVietcapClient, get_logger, merge_csv_dedup, write_csv


log = get_logger("vietcap_fundamental_data")
VN_TZ = timezone(timedelta(hours=7))

IQ_ROOT = "https://iq.vietcap.com.vn/api/iq-insight-service/v1"
FINANCIAL_STATEMENT_ENDPOINT = IQ_ROOT + "/company/{symbol}/financial-statement"
FINANCIAL_METRICS_ENDPOINT = IQ_ROOT + "/company/{symbol}/financial-statement/metrics"
FINANCIAL_RATIOS_ENDPOINT = IQ_ROOT + "/company/{symbol}/statistics-financial"

SECTION_BALANCE_SHEET = "BALANCE_SHEET"
SECTION_INCOME_STATEMENT = "INCOME_STATEMENT"
SECTION_CASH_FLOW = "CASH_FLOW"

IQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": "https://iq.vietcap.com.vn/",
    "Origin": "https://iq.vietcap.com.vn",
}

BALANCE_SHEET_MAP = {
    "bsa1": "current_assets",
    "bsa23": "non_current_assets",
    "bsa53": "total_assets",
    "bsa54": "total_liabilities",
    "bsa78": "total_equity",
    "bsa79": "equity_attributable_to_parent",
    "bsa96": "total_capital_source",
}

REQUIRED_MODEL_FIELDS = (
    "revenue",
    "net_profit",
    "total_equity",
    "total_debt",
    "operating_cash_flow",
    "eps",
    "pe",
    "pb",
)

PeriodKey = Tuple[str, int, int]


def _date_part(value) -> Optional[str]:
    text = str(value or "").strip()
    return text[:10] or None


def _latest_text(*values) -> Optional[str]:
    valid = [str(value) for value in values if value not in (None, "")]
    return max(valid) if valid else None


def _number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_present(*values):
    available = [number for number in (_number(value) for value in values) if number is not None]
    return sum(available) if available else None


def _period_key(item: dict, report_type: str) -> Optional[PeriodKey]:
    try:
        year = int(item.get("yearReport"))
        length = int(item.get("lengthReport") or (5 if report_type == "yearly" else 0))
    except (TypeError, ValueError):
        return None
    return report_type, year, length


def _index_statement(data: dict) -> Dict[PeriodKey, dict]:
    indexed: Dict[PeriodKey, dict] = {}
    for report_type, bucket in (("yearly", "years"), ("quarterly", "quarters")):
        for item in (data or {}).get(bucket) or []:
            key = _period_key(item, report_type)
            if key:
                indexed[key] = item
    return indexed


def _metric_fields(metrics: dict, section: str, preferred_titles: Sequence[str]) -> List[str]:
    """Resolve fields by Vietcap's own English labels, in priority order."""
    by_title: Dict[str, List[str]] = {}
    for row in (metrics or {}).get(section) or []:
        field = row.get("field")
        title = str(row.get("titleEn") or "").strip().casefold()
        if field and title:
            by_title.setdefault(title, []).append(field)

    resolved: List[str] = []
    for title in preferred_titles:
        for field in by_title.get(title.casefold(), []):
            if field not in resolved:
                resolved.append(field)
    return resolved


def _first_value(item: dict, fields: Iterable[str]):
    for field in fields:
        value = item.get(field)
        if value not in (None, ""):
            return value, field
    return None, None


def resolve_metric_map(metrics: dict) -> dict:
    """Create an industry-aware field map from the metric dictionary."""
    revenue = _metric_fields(
        metrics,
        SECTION_INCOME_STATEMENT,
        ("Total Operating Income", "Operating Sales", "Net sales"),
    )
    net_profit = _metric_fields(
        metrics,
        SECTION_INCOME_STATEMENT,
        ("Net profit/(loss) after tax", "Net profit after tax"),
    )
    eps = _metric_fields(
        metrics,
        SECTION_INCOME_STATEMENT,
        ("EPS basic (VND)", "Basic EPS (VND)"),
    )
    cfo = _metric_fields(
        metrics,
        SECTION_CASH_FLOW,
        (
            "Net cash from operating activities",
            "Net cash inflows/(outflows) from operating activities",
        ),
    )
    short_debt = _metric_fields(
        metrics,
        SECTION_BALANCE_SHEET,
        ("Short-term borrowings", "Short-term loans and liabilities"),
    )
    long_debt = _metric_fields(
        metrics,
        SECTION_BALANCE_SHEET,
        ("Long-term borrowings", "Long-term liabilities"),
    )
    return {
        "revenue": revenue or ["isb38", "isa1", "isa3"],
        "net_profit": net_profit or ["isa20"],
        "eps": eps or ["isa23"],
        "operating_cash_flow": cfo or ["cfa18"],
        "short_debt": short_debt,
        "long_debt": long_debt,
    }


class VietcapFundamentalClient(BaseVietcapClient):
    def __init__(self, min_interval_sec: float = 0.2):
        super().__init__(min_interval_sec=min_interval_sec, headers=IQ_HEADERS)

    def get_statement(self, symbol: str, section: str) -> dict:
        url = FINANCIAL_STATEMENT_ENDPOINT.format(symbol=symbol.upper())
        raw = self._request("GET", url, params={"section": section})
        return (raw or {}).get("data") or {}

    def get_metrics(self, symbol: str) -> dict:
        raw = self._request("GET", FINANCIAL_METRICS_ENDPOINT.format(symbol=symbol.upper()))
        return (raw or {}).get("data") or {}

    def get_ratios(self, symbol: str) -> List[dict]:
        raw = self._request("GET", FINANCIAL_RATIOS_ENDPOINT.format(symbol=symbol.upper()))
        return (raw or {}).get("data") or []

    def get_fundamentals(self, symbol: str) -> List[dict]:
        symbol = symbol.upper()
        balance = _index_statement(self.get_statement(symbol, SECTION_BALANCE_SHEET))
        income = _index_statement(self.get_statement(symbol, SECTION_INCOME_STATEMENT))
        cash_flow = _index_statement(self.get_statement(symbol, SECTION_CASH_FLOW))
        field_map = resolve_metric_map(self.get_metrics(symbol))

        ratio_index: Dict[PeriodKey, dict] = {}
        for ratio in self.get_ratios(symbol):
            try:
                year = int(ratio.get("yearReport") or ratio.get("year"))
                quarter = int(ratio.get("quarter"))
            except (TypeError, ValueError):
                continue
            report_type = "yearly" if quarter == 5 else "quarterly"
            ratio_index[(report_type, year, quarter)] = ratio

        keys = sorted(set(balance) | set(income) | set(cash_flow) | set(ratio_index))
        fetched_at = datetime.now(VN_TZ).isoformat(timespec="seconds")
        records: List[dict] = []

        for report_type, year, length in keys:
            key = report_type, year, length
            bs = balance.get(key, {})
            inc = income.get(key, {})
            cf = cash_flow.get(key, {})
            ratio = ratio_index.get(key, {})

            revenue, revenue_field = _first_value(inc, field_map["revenue"])
            net_profit, npat_field = _first_value(inc, field_map["net_profit"])
            eps, eps_field = _first_value(inc, field_map["eps"])
            cfo, cfo_field = _first_value(cf, field_map["operating_cash_flow"])
            short_debt, short_debt_field = _first_value(bs, field_map["short_debt"])
            long_debt, long_debt_field = _first_value(bs, field_map["long_debt"])

            total_debt = _sum_present(short_debt, long_debt)
            debt_fields = [field for field in (short_debt_field, long_debt_field) if field]
            if total_debt is None and bs.get("bsa54") not in (None, ""):
                total_debt = bs.get("bsa54")
                debt_definition = "total_liabilities_bank"
                debt_fields = ["bsa54"]
            else:
                debt_definition = "short_plus_long_borrowings"

            announcement_date = _latest_text(
                _date_part(bs.get("publicDate")),
                _date_part(inc.get("publicDate")),
                _date_part(cf.get("publicDate")),
            )
            record = {
                "symbol": symbol,
                "report_period": str(year) if report_type == "yearly" else f"{year}Q{length}",
                "report_type": report_type,
                "year_report": year,
                "length_report": length,
                "announcement_date": announcement_date,
                "publish_date": announcement_date,
                "update_date": _latest_text(
                    bs.get("updateDate"), inc.get("updateDate"), cf.get("updateDate")
                ),
            }
            for raw_key, field_name in BALANCE_SHEET_MAP.items():
                record[field_name] = bs.get(raw_key)
            record.update(
                {
                    "revenue": revenue,
                    "net_profit": net_profit,
                    "total_debt": total_debt,
                    "operating_cash_flow": cfo,
                    "eps": eps,
                    "pe": ratio.get("pe"),
                    "pb": ratio.get("pb"),
                    "roe": ratio.get("roe"),
                    "roa": ratio.get("roa"),
                    "debt_to_equity": ratio.get("debtToEquity"),
                    "market_cap": ratio.get("marketCap"),
                    "shares_outstanding": ratio.get("numberOfSharesMktCap"),
                    "ratio_type": ratio.get("ratioType"),
                    "revenue_source_field": revenue_field,
                    "net_profit_source_field": npat_field,
                    "eps_source_field": eps_field,
                    "cfo_source_field": cfo_field,
                    "debt_source_fields": "+".join(debt_fields) or None,
                    "debt_definition": debt_definition,
                    "currency": "VND",
                    "source": "vietcap_iq",
                    "fetched_at": fetched_at,
                }
            )
            missing = [field for field in REQUIRED_MODEL_FIELDS if record.get(field) in (None, "")]
            record["data_quality_flags"] = "missing:" + "|".join(missing) if missing else ""
            records.append(record)
        return records


def load_stock_symbols(cache_path: Path, exchanges: Sequence[str]) -> List[str]:
    aliases = {"HOSE": "HSX", "HSX": "HSX", "HNX": "HNX", "UPCOM": "UPCOM"}
    wanted = {aliases.get(exchange.upper(), exchange.upper()) for exchange in exchanges}
    with cache_path.open("r", newline="", encoding="utf-8-sig") as handle:
        rows = csv.DictReader(handle)
        symbols = {
            row["symbol"].strip().upper()
            for row in rows
            if row.get("exchange", "").upper() in wanted
            and row.get("security_type", "").upper() == "STOCK"
        }
    return sorted(symbols)


def crawl_fundamental_data(
    symbols: Sequence[str],
    out_dir: Path,
    min_interval: float = 0.2,
    workers: int = 4,
    failure_path: Optional[Path] = None,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    workers = max(1, min(int(workers), 8))
    worker_local = threading.local()

    def crawl_one(symbol: str):
        try:
            client = getattr(worker_local, "client", None)
            if client is None:
                client = VietcapFundamentalClient(min_interval)
                worker_local.client = client
            records = client.get_fundamentals(symbol)
            if not records:
                return symbol, "empty", 0, ""
            merge_csv_dedup(
                records,
                out_dir / f"{symbol}_fundamental.csv",
                key_fields=("report_type", "report_period"),
            )
            return symbol, "success", len(records), ""
        except Exception as exc:
            return symbol, "failed", 0, str(exc)

    summary = {"requested": len(symbols), "success": 0, "empty": 0, "failed": 0}
    failures: List[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(crawl_one, symbol): symbol for symbol in symbols}
        for index, future in enumerate(as_completed(futures), 1):
            symbol, status, count, error = future.result()
            summary[status] += 1
            if status == "success":
                log.info("[%d/%d] %s: %d kỳ.", index, len(symbols), symbol, count)
            else:
                log.warning("[%d/%d] %s: %s %s", index, len(symbols), symbol, status, error)
                failures.append({"symbol": symbol, "status": status, "error": error})

    if failure_path:
        if failures:
            write_csv(failures, failure_path, mode="w")
        elif failure_path.exists():
            failure_path.unlink()
    log.info("Fundamental hoàn tất: %s", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Crawl Fundamental Data từ Vietcap IQ")
    parser.add_argument("--symbols", help="Danh sách mã, ví dụ FPT,VCB,SSI")
    parser.add_argument("--all", action="store_true", help="Toàn bộ cổ phiếu theo sàn")
    parser.add_argument("--exchanges", default="HSX,HNX", help="Mặc định HSX,HNX")
    parser.add_argument("--symbol-cache", default="data/symbol_info_cache.csv")
    parser.add_argument("--out", default="data/fundamental")
    parser.add_argument("--failure-out", default="data/quality/fundamental_failures.csv")
    parser.add_argument("--min-interval", type=float, default=0.2)
    parser.add_argument("--workers", type=int, default=4)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.all:
        exchanges = [value.strip() for value in args.exchanges.split(",") if value.strip()]
        symbols = load_stock_symbols(Path(args.symbol_cache), exchanges)
    else:
        symbols = [value.strip().upper() for value in (args.symbols or "").split(",") if value.strip()]
    if not symbols:
        log.error("Không có mã để crawl. Dùng --symbols hoặc --all.")
        return 2
    summary = crawl_fundamental_data(
        symbols,
        Path(args.out),
        min_interval=args.min_interval,
        workers=args.workers,
        failure_path=Path(args.failure_out),
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
