import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from vietcap_common import merge_csv_dedup
from vietcap_fundamental_data import crawl_fundamental_data, resolve_metric_map
from vietcap_market_data import build_market_data_record, crawl_market_data, normalize_priceboard_row


class CsvMergeTests(unittest.TestCase):
    def test_composite_key_keeps_annual_and_quarterly_periods(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fundamental.csv"
            merge_csv_dedup(
                [
                    {"report_type": "yearly", "report_period": "2025", "revenue": 10},
                    {"report_type": "quarterly", "report_period": "2025Q4", "revenue": 3},
                ],
                path,
                key_fields=("report_type", "report_period"),
            )
            merge_csv_dedup(
                [{"report_type": "quarterly", "report_period": "2025Q4", "revenue": 4}],
                path,
                key_fields=("report_type", "report_period"),
            )
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            quarter = next(row for row in rows if row["report_type"] == "quarterly")
            self.assertEqual(quarter["revenue"], "4")


class FundamentalMappingTests(unittest.TestCase):
    def test_industry_specific_revenue_priority(self):
        metrics = {
            "INCOME_STATEMENT": [
                {"titleEn": "Net sales", "field": "isa3"},
                {"titleEn": "Total Operating Income", "field": "isb38"},
                {"titleEn": "Net profit/(loss) after tax", "field": "isa20"},
                {"titleEn": "EPS basic (VND)", "field": "isa23"},
            ],
            "CASH_FLOW": [
                {"titleEn": "Net cash from operating activities", "field": "cfa18"},
            ],
            "BALANCE_SHEET": [
                {"titleEn": "Short-term borrowings", "field": "bsa56"},
                {"titleEn": "Long-term borrowings", "field": "bsa71"},
            ],
        }
        mapping = resolve_metric_map(metrics)
        self.assertEqual(mapping["revenue"], ["isb38", "isa3"])
        self.assertEqual(mapping["operating_cash_flow"], ["cfa18"])
        self.assertEqual(mapping["short_debt"], ["bsa56"])


class PriceboardMappingTests(unittest.TestCase):
    def test_normalized_snapshot_schema(self):
        row = {
            "snapshot_time": "2026-09-21T16:00:00+07:00",
            "co": "VN000000AAA4",
            "s": "AAA",
            "c": 7260,
            "mv": 100,
            "vo": 1200,
            "va": 1.5,
            "st": "STOCK",
            "bo": "HSX",
        }
        result = normalize_priceboard_row(row)
        self.assertEqual(result["isin"], "VN000000AAA4")
        self.assertEqual(result["symbol"], "AAA")
        self.assertEqual(result["match_price"], 7260.0)
        self.assertEqual(result["total_value"], 1_500_000_000)
        self.assertEqual(result["exchange"], "HSX")

    def test_source_ohlc_anomaly_is_flagged_not_silently_changed(self):
        result = build_market_data_record(
            "AAA",
            {
                "trade_date": "2020-01-02",
                "open": 10,
                "high": 9,
                "low": 9,
                "close": 9,
                "volume": 0,
            },
        )
        self.assertEqual(result["open"], 10.0)
        self.assertIn("ohlc_bounds_source_anomaly", result["data_quality_flags"])
        self.assertIn("zero_volume", result["data_quality_flags"])


class MarketCrawlerSafetyTests(unittest.TestCase):
    def test_market_client_is_reused_per_worker(self):
        class EmptyClient:
            created = 0

            def __init__(self, **kwargs):
                self.__class__.created += 1

            def get_ohlcv_bars(self, *args, **kwargs):
                return []

        with tempfile.TemporaryDirectory() as tmp:
            with patch("vietcap_market_data.VietcapMarketClient", EmptyClient), patch(
                "vietcap_market_data.load_symbol_info_cache", return_value={}
            ):
                result = crawl_market_data(
                    symbols=[f"A{i:03d}" for i in range(20)],
                    start=date(2026, 9, 14), end=date(2026, 9, 21),
                    interval="1D", min_interval=0, workers=2,
                    out_dir=Path(tmp), enrich_today=False,
                    symbol_cache_path=Path(tmp) / "cache.csv",
                )
        self.assertEqual(result["empty"], 20)
        self.assertLessEqual(EmptyClient.created, 2)

    def test_repeated_network_failures_stop_before_whole_market(self):
        class TimeoutClient:
            def __init__(self, **kwargs):
                pass

            def get_ohlcv_bars(self, *args, **kwargs):
                raise RuntimeError("Request thất bại sau 2 lần thử")

        with tempfile.TemporaryDirectory() as tmp:
            with patch("vietcap_market_data.VietcapMarketClient", TimeoutClient), patch(
                "vietcap_market_data.load_symbol_info_cache", return_value={}
            ):
                result = crawl_market_data(
                    symbols=[f"A{i:03d}" for i in range(50)],
                    start=date(2026, 9, 14), end=date(2026, 9, 21),
                    interval="1D", min_interval=0, workers=2,
                    out_dir=Path(tmp), enrich_today=False,
                    symbol_cache_path=Path(tmp) / "cache.csv",
                )
        self.assertTrue(result["aborted"])
        self.assertGreaterEqual(result["unprocessed"], 40)


class FundamentalCrawlerSafetyTests(unittest.TestCase):
    def test_fundamental_client_is_reused_per_worker(self):
        class EmptyClient:
            created = 0

            def __init__(self, min_interval):
                self.__class__.created += 1

            def get_fundamentals(self, symbol):
                return []

        with tempfile.TemporaryDirectory() as tmp:
            with patch("vietcap_fundamental_data.VietcapFundamentalClient", EmptyClient):
                result = crawl_fundamental_data(
                    symbols=[f"A{i:03d}" for i in range(20)],
                    out_dir=Path(tmp), min_interval=0, workers=2,
                )
        self.assertEqual(result["empty"], 20)
        self.assertLessEqual(EmptyClient.created, 2)


if __name__ == "__main__":
    unittest.main()
