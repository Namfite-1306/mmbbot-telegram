from __future__ import annotations

from datetime import date

import pytest

from app.market_store import MarketStore
from app.strategy_engine import StrategyDataError, _read_sector_map
from scripts.build_score_inputs import parse_hnx_report


def test_hnx_pdf_row_is_date_checked_and_ohlc_validated() -> None:
    text = "Thống kê thông tin chỉ số 21/09/2026\n3 HNX Index 275,29 277,17 272,61 274,38 -0,91 -0,33 35.192.488"
    row = parse_hnx_report(text, date(2026, 9, 21))
    assert (row["ticker"], row["trade_date"], row["close"], row["is_final"]) == (
        "HNXINDEX", "2026-09-21", 274.38, "True")
    with pytest.raises(ValueError, match="ngày"):
        parse_hnx_report(text, date(2026, 9, 18))
    with pytest.raises(ValueError, match="OHLC"):
        parse_hnx_report(text.replace("277,17", "270,00"), date(2026, 9, 21))


def test_sector_map_rejects_blank_and_duplicate_symbols(tmp_path) -> None:
    path = tmp_path / "industry_map.csv"
    path.write_text("symbol,sector_group\nAAA,ICB2:1700\nAAA,ICB2:1700\n", encoding="utf-8")
    with pytest.raises(StrategyDataError, match="trùng"):
        _read_sector_map(str(path), path.stat().st_mtime_ns)
    path.write_text("symbol,sector_group\nAAA,\n", encoding="utf-8")
    with pytest.raises(StrategyDataError, match="trống"):
        _read_sector_map(str(path), path.stat().st_mtime_ns)


def test_hnx_csv_imports_to_existing_market_store(tmp_path) -> None:
    directory = tmp_path / "cleaned_v2" / "index_data"
    directory.mkdir(parents=True)
    (directory / "cleaned_HNXINDEX.csv").write_text(
        "ticker,trade_date,open,high,low,close,is_final,data_quality_flags,source\n"
        "HNXINDEX,2026-09-21,275.29,277.17,272.61,274.38,True,,HNX\n",
        encoding="utf-8",
    )
    store = MarketStore(tmp_path / "market.db")
    assert store.import_directory(tmp_path / "cleaned_v2")["index"] == 1
    frame = store.index("HNXINDEX")
    assert frame.iloc[-1]["trade_date"] == "2026-09-21"
    assert frame.iloc[-1]["close"] == pytest.approx(274.38)
