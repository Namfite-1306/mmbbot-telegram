from __future__ import annotations

from datetime import date

import pytest

from app.cafef_market_data import CafeFDataError, CafeFMarketClient, parse_price_history


def _payload(**changes):
    row = {"Symbol": "FPT", "Ngay": "21/09/2026", "GiaMoCua": 66.5,
           "GiaCaoNhat": 66.8, "GiaThapNhat": 65.0, "GiaDongCua": 66.4,
           "KhoiLuongKhopLenh": 8401600}
    row.update(changes)
    return {"Data": {"TotalCount": 1, "Data": [row]}}


def test_cafef_normalizes_public_history_fields():
    bars = parse_price_history(_payload(), "FPT", date(2026, 9, 14), date(2026, 9, 21))
    assert bars == [{"trade_date": "2026-09-21", "open": 66.5, "high": 66.8,
                     "low": 65.0, "close": 66.4, "volume": 8401600}]


@pytest.mark.parametrize("change", [
    {"Symbol": "HPG"}, {"GiaCaoNhat": 60}, {"KhoiLuongKhopLenh": -1},
    {"Ngay": "22/09/2026"},
])
def test_cafef_rejects_wrong_or_invalid_rows(change):
    with pytest.raises(CafeFDataError):
        parse_price_history(_payload(**change), "FPT", date(2026, 9, 14), date(2026, 9, 21))


def test_cafef_rejects_truncated_page():
    payload = _payload()
    payload["Data"]["TotalCount"] = 2
    with pytest.raises(CafeFDataError, match="phân trang"):
        parse_price_history(payload, "FPT", date(2026, 9, 14), date(2026, 9, 21))


def test_cafef_uses_index_alias_and_exchange():
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"Data": {"TotalCount": 0, "Data": []}}

    class Session:
        def __init__(self):
            self.headers = {}
            self.params = None

        def get(self, url, *, params, timeout):
            self.params = params
            return Response()

    session = Session()
    client = CafeFMarketClient(session)
    assert client.get_ohlcv_bars("HNXINDEX", "", date(2026, 9, 14),
                                 date(2026, 9, 21), index=True) == []
    assert session.params["Symbol"] == "HNX-INDEX"
    assert session.params["ExchangeType"] == "HNX"
