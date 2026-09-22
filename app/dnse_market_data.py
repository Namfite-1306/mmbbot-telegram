"""Read-only DNSE OpenAPI market-data adapter (no trading endpoints)."""

from __future__ import annotations

import os
import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import load_dotenv


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


class DNSEDataError(RuntimeError):
    pass


class DNSENoDataError(DNSEDataError):
    pass


class DNSEConfigurationError(DNSEDataError):
    pass


def _daily_bars(body: object, start: date, end: date) -> list[dict]:
    """Normalize the documented OHLC arrays; reject malformed/partial replies."""
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        body = body["data"]
    if not isinstance(body, dict):
        raise DNSEDataError("DNSE trả về OHLC không đúng định dạng")
    if body.get("s") in {"no_data", "no_data_available"}:
        return []
    keys = ("t", "o", "h", "l", "c", "v")
    series = [body.get(key) for key in keys]
    if any(not isinstance(items, list) for items in series):
        raise DNSEDataError("DNSE thiếu mảng OHLCV")
    if len({len(items) for items in series}) != 1:
        raise DNSEDataError("DNSE trả về mảng OHLCV không cùng độ dài")
    rows = []
    for stamp, op, high, low, close, volume in zip(*series):
        try:
            day = datetime.fromtimestamp(int(stamp), VN_TZ).date()
            values = [float(value) for value in (op, high, low, close, volume)]
        except (TypeError, ValueError, OverflowError) as exc:
            raise DNSEDataError("DNSE có giá trị OHLCV không hợp lệ") from exc
        op, high, low, close, volume = values
        if not (all(math.isfinite(value) for value in values) and low > 0
                and low <= min(op, close) and high >= max(op, close) and volume >= 0):
            raise DNSEDataError("DNSE có nến OHLCV không hợp lệ")
        if start <= day <= end:
            rows.append({"trade_date": day.isoformat(), "open": op, "high": high,
                         "low": low, "close": close, "volume": int(volume)})
    return rows


def _latest_trade(body: object, symbol: str, reference_close: float | None = None) -> dict:
    """Normalize DNSE's latest STOCK match; market prices are in thousand VND."""
    if not isinstance(body, dict) or not isinstance(body.get("trades"), list) or not body["trades"]:
        raise DNSENoDataError(f"DNSE chưa có giá khớp gần nhất cho {symbol}")
    trade = body["trades"][0]
    if not isinstance(trade, dict) or str(trade.get("symbol", "")).upper() != symbol:
        raise DNSEDataError("DNSE trả về mã khớp lệnh không hợp lệ")
    try:
        stamp = trade["time"]
        if isinstance(stamp, (int, float)):
            moment = datetime.fromtimestamp(stamp / (1000 if stamp > 1e11 else 1), VN_TZ)
        else:
            moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            moment = moment.replace(tzinfo=VN_TZ) if moment.tzinfo is None else moment.astimezone(VN_TZ)
        price = float(trade["matchPrice"]) * 1000
        if not math.isfinite(price) or price <= 0:
            raise ValueError("invalid price")
        if reference_close and not 0.5 <= price / reference_close <= 1.5:
            raise ValueError("unit mismatch")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise DNSEDataError("Giá/thời gian khớp lệnh DNSE không hợp lệ hoặc không khớp đơn vị") from exc
    if moment > datetime.now(VN_TZ) + timedelta(minutes=5):
        raise DNSEDataError("DNSE trả về thời gian khớp lệnh trong tương lai")
    result = {"symbol": symbol, "price_vnd": price, "time": moment, "source": "DNSE"}
    for source, target in (("matchQtty", "match_volume"), ("totalVolumeTraded", "total_volume"),
                           ("openPrice", "open_vnd"), ("highestPrice", "high_vnd"),
                           ("lowestPrice", "low_vnd")):
        raw = trade.get(source)
        if raw is not None:
            try:
                number = float(raw) * (1000 if target.endswith("_vnd") else 1)
                if math.isfinite(number) and number >= 0:
                    result[target] = number
            except (TypeError, ValueError):
                pass
    return result


class DNSEMarketClient:
    def __init__(self, env_file, client=None):
        load_dotenv(env_file)
        key = os.getenv("DNSE_API_KEY", "").strip()
        secret = os.getenv("DNSE_API_SECRET", "").strip()
        if not key or not secret:
            raise DNSEConfigurationError("Thiếu DNSE_API_KEY hoặc DNSE_API_SECRET trong .env")
        if client is None:
            try:
                from dnse import DnseClient
            except ImportError as exc:
                raise DNSEConfigurationError("Chưa cài dnse; chạy pip install -r requirements.txt") from exc
            client = DnseClient(api_key=key, api_secret=secret,
                                base_url="https://openapi.dnse.com.vn")
        self.client = client

    def get_latest_trade(self, symbol: str, reference_close: float | None = None) -> dict:
        symbol = symbol.strip().upper()
        if not symbol.isalnum() or not 1 <= len(symbol) <= 10:
            raise DNSEDataError("Mã cổ phiếu không hợp lệ")
        try:
            response = self.client.get(f"/price/{symbol}/trades/latest", params={"boardId": "G1"})
        except Exception as exc:
            if (getattr(exc, "status_code", None) in (401, 403)
                    or type(exc).__name__ == "DnseAuthError"):
                raise DNSEConfigurationError("DNSE từ chối API key/secret hoặc quyền market-data") from None
            raise DNSEDataError(f"DNSE không phản hồi giá khớp gần nhất cho {symbol}") from exc
        if response.status_code in (401, 403):
            raise DNSEConfigurationError("DNSE từ chối API key/secret hoặc quyền market-data")
        if response.status_code != 200:
            raise DNSEDataError(f"DNSE giá khớp {symbol}: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise DNSEDataError("DNSE trả về JSON giá khớp không hợp lệ") from exc
        return _latest_trade(body, symbol, reference_close)

    def get_ohlcv_bars(self, symbol: str, start: date, end: date, *, index: bool = False) -> list[dict]:
        beginning = datetime.combine(start, time.min, tzinfo=VN_TZ).astimezone(timezone.utc)
        ending = datetime.combine(end + timedelta(days=1), time.min, tzinfo=VN_TZ).astimezone(timezone.utc)
        try:
            response = self.client.get(
                "/price/ohlc",
                params={"type": "INDEX" if index else "STOCK", "symbol": symbol,
                        "resolution": "1D", "from": int(beginning.timestamp()),
                        "to": int(ending.timestamp())},
            )
        except Exception as exc:
            if (getattr(exc, "status_code", None) in (401, 403)
                    or type(exc).__name__ == "DnseAuthError"):
                raise DNSEConfigurationError("DNSE từ chối API key/secret hoặc quyền market-data") from None
            # SDK exceptions can contain signed requests. Never surface their text.
            raise DNSEDataError(f"DNSE không phản hồi OHLC cho {symbol}") from exc
        if response.status_code in (401, 403):
            raise DNSEConfigurationError("DNSE từ chối API key/secret hoặc quyền market-data")
        if response.status_code != 200:
            raise DNSEDataError(f"DNSE OHLC {symbol}: HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise DNSEDataError("DNSE trả về JSON không hợp lệ") from exc
        return _daily_bars(body, start, end)
