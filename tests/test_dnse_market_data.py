from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.dnse_market_data import DNSEConfigurationError, DNSEDataError, DNSEMarketClient, _daily_bars, _latest_trade


def _stamp(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")).timestamp())


def test_dnse_ohlcv_normalization_and_reject_misaligned_arrays():
    body = {"t": [_stamp("2026-09-18")], "o": [20], "h": [22], "l": [19],
            "c": [21], "v": [12345]}
    assert _daily_bars(body, date(2026, 9, 17), date(2026, 9, 19)) == [
        {"trade_date": "2026-09-18", "open": 20.0, "high": 22.0,
         "low": 19.0, "close": 21.0, "volume": 12345}]
    with pytest.raises(DNSEDataError, match="cùng độ dài"):
        _daily_bars({**body, "v": []}, date(2026, 9, 17), date(2026, 9, 19))


def test_dnse_client_uses_read_only_ohlc_and_no_credential_logging(tmp_path, monkeypatch):
    monkeypatch.setenv("DNSE_API_KEY", "test-key")
    monkeypatch.setenv("DNSE_API_SECRET", "test-secret")

    class FakeSDK:
        def get(self, path, **kwargs):
            assert path == "/price/ohlc"
            assert kwargs["params"]["type"] == "STOCK"
            assert kwargs["params"]["resolution"] == "1D"
            return SimpleNamespace(status_code=200, json=lambda: {
                "t": [_stamp("2026-09-18")], "o": [20], "h": [21],
                "l": [19], "c": [20], "v": [100]})

    client = DNSEMarketClient(tmp_path / ".env", client=FakeSDK())
    assert len(client.get_ohlcv_bars("AAA", date(2026, 9, 17), date(2026, 9, 19))) == 1

    class DeniedSDK:
        def get(self, path, **kwargs):
            return SimpleNamespace(status_code=403, json=lambda: {"secret": "test-secret"})

    with pytest.raises(DNSEConfigurationError, match="từ chối") as error:
        DNSEMarketClient(tmp_path / ".env", client=DeniedSDK()).get_ohlcv_bars(
            "AAA", date(2026, 9, 17), date(2026, 9, 19))
    assert "test-secret" not in str(error.value)


def test_latest_trade_normalizes_price_and_rejects_wrong_symbol_or_units(tmp_path, monkeypatch):
    monkeypatch.setenv("DNSE_API_KEY", "test-key")
    monkeypatch.setenv("DNSE_API_SECRET", "test-secret")
    payload = {"trades": [{"symbol": "FPT", "matchPrice": 66.4, "matchQtty": 100,
                           "totalVolumeTraded": 10000, "time": "2026-09-21 14:45:02.639"}]}

    class FakeSDK:
        def get(self, path, **kwargs):
            assert path == "/price/FPT/trades/latest"
            assert kwargs["params"] == {"boardId": "G1"}
            return SimpleNamespace(status_code=200, json=lambda: payload)

    trade = DNSEMarketClient(tmp_path / ".env", client=FakeSDK()).get_latest_trade("FPT", 66000)
    assert trade["price_vnd"] == 66400
    assert trade["time"].date() == date(2026, 9, 21)
    assert trade["total_volume"] == 10000
    with pytest.raises(DNSEDataError, match="đơn vị"):
        _latest_trade(payload, "FPT", 1000)
    with pytest.raises(DNSEDataError, match="mã"):
        _latest_trade(payload, "HPG")
