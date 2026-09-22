"""Read CafeF's public price-history feed as a last-resort OHLCV source.

CafeF displays stock prices in thousands of VND. Conversion to the market
store's VND units is performed only after checking an overlapping close.
This endpoint belongs to the CafeF web page and may change without notice.
"""

from __future__ import annotations

import math
from datetime import date, datetime

import requests


PRICE_HISTORY_URL = "https://cafef.vn/du-lieu/Ajax/PageNew/DataHistory/PriceHistory.ashx"
INDEX_EXCHANGES = {"VNINDEX": "HOSE", "HNXINDEX": "HNX", "UPCOMINDEX": "UPCOM"}
INDEX_SYMBOLS = {"VNINDEX": "VNINDEX", "HNXINDEX": "HNX-INDEX", "UPCOMINDEX": "UPCOM-INDEX"}
STOCK_EXCHANGES = {"HSX": "HOSE", "HOSE": "HOSE", "HNX": "HNX", "UPCOM": "UPCOM"}


class CafeFDataError(RuntimeError):
    """CafeF is unreachable or returned unusable market data."""


class CafeFNoDataError(CafeFDataError):
    """CafeF returned a valid, empty price history for this date range."""


def _positive_number(row: dict, name: str) -> float:
    value = row.get(name)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CafeFDataError(f"CafeF thiếu {name}") from exc
    if not math.isfinite(number) or number <= 0:
        raise CafeFDataError(f"CafeF có {name} không hợp lệ")
    return number


def parse_price_history(payload: dict, symbol: str, start: date, end: date) -> list[dict]:
    """Validate and normalize one page of CafeF history, without changing units."""
    container = payload.get("Data") if isinstance(payload, dict) else None
    items = container.get("Data") if isinstance(container, dict) else None
    total = container.get("TotalCount") if isinstance(container, dict) else None
    if not isinstance(items, list):
        raise CafeFDataError("CafeF trả về định dạng lịch sử giá không hợp lệ")
    if not isinstance(total, int) or total > len(items):
        raise CafeFDataError("CafeF yêu cầu phân trang; không dùng dữ liệu thiếu")
    bars = []
    for item in items:
        if not isinstance(item, dict) or str(item.get("Symbol", "")).upper() != symbol:
            raise CafeFDataError(f"CafeF trả về sai mã {symbol}")
        try:
            trade_day = datetime.strptime(item["Ngay"], "%d/%m/%Y").date()
        except (KeyError, TypeError, ValueError) as exc:
            raise CafeFDataError("CafeF trả về ngày giao dịch không hợp lệ") from exc
        if not start <= trade_day <= end:
            raise CafeFDataError("CafeF trả về ngày ngoài khoảng yêu cầu")
        op = _positive_number(item, "GiaMoCua")
        high = _positive_number(item, "GiaCaoNhat")
        low = _positive_number(item, "GiaThapNhat")
        close = _positive_number(item, "GiaDongCua")
        try:
            volume = int(item["KhoiLuongKhopLenh"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CafeFDataError("CafeF thiếu khối lượng khớp lệnh") from exc
        if volume < 0 or low > min(op, close) or high < max(op, close):
            raise CafeFDataError("CafeF trả về OHLC/khối lượng không hợp lệ")
        bars.append({"trade_date": trade_day.isoformat(), "open": op, "high": high,
                     "low": low, "close": close, "volume": volume})
    if len({bar["trade_date"] for bar in bars}) != len(bars):
        raise CafeFDataError("CafeF trả về ngày giao dịch trùng")
    return sorted(bars, key=lambda bar: bar["trade_date"])


class CafeFMarketClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; Finance1Bot/1.0)"})

    def get_ohlcv_bars(self, symbol: str, exchange: str, start: date,
                       end: date, *, index: bool = False) -> list[dict]:
        symbol = symbol.strip().upper()
        board = INDEX_EXCHANGES.get(symbol) if index else STOCK_EXCHANGES.get(exchange.upper())
        provider_symbol = INDEX_SYMBOLS.get(symbol, symbol) if index else symbol
        if not board or not symbol.isalnum():
            raise CafeFDataError(f"CafeF không hỗ trợ mã/sàn {symbol}/{exchange}")
        params = {"ExchangeType": board, "Symbol": provider_symbol,
                  "StartDate": start.strftime("%m/%d/%Y"),
                  "EndDate": end.strftime("%m/%d/%Y"), "PageIndex": 1, "PageSize": 20}
        try:
            response = self.session.get(PRICE_HISTORY_URL, params=params, timeout=12)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CafeFDataError(f"CafeF không trả về dữ liệu: {exc}") from exc
        return parse_price_history(payload, provider_symbol, start, end)
