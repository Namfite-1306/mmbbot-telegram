from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Bot, BotCommand, Update
from telegram.error import InvalidToken, NetworkError, TelegramError
from telegram.ext import Application, ApplicationBuilder

from app.bot.handlers import BotHandlers
from app.config import ConfigurationError, Settings
from app.market_store import CLEAN_DIR, MarketStore, previous_weekday
from app.providers import MockSignalProvider, SignalProvider, StrategySignalProvider
from app.providers.base import ProviderError
from app.strategy_engine import VietcapStrategyEngine
from app.services import NotificationService, SignalService, SubscriptionService
from app.storage import Database, NotificationRepository, SettingsRepository, UserRepository, WatchlistRepository


logger = logging.getLogger(__name__)

TOKEN_PATTERN = re.compile(r"(?i)(?:bot)?\d{6,}:[A-Za-z0-9_-]{20,}")


def redact_secrets(value: str) -> str:
    return TOKEN_PATTERN.sub("bot<redacted>", value)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_secrets(super().format(record))

    def formatException(self, exc_info) -> str:
        return redact_secrets(super().formatException(exc_info))


class ContextDefaultsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "ticker"):
            record.ticker = "-"
        if not hasattr(record, "chat_id"):
            record.chat_id = "-"
        return True


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(ContextDefaultsFilter())
    handler.setFormatter(
        RedactingFormatter(
            "%(asctime)s level=%(levelname)s module=%(name)s ticker=%(ticker)s chat_id=%(chat_id)s message=%(message)s"
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # httpx logs complete Telegram request URLs, which contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def create_provider(name: str, database_path=None) -> SignalProvider:
    if name == "mock":
        return MockSignalProvider()
    if name == "strategy":
        return StrategySignalProvider(database_path=database_path)
    raise ConfigurationError(f"Signal provider không hỗ trợ: {name}")


@dataclass(slots=True)
class Runtime:
    database: Database
    signal_service: SignalService
    subscriptions: SubscriptionService
    notifications: NotificationService


def build_runtime(settings: Settings) -> Runtime:
    database = Database(settings.database_path)
    database.initialize()
    users = UserRepository(database)
    watchlists = WatchlistRepository(database)
    user_settings = SettingsRepository(database)
    notification_repository = NotificationRepository(database)
    signal_service = SignalService(create_provider(settings.signal_provider, settings.database_path))
    subscriptions = SubscriptionService(users, watchlists, user_settings)
    notifications = NotificationService(
        signal_service,
        subscriptions,
        notification_repository,
        settings.timezone,
    )
    return Runtime(database, signal_service, subscriptions, notifications)


async def _alert_loop(application: Application, runtime: Runtime, interval_seconds: int) -> None:
    async def sender(chat_id: int, text: str) -> None:
        await application.bot.send_message(chat_id=chat_id, text=text)

    while True:
        await runtime.notifications.send_pending_alerts(sender)
        await asyncio.sleep(interval_seconds)


async def _eod_refresh_loop(provider: StrategySignalProvider) -> None:
    """Fill the previous weekday in the background; /scan itself stays network-free."""
    store = provider.engine.store
    while True:
        today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
        expected = previous_weekday(today).isoformat()
        delay = 3600
        try:
            if store.last_eod_refresh_date() != expected or store.last_scan_session(today) != expected:
                logger.info("Refreshing finalized EOD market data for %s", expected)

                def refresh_locked() -> dict:
                    with provider._refresh_lock:
                        return store.refresh(include_fundamentals=False)

                await asyncio.to_thread(refresh_locked)
                if store.last_scan_session(today) != expected or store.last_eod_refresh_date() != expected:
                    raise RuntimeError("Nguồn dự phòng chưa xác nhận đủ phiên cuối ngày")
                provider._scan_revision += 1
                provider._scan_cache = None
                logger.info("EOD market data ready for %s", expected)
        except Exception as exc:
            logger.warning("EOD refresh for %s failed; will retry: %s", expected, exc, exc_info=True)
            delay = 4 * 3600
        await asyncio.sleep(delay)


def build_application(settings: Settings, runtime: Runtime | None = None) -> Application:
    runtime = runtime or build_runtime(settings)

    async def post_init(application: Application) -> None:
        async def update_command_menu() -> None:
            commands = [
                BotCommand("start", "Giới thiệu bot"),
                BotCommand("analyze", "Phân tích một mã"),
                BotCommand("soi", "Phân tích một mã (lệnh quen dùng)"),
                BotCommand("chart", "Xem biểu đồ một mã"),
                BotCommand("scan", "Quét thị trường"),
                BotCommand("portfolio", "Xem danh mục quan tâm"),
                BotCommand("modelportfolio", "Xem danh mục mô phỏng"),
                BotCommand("paper", "Tài khoản giao dịch ảo"),
                BotCommand("paperbuy", "Đặt mua cổ phiếu ảo"),
                BotCommand("papersell", "Đặt bán cổ phiếu ảo"),
                BotCommand("paperorders", "Xem lệnh ảo đang chờ"),
                BotCommand("paperhistory", "Lịch sử lệnh ảo"),
                BotCommand("papercancel", "Hủy lệnh ảo đang chờ"),
                BotCommand("add", "Thêm mã vào danh mục"),
                BotCommand("remove", "Xóa mã khỏi danh mục"),
                BotCommand("alert", "Bật hoặc tắt cảnh báo"),
                BotCommand("status", "Trạng thái hệ thống"),
                BotCommand("help", "Hướng dẫn sử dụng"),
            ]
            try:
                await application.bot.set_my_commands(commands)
            except TelegramError as exc:
                logger.warning("Telegram command menu could not be updated: %s", exc)

        application.bot_data["command_menu_task"] = asyncio.create_task(
            update_command_menu(), name="update-command-menu")
        if settings.signal_provider == "strategy":
            async def startup_scan() -> None:
                try:
                    signals = await runtime.signal_service.scan()
                    application.bot_data["startup_scan_count"] = len(signals)
                    logger.info("Startup market scan evaluated %s symbols", len(signals))
                except ProviderError as exc:
                    logger.info("Startup scan waits for EOD data: %s", exc)
            # post_init runs before Application.start(); manage these background
            # tasks ourselves instead of calling Application.create_task early.
            application.bot_data["startup_scan_task"] = asyncio.create_task(
                startup_scan(), name="startup-market-scan"
            )
            provider = getattr(runtime.signal_service, "provider", None)
            if (isinstance(provider, StrategySignalProvider)
                    and isinstance(provider.engine, VietcapStrategyEngine)):
                application.bot_data["eod_refresh_task"] = asyncio.create_task(
                    _eod_refresh_loop(provider), name="eod-market-refresh"
                )
        if settings.enable_mock_alert_scheduler:
            application.bot_data["mock_alert_task"] = asyncio.create_task(
                _alert_loop(application, runtime, settings.mock_alert_interval_seconds),
                name="mock-alert-loop",
            )
            logger.info("Mock alert scheduler enabled")

    async def post_shutdown(application: Application) -> None:
        for key in ("mock_alert_task", "startup_scan_task", "command_menu_task", "eod_refresh_task"):
            task = application.bot_data.get(key)
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .concurrent_updates(4)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    BotHandlers(
        settings,
        runtime.signal_service,
        runtime.subscriptions,
        runtime.database,
    ).register(application)
    application.bot_data["runtime"] = runtime
    return application


async def run_alert_once(settings: Settings) -> int:
    runtime = build_runtime(settings)
    application = build_application(settings, runtime)

    async with application.bot:
        async def sender(chat_id: int, text: str) -> None:
            await application.bot.send_message(chat_id=chat_id, text=text)

        return await runtime.notifications.send_pending_alerts(sender)


async def check_telegram_token(settings: Settings) -> str:
    """Validate credentials with Telegram without printing the token."""
    async with Bot(settings.telegram_bot_token) as bot:
        identity = await bot.get_me()
    return identity.username or str(identity.id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Vietnam stock signal Telegram bot")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--check-token", action="store_true")
    parser.add_argument("--init-db", action="store_true")
    parser.add_argument("--import-market-data", action="store_true")
    parser.add_argument("--run-alert-once", action="store_true")
    return parser.parse_args()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    try:
        settings = Settings.from_env(args.env_file, require_token=not (args.init_db or args.import_market_data))
        configure_logging(settings.log_level)
        if args.import_market_data:
            market = MarketStore(settings.database_path)
            imported = market.import_directory(CLEAN_DIR)
            print(f"Market database: {market.path}; imported={imported}")
            return 0
        if args.init_db:
            runtime = build_runtime(settings)
            MarketStore(settings.database_path).initialize()
            print(f"Database initialized: {runtime.database.path}")
            return 0
        if args.check_config:
            runtime = build_runtime(settings)
            print(
                f"Local configuration OK: provider={settings.signal_provider}, "
                f"database={'OK' if runtime.database.healthcheck() else 'ERROR'}"
            )
            print("Token mới chỉ được kiểm tra định dạng. Dùng --check-token để xác thực với Telegram.")
            return 0
        if args.check_token:
            username = asyncio.run(check_telegram_token(settings))
            print(f"Telegram token OK: @{username}")
            return 0
        if args.run_alert_once:
            sent = asyncio.run(run_alert_once(settings))
            print(f"Notifications sent: {sent}")
            return 0
        application = build_application(settings)
        logger.info("Starting Telegram polling with provider=%s", settings.signal_provider)
        application.run_polling(allowed_updates=Update.ALL_TYPES)
        return 0
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}")
        return 2
    except InvalidToken:
        print(
            "Telegram authentication error: token bị Telegram từ chối. "
            "Hãy tạo token mới trong BotFather, cập nhật .env và xóa biến môi trường token cũ."
        )
        return 3
    except NetworkError as exc:
        print(f"Telegram network error: {redact_secrets(str(exc)) or 'không thể kết nối Telegram'}")
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
