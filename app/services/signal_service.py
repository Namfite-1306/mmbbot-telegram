from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from app.models import Action, Signal, SignalStatus, SignalValidationError
from app.providers import ProviderError, SignalProvider, TickerNotFoundError
from app.providers.strategy_signal_provider import StrategySignalProvider
from app.vn100 import VN100_SYMBOLS


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

    async def get_saved_signal(self, ticker: str) -> Signal:
        getter = getattr(self.provider, "get_saved_signal", None)
        signal = await getter(ticker) if callable(getter) else await self.get_signal(ticker)
        if not isinstance(signal, Signal):
            raise SignalValidationError("Provider không trả về Signal")
        return signal

    async def lookup(self, ticker: str, *, saved_only: bool = False) -> SignalLookup:
        try:
            signal = (await self.get_saved_signal(ticker) if saved_only
                      else await self.get_signal(ticker))
            return SignalLookup(ticker=ticker, signal=signal)
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

    async def scan(self, *, allow_previous_session: bool = False,
                   vn100_only: bool = False) -> list[Signal]:
        if allow_previous_session and isinstance(self.provider, StrategySignalProvider):
            signals = await self.provider.get_market_signals(
                allow_previous_session=True,
                symbols=VN100_SYMBOLS if vn100_only else None,
            )
        elif vn100_only and isinstance(self.provider, StrategySignalProvider):
            signals = await self.provider.get_market_signals(symbols=VN100_SYMBOLS)
        else:
            signals = await self.provider.get_market_signals()
        if vn100_only and self.provider.name == "strategy":
            def has_complete_total(signal: Signal) -> bool:
                components = signal.score_components or {}
                values = [components.get(key) for key in ("T", "F", "M")]
                return (signal.ticker in VN100_SYMBOLS
                        and all(value is not None and math.isfinite(float(value)) for value in values)
                        and signal.change_pct is not None and signal.change_pct != 0)

            signals = [signal for signal in signals if has_complete_total(signal)]
        if self.provider.name == "strategy":
            return sorted(signals, key=lambda signal: (-signal.score, signal.ticker))
        filtered = [
            signal
            for signal in signals
            if signal.status is SignalStatus.SUCCESS and signal.action in {Action.BUY, Action.SELL}
        ]
        order = {Action.BUY: 0, Action.SELL: 1}
        return sorted(filtered, key=lambda signal: (order[signal.action], -signal.score, signal.ticker))
