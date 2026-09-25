from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigurationError(RuntimeError):
    pass


TELEGRAM_TOKEN_PATTERN = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _as_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    signal_provider: str = "mock"
    database_path: Path = Path("data/fintech_bot.db")
    timezone: str = "Asia/Ho_Chi_Minh"
    log_level: str = "INFO"
    max_tickers_per_message: int = 10
    enable_mock_alert_scheduler: bool = False
    mock_alert_interval_seconds: int = 300
    digest_time: time = time(17, 30)
    admin_chat_id: int | None = None

    @classmethod
    def from_env(cls, env_file: str | Path = ".env", require_token: bool = True) -> "Settings":
        load_dotenv(env_file)
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if require_token and not token:
            raise ConfigurationError(
                "Thiếu TELEGRAM_BOT_TOKEN. Hãy sao chép .env.example thành .env và điền token BotFather."
            )
        if token and not TELEGRAM_TOKEN_PATTERN.fullmatch(token):
            raise ConfigurationError(
                "TELEGRAM_BOT_TOKEN sai định dạng. Chỉ dán token dạng số:chuỗi_bí_mật, không thêm tiền tố 'bot'."
            )
        provider = os.getenv("SIGNAL_PROVIDER", "mock").strip().lower()
        if provider not in {"mock", "strategy"}:
            raise ConfigurationError("SIGNAL_PROVIDER phải là mock hoặc strategy")
        timezone_name = os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh").strip()
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigurationError(f"TIMEZONE không hợp lệ: {timezone_name}") from exc
        try:
            max_tickers = int(os.getenv("MAX_TICKERS_PER_MESSAGE", "10"))
            interval = int(os.getenv("MOCK_ALERT_INTERVAL_SECONDS", "300"))
        except ValueError as exc:
            raise ConfigurationError("MAX_TICKERS_PER_MESSAGE và interval phải là số nguyên") from exc
        if not 1 <= max_tickers <= 50:
            raise ConfigurationError("MAX_TICKERS_PER_MESSAGE phải từ 1 đến 50")
        if interval < 30:
            raise ConfigurationError("MOCK_ALERT_INTERVAL_SECONDS phải ít nhất 30")
        digest_time_text = os.getenv("DIGEST_TIME", "17:30").strip()
        if not re.fullmatch(r"\d{2}:\d{2}", digest_time_text):
            raise ConfigurationError("DIGEST_TIME phải có dạng HH:MM, ví dụ 17:30")
        try:
            digest_time = time.fromisoformat(digest_time_text)
        except ValueError as exc:
            raise ConfigurationError("DIGEST_TIME không hợp lệ") from exc
        admin_chat_text = os.getenv("ADMIN_CHAT_ID", "").strip()
        try:
            admin_chat_id = int(admin_chat_text) if admin_chat_text else None
        except ValueError as exc:
            raise ConfigurationError("ADMIN_CHAT_ID phải là số nguyên") from exc
        if admin_chat_id == 0:
            raise ConfigurationError("ADMIN_CHAT_ID không được bằng 0")
        return cls(
            telegram_bot_token=token,
            signal_provider=provider,
            database_path=Path(os.getenv("DATABASE_PATH", "data/fintech_bot.db")),
            timezone=timezone_name,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            max_tickers_per_message=max_tickers,
            enable_mock_alert_scheduler=_as_bool(os.getenv("ENABLE_MOCK_ALERT_SCHEDULER", "false")),
            mock_alert_interval_seconds=interval,
            digest_time=digest_time,
            admin_chat_id=admin_chat_id,
        )
