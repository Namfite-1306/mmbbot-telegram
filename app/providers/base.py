from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import Signal


class ProviderError(RuntimeError):
    """The upstream signal provider failed."""


class TickerNotFoundError(LookupError):
    """Ticker format is valid but the provider does not support it."""


class SignalProvider(ABC):
    name = "base"

    @abstractmethod
    async def get_signal(self, ticker: str) -> Signal:
        raise NotImplementedError

    @abstractmethod
    async def get_market_signals(self) -> list[Signal]:
        raise NotImplementedError

    @abstractmethod
    async def supports_ticker(self, ticker: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def latest_data_time(self) -> str:
        raise NotImplementedError

