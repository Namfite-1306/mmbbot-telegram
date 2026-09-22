from __future__ import annotations

import re
from dataclasses import dataclass


TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{2}$")
TOKEN_SPLIT_PATTERN = re.compile(r"[\s,]+")


def normalize_ticker(value: str) -> str:
    return value.strip().upper()


def is_valid_ticker_format(value: str) -> bool:
    return bool(TICKER_PATTERN.fullmatch(normalize_ticker(value)))


@dataclass(frozen=True, slots=True)
class ParsedTickers:
    tickers: list[str]
    invalid: list[str]
    truncated: list[str]


def parse_tickers(text: str, max_tickers: int | None = None) -> ParsedTickers:
    tokens = [token for token in TOKEN_SPLIT_PATTERN.split(text.strip()) if token]
    valid: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        normalized = normalize_ticker(token)
        if normalized in seen:
            continue
        seen.add(normalized)
        if is_valid_ticker_format(normalized):
            valid.append(normalized)
        else:
            invalid.append(token.strip())
    if max_tickers is None or len(valid) <= max_tickers:
        return ParsedTickers(valid, invalid, [])
    return ParsedTickers(valid[:max_tickers], invalid, valid[max_tickers:])


def looks_like_ticker_input(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 150 or "\n" in stripped:
        return False
    tokens = [token for token in TOKEN_SPLIT_PATTERN.split(stripped) if token]
    return bool(tokens) and all(is_valid_ticker_format(token) for token in tokens)

