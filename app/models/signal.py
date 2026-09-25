from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class Action(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SignalStatus(StrEnum):
    SUCCESS = "SUCCESS"
    WATCH_ONLY = "WATCH_ONLY"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    STALE_DATA = "STALE_DATA"
    PROVIDER_ERROR = "PROVIDER_ERROR"


class SignalValidationError(ValueError):
    """Raised when a provider returns a signal that violates the contract."""


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    ticker: str
    action: Action | None
    price: float
    score: float
    reasons: list[str]
    data_time: datetime
    generated_at: datetime
    strategy_version: str
    timeframe: str
    status: SignalStatus
    score_components: dict[str, float | None] | None = None
    change_pct: float | None = None

    def __post_init__(self) -> None:
        ticker = self.ticker.strip().upper()
        object.__setattr__(self, "ticker", ticker)

        if not self.signal_id.strip():
            raise SignalValidationError("signal_id không được để trống")
        if not ticker:
            raise SignalValidationError("ticker không được để trống")
        if not math.isfinite(self.score) or not 0 <= self.score <= 100:
            raise SignalValidationError("score phải nằm trong khoảng 0–100")
        if self.change_pct is not None and not math.isfinite(self.change_pct):
            raise SignalValidationError("change_pct phải là số hữu hạn")
        if self.data_time.tzinfo is None or self.data_time.utcoffset() is None:
            raise SignalValidationError("data_time phải có timezone")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise SignalValidationError("generated_at phải có timezone")
        if self.status is SignalStatus.SUCCESS:
            if self.action is None:
                raise SignalValidationError("signal SUCCESS phải có action")
            if not math.isfinite(self.price) or self.price <= 0:
                raise SignalValidationError("price phải lớn hơn 0 khi signal SUCCESS")
        elif self.action is not None:
            raise SignalValidationError(
                "signal lỗi/thiếu dữ liệu không được dùng BUY, SELL hoặc HOLD làm action"
            )
