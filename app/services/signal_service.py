from __future__ import annotations

import logging
from dataclasses import dataclass

from app.models import Action, Signal, SignalStatus, SignalValidationError
from app.providers import ProviderError, SignalProvider, TickerNotFoundError


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SignalLookup:
    ticker: str
    signal: Signal | None = None
    error_code: str | None = None


class SignalService:
    def __init__(self, provider: SignalProvider):
        self.provider = provider

    async def ticker_exists(self, ticker: str) -> bool:
        return await self.provider.supports_ticker(ticker)

    async def get_signal(self, ticker: str) -> Signal:
        signal = await self.provider.get_signal(ticker)
        if not isinstance(signal, Signal):
            raise SignalValidationError("Provider không trả về Signal")
        return signal

    async def lookup(self, ticker: str) -> SignalLookup:
        try:
            return SignalLookup(ticker=ticker, signal=await self.get_signal(ticker))
        except TickerNotFoundError:
            return SignalLookup(ticker=ticker, error_code="not_found")
        except SignalValidationError:
            logger.exception("Invalid signal", extra={"ticker": ticker})
            return SignalLookup(ticker=ticker, error_code="invalid_signal")
        except ProviderError:
            logger.exception("Provider error", extra={"ticker": ticker})
            return SignalLookup(ticker=ticker, error_code="provider_error")

    async def lookup_many(self, tickers: list[str]) -> list[SignalLookup]:
        results = []
        for ticker in tickers:
            results.append(await self.lookup(ticker))
        return results

    async def scan(self) -> list[Signal]:
        try:
            signals = await self.provider.get_market_signals()
        except ProviderError:
            logger.exception("Market scan provider error")
            return []
        if self.provider.name == "strategy":
            return sorted(signals, key=lambda signal: (-signal.score, signal.ticker))
        filtered = [
            signal
            for signal in signals
            if signal.status is SignalStatus.SUCCESS and signal.action in {Action.BUY, Action.SELL}
        ]
        order = {Action.BUY: 0, Action.SELL: 1}
        return sorted(filtered, key=lambda signal: (order[signal.action], -signal.score, signal.ticker))
