from __future__ import annotations

from datetime import datetime
from io import BytesIO
from zoneinfo import ZoneInfo

import pytest
import pandas as pd

from app.bot.formatter import format_scan, format_status, format_stock_overview, format_watchlist
from app.cafef_news import NewsItem, _CompanyNewsParser
from app.market_store import MarketStore
from app.models import Action, Signal, SignalStatus
from app.stock_chart import calculate_cmf, render_money_flow_chart, render_stock_chart


def test_cafef_news_parser_accepts_only_source_links() -> None:
    parser = _CompanyNewsParser()
    parser.feed('''<li><span class="timeTitle">21/09/2026 10:00</span>
        <a class="docnhanhTitle" href="/du-lieu/VPB-1/news.chn">Tin thử</a></li>
        <li><a class="docnhanhTitle" href="https://bad.example/news">Bad</a></li>''')
    assert len(parser.items) == 1
    assert parser.items[0].url == "https://cafef.vn/du-lieu/VPB-1/news.chn"


def test_overview_uses_only_announced_fundamental(tmp_path) -> None:
    store = MarketStore(tmp_path / "market.db")
    store.initialize()
    with store.connect() as conn:
        conn.execute("""INSERT INTO market_prices(symbol,trade_date,open,high,low,close,volume,is_final)
            VALUES('AAA','2026-09-21',10,11,9,10,100,1)""")
        conn.execute("""INSERT INTO market_fundamentals VALUES
            ('AAA','quarterly','2026Q1','2026-04-30','{"report_period":"2026Q1"}')""")
        conn.execute("""INSERT INTO market_fundamentals VALUES
            ('AAA','quarterly','2026Q2','2026-09-22','{"report_period":"2026Q2"}')""")
    overview = store.stock_overview("AAA")
    assert overview["financial"]["report_period"] == "2026Q1"


def test_formatter_does_not_mislabel_technical_score_as_total() -> None:
    timestamp = datetime(2026, 9, 21, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    signal = Signal("x", "AAA", None, 10, 100, ["Thiếu ngành"], timestamp,
                    timestamp, "v1", "1D", SignalStatus.WATCH_ONLY,
                    {"T": 100, "F": None, "M": 40})
    text = format_stock_overview(signal, {}, [])
    assert "Điểm: chưa tính được." in text
    assert "T: 100/100" not in text and "F: chưa có" not in text and "M: 40/100" not in text
    assert "37,5%" not in text and "31,25%" not in text
    assert text.startswith("🟡 TÍN HIỆU: THEO DÕI")
    assert "Tin liên quan" not in text
    assert "Thiếu ngành" in text


def test_overview_shows_two_news_titles_and_links() -> None:
    timestamp = datetime(2026, 9, 21, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    signal = Signal("x", "AAA", Action.BUY, 10000, 80, ["Đạt điều kiện"], timestamp,
                    timestamp, "v1", "1D", SignalStatus.SUCCESS)
    news = [NewsItem(f"Headline {i}", "21/09/2026", f"https://cafef.vn/news-{i}.chn")
            for i in range(3)]
    text = format_stock_overview(signal, {}, news)
    assert text.startswith("🟢 TÍN HIỆU: MUA | AAA")
    assert "news-0.chn" in text and "news-1.chn" in text
    assert "news-2.chn" not in text
    assert "Headline 0: https://" in text
    assert "(CafeF)" not in text


def test_old_dnse_match_shows_timestamp_without_realtime_claim() -> None:
    timestamp = datetime(2026, 9, 21, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    signal = Signal("x", "AAA", None, 10, 50, [], timestamp,
                    timestamp, "v1", "1D", SignalStatus.WATCH_ONLY)
    text = format_stock_overview(signal, {}, [], {
        "symbol": "AAA", "price_vnd": 11000, "time": timestamp,
        "reference_close": 10000,
    })
    assert "Dữ liệu mới nhất" in text
    assert "khớp gần nhất" not in text
    assert "Thời điểm dữ liệu: 15:00:00 21/09/2026" in text
    assert "11.000 VNĐ" in text


def test_overview_explains_score_gate_and_observed_price_source() -> None:
    timestamp = datetime(2026, 9, 21, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    signal = Signal("x", "AAA", None, 10000, 70,
                    ["T=70/100; E=NO_ENTRY", "Market regime: BEAR"],
                    timestamp, timestamp, "v1", "1D", SignalStatus.WATCH_ONLY,
                    {"T": 70, "F": 70, "M": 70})
    text = format_stock_overview(signal, {"quote": {"trade_date": "2026-09-21",
                                                "close": 10000, "is_final": 1,
                                                "source": "dnse"}}, [])
    assert "Nguồn giá lưu: DNSE · ngày 2026-09-21" in text
    assert "Điểm: 70,0/100" in text
    assert "T: 70/100" not in text and "F: 70/100" not in text and "M: 70/100" not in text
    assert "Điểm tổng còn 5.0 điểm" in text
    assert "Cổng vào lệnh hiện là NO_ENTRY" in text
    assert "Trạng thái thị trường hiện là BEAR" in text


def test_scan_shows_session_without_coverage_and_status_shows_eod_health() -> None:
    stamp = datetime(2026, 9, 23, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    signal = Signal("x", "AAA", Action.BUY, 10000, 80, [], stamp, stamp,
                    "v1", "1D", SignalStatus.SUCCESS,
                    {"T": 80, "F": 80, "M": 80})
    scan = format_scan([signal], "Asia/Ho_Chi_Minh", scan_date="2026-09-23",
                       coverage=(870, 1523), expected_scan_date="2026-09-24")
    assert "đang dùng dữ liệu phiên 2026-09-23" in scan
    assert "không phải tín hiệu của 2026-09-24" in scan
    assert "Phiên dữ liệu: 2026-09-23\n" in scan
    assert "870/1523" not in scan
    assert "không được coi là tín hiệu BÁN" not in scan
    assert "đã lưu; /scan không crawl" not in scan
    current_scan = format_scan([signal], "Asia/Ho_Chi_Minh", scan_date="2026-09-24",
                               expected_scan_date="2026-09-24")
    assert "đang dùng dữ liệu phiên" not in current_scan
    unavailable = Signal("x", "BBB", None, 0, 0, [], stamp, stamp,
                         "v1", "1D", SignalStatus.INSUFFICIENT_DATA)
    scan_with_missing = format_scan([signal, unavailable], "Asia/Ho_Chi_Minh",
                                    scan_date="2026-09-24")
    assert "Không lấy được dữ liệu đúng phiên cho: BBB" in scan_with_missing
    status = format_status("strategy", True, stamp, "2026-09-23",
                           None, "2026-09-23", {"state": "failed", "retry_minutes": 15,
                                                "target": "2026-09-23", "covered": 870,
                                                "universe": 1523, "required": 1000,
                                                "failed_symbols": 3, "missing_symbols": 653})
    assert "Cập nhật toàn thị trường đã xác nhận: chưa có" in status
    assert "Mã đã lưu hợp lệ: 870/1523; cần ít nhất 1000" in status
    assert "Mã tải lỗi: 3" in status and "Mã còn thiếu: 653" in status
    assert "thử lại sau khoảng 15 phút" in status


def test_vn100_scan_shows_only_movers_ranked_by_total_score() -> None:
    stamp = datetime(2026, 9, 24, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))

    def signal(ticker: str, score: float, change: float) -> Signal:
        return Signal(ticker, ticker, None, 10_000, score, [], stamp, stamp,
                      "v1", "1D", SignalStatus.WATCH_ONLY,
                      {"T": score, "F": score, "M": score}, change)

    text = format_scan([
        signal("ACB", 90, 2.5),
        signal("FPT", 40, -1.25),
        signal("HPG", 70, 0.75),
    ], "Asia/Ho_Chi_Minh", scan_date="2026-09-24", vn100_mode=True)

    assert "VN100 — 3 mã tính được điểm" in text
    assert "T/F/M" not in text
    assert "Đang tăng: 2 | 🔴 Đang giảm: 1" in text
    assert text.index("ACB: +2.50% · 90.00/100") < text.index("HPG: +0.75% · 70.00/100")
    assert "FPT: -1.25% · 40.00/100" in text
    assert "Điểm thấp nhất" in text


def test_portfolio_traffic_lights_price_change_and_navigation() -> None:
    timestamp = datetime(2026, 9, 21, 15, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))

    def make(ticker: str, action: Action | None, status: SignalStatus) -> Signal:
        return Signal(ticker, ticker, action, 10000, 80, [], timestamp,
                      timestamp, "v1", "1D", status)

    quote = {"quote": {"trade_date": "2026-09-21", "close": 10000, "is_final": 1},
             "previous_close": 9500}
    text = format_watchlist([
        ("AAA", make("AAA", Action.BUY, SignalStatus.SUCCESS), None, quote),
        ("BBB", make("BBB", None, SignalStatus.WATCH_ONLY), None, quote),
        ("CCC", make("CCC", Action.SELL, SignalStatus.SUCCESS), None, quote),
    ])
    assert "🟢 AAA — Tích cực" in text
    assert "🟡 BBB — Trung tính" in text
    assert "🔴 CCC — Cần chú ý" in text
    assert "+5.26%" in text
    assert "/soi <MÃ>" in text and "/help" in text


def test_chart_is_png_with_sufficient_final_bars(tmp_path) -> None:
    store = MarketStore(tmp_path / "chart.db")
    store.initialize()
    with store.connect() as conn:
        conn.executemany("""INSERT INTO market_prices(symbol,trade_date,open,high,low,close,volume,is_final)
            VALUES('AAA',?,?,?,?,?,?,1)""",
            [(f"2026-08-{day:02d}", 10, 11, 9, 10 + day / 100, 1000)
             for day in range(1, 21)])
    image = render_stock_chart(store, "AAA")
    assert isinstance(image, BytesIO)
    assert image.getvalue().startswith(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ValueError, match="ít nhất 20 phiên"):
        render_stock_chart(store, "BBB")
    flow_image = render_money_flow_chart(store, "AAA")
    assert flow_image.getvalue().startswith(b"\x89PNG\r\n\x1a\n")
    with pytest.raises(ValueError, match="ít nhất 20 phiên"):
        render_money_flow_chart(store, "BBB")


def test_cmf_uses_close_location_and_handles_flat_bars() -> None:
    frame = pd.DataFrame({
        "high": [11.0] * 20, "low": [9.0] * 20,
        "close": [11.0] * 20, "volume": [100.0] * 20,
    })
    assert calculate_cmf(frame).iloc[-1] == pytest.approx(1.0)
    frame["close"] = 9.0
    assert calculate_cmf(frame).iloc[-1] == pytest.approx(-1.0)
    frame["high"] = frame["low"]
    assert calculate_cmf(frame).iloc[-1] == pytest.approx(0.0)
