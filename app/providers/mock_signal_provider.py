from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.models import Action, Signal, SignalStatus
from app.providers.base import ProviderError, SignalProvider, TickerNotFoundError


class MockSignalProvider(SignalProvider):
    name = "mock"
    _timezone = ZoneInfo("Asia/Ho_Chi_Minh")
    _data_time = datetime(2026, 9, 19, 14, 45, tzinfo=_timezone)

    _definitions = {
        "FPT": (Action.BUY, 123_000, 82, ["EMA20 cắt lên EMA50", "Khối lượng cao hơn trung bình 20 phiên"]),
        "HPG": (Action.HOLD, 28_500, 55, ["Xu hướng chưa rõ ràng", "Động lượng ở vùng trung tính"]),
        "VNM": (Action.SELL, 61_200, 76, ["Giá đóng cửa dưới SMA20", "Động lượng 5 phiên suy yếu"]),
        "VCB": (Action.BUY, 68_400, 74, ["Xu hướng trung hạn tích cực", "Biến động trong ngưỡng kiểm soát"]),
        "MBB": (Action.BUY, 27_300, 71, ["Động lượng giá cải thiện", "Thanh khoản duy trì trên trung bình"]),
        "BID": (Action.HOLD, 49_100, 58, ["Tín hiệu kỹ thuật chưa đồng thuận"]),
        "CTG": (Action.SELL, 39_800, 67, ["Giá suy yếu dưới đường trung bình ngắn hạn"]),
        "GAS": (Action.HOLD, 72_500, 52, ["Biên dao động hẹp"]),
        "MWG": (Action.BUY, 68_700, 69, ["Động lượng 20 phiên tích cực"]),
        "PLX": (Action.SELL, 41_300, 64, ["Khối lượng giảm trong nhịp hồi"]),
        "VIC": (Action.HOLD, 48_000, 50, ["Chưa xác nhận xu hướng"]),
        "VHM": (Action.BUY, 45_600, 66, ["Giá vượt vùng tích lũy ngắn hạn"]),
    }
    _special_tickers = {"SSI", "ERR"}

    async def supports_ticker(self, ticker: str) -> bool:
        normalized = ticker.strip().upper()
        return normalized in self._definitions or normalized in self._special_tickers

    async def get_signal(self, ticker: str) -> Signal:
        normalized = ticker.strip().upper()
        if not await self.supports_ticker(normalized):
            raise TickerNotFoundError(normalized)
        if normalized == "ERR":
            raise ProviderError("Mock provider error")
        if normalized == "SSI":
            return Signal(
                signal_id="mock-20260919-SSI-insufficient-v1",
                ticker=normalized,
                action=None,
                price=0,
                score=0,
                reasons=["Chưa đủ số phiên để tính toàn bộ indicator"],
                data_time=self._data_time,
                generated_at=self._data_time,
                strategy_version="mock_v1",
                timeframe="1D",
                status=SignalStatus.INSUFFICIENT_DATA,
            )

        action, price, score, reasons = self._definitions[normalized]
        return Signal(
            signal_id=f"mock-20260919-{normalized}-{action.value.lower()}-v1",
            ticker=normalized,
            action=action,
            price=price,
            score=score,
            reasons=list(reasons),
            data_time=self._data_time,
            generated_at=self._data_time,
            strategy_version="mock_v1",
            timeframe="1D",
            status=SignalStatus.SUCCESS,
        )

    async def get_market_signals(self) -> list[Signal]:
        return [await self.get_signal(ticker) for ticker in self._definitions]

    def latest_data_time(self) -> str:
        return self._data_time.isoformat()

