from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.bot.formatter import format_signal
from app.bot.validators import normalize_ticker, parse_tickers
from app.config import ConfigurationError, Settings
from app.models import Action, Signal, SignalStatus, SignalValidationError


def make_signal(action: Action) -> Signal:
    now = datetime(2026, 9, 19, 8, tzinfo=timezone.utc)
    return Signal(
        signal_id=f"test-{action.value}",
        ticker="fpt",
        action=action,
        price=123_000,
        score=75,
        reasons=["Lý do kiểm thử"],
        data_time=now,
        generated_at=now,
        strategy_version="test_v1",
        timeframe="1D",
        status=SignalStatus.SUCCESS,
    )


def test_normalize_ticker() -> None:
    assert normalize_ticker("  fpt ") == "FPT"


def test_parse_mixed_separators() -> None:
    result = parse_tickers("HPG, FPT VNM")
    assert result.tickers == ["HPG", "FPT", "VNM"]
    assert result.invalid == []


def test_parse_removes_duplicates() -> None:
    result = parse_tickers("fpt, FPT hpg HPG")
    assert result.tickers == ["FPT", "HPG"]


def test_parse_applies_limit() -> None:
    result = parse_tickers("FPT HPG VNM VCB", max_tickers=3)
    assert result.tickers == ["FPT", "HPG", "VNM"]
    assert result.truncated == ["VCB"]


def test_signal_normalizes_ticker_and_validates_score() -> None:
    assert make_signal(Action.BUY).ticker == "FPT"
    now = datetime.now(timezone.utc)
    with pytest.raises(SignalValidationError):
        Signal("bad", "FPT", Action.BUY, 1, 101, [], now, now, "v1", "1D", SignalStatus.SUCCESS)


def test_signal_requires_aware_time_and_positive_success_price() -> None:
    aware = datetime.now(timezone.utc)
    naive = datetime.now()
    with pytest.raises(SignalValidationError):
        Signal("bad-time", "FPT", Action.BUY, 1, 50, [], naive, aware, "v1", "1D", SignalStatus.SUCCESS)
    with pytest.raises(SignalValidationError):
        Signal("bad-price", "FPT", Action.BUY, 0, 50, [], aware, aware, "v1", "1D", SignalStatus.SUCCESS)


def test_error_status_cannot_hide_behind_hold() -> None:
    now = datetime.now(timezone.utc)
    with pytest.raises(SignalValidationError):
        Signal("bad", "SSI", Action.HOLD, 0, 0, [], now, now, "v1", "1D", SignalStatus.INSUFFICIENT_DATA)


@pytest.mark.parametrize(
    ("action", "expected"),
    [(Action.BUY, "🟢 FPT — BUY"), (Action.SELL, "🔴 FPT — SELL"), (Action.HOLD, "🟡 FPT — HOLD")],
)
def test_formatter_actions(action: Action, expected: str) -> None:
    assert expected in format_signal(make_signal(action))


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (SignalStatus.WATCH_ONLY, "🟡 FPT — Theo dõi"),
        (SignalStatus.STALE_DATA, "🔴 FPT — Dữ liệu đã cũ"),
        (SignalStatus.INSUFFICIENT_DATA, "🔴 FPT — Không đủ dữ liệu"),
        (SignalStatus.PROVIDER_ERROR, "🔴 FPT — Lỗi dữ liệu"),
    ],
)
def test_formatter_non_action_status_colors(status: SignalStatus, expected: str) -> None:
    signal = make_signal(Action.BUY)
    from dataclasses import replace
    assert expected in format_signal(replace(signal, action=None, status=status))


def test_settings_rejects_bot_prefix_in_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "bot123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdef")
    with pytest.raises(ConfigurationError, match="sai định dạng"):
        Settings.from_env(tmp_path / "missing.env")


def test_settings_validate_digest_time(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdef")
    monkeypatch.setenv("DIGEST_TIME", "25:99")
    with pytest.raises(ConfigurationError, match="DIGEST_TIME"):
        Settings.from_env(tmp_path / "missing.env")
    monkeypatch.setenv("DIGEST_TIME", "18:15")
    assert Settings.from_env(tmp_path / "missing.env").digest_time.strftime("%H:%M") == "18:15"
    monkeypatch.setenv("ADMIN_CHAT_ID", "not-a-number")
    with pytest.raises(ConfigurationError, match="ADMIN_CHAT_ID"):
        Settings.from_env(tmp_path / "missing.env")
    monkeypatch.setenv("ADMIN_CHAT_ID", "12345")
    assert Settings.from_env(tmp_path / "missing.env").admin_chat_id == 12345
