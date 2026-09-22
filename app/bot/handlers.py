from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, LinkPreviewOptions, Update
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.bot.formatter import (
    format_add_result,
    format_alert_state,
    format_delete_result,
    format_help,
    format_limit_notice,
    format_lookup_error,
    format_lookup_results,
    format_plain_text_help,
    format_scan,
    format_start,
    format_status,
    format_stock_overview,
    format_usage,
    format_watchlist,
)
from app.bot.validators import is_valid_ticker_format, looks_like_ticker_input, normalize_ticker, parse_tickers
from app.config import Settings
from app.services import SignalService, SubscriptionService
from app.services.signal_service import SignalLookup
from app.storage import Database
from app.cafef_news import fetch_company_news
from app.dnse_market_data import DNSEMarketClient
from app.market_store import BOT_DIR
from app.providers.strategy_signal_provider import StrategySignalProvider
from app.providers.base import ProviderError, TickerNotFoundError
from app.stock_chart import render_money_flow_chart, render_stock_chart
from app.backtest_v1 import BacktestConfig
from app.model_portfolio import ModelPortfolio, format_model_portfolio, propose_portfolio, render_model_portfolio
from app.paper_broker import PaperBroker, PaperOrderError, format_paper_account, format_paper_orders


logger = logging.getLogger(__name__)


class BotHandlers:
    def __init__(
        self,
        settings: Settings,
        signal_service: SignalService,
        subscriptions: SubscriptionService,
        database: Database,
    ):
        self.settings = settings
        self.signal_service = signal_service
        self.subscriptions = subscriptions
        self.database = database

    async def _ensure_user(self, update: Update) -> int:
        chat = update.effective_chat
        user = update.effective_user
        if chat is None:
            raise RuntimeError("Update không có effective_chat")
        await self.subscriptions.ensure_user(chat.id, user.username if user else None)
        return chat.id

    @staticmethod
    async def _reply(update: Update, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
        if not update.effective_message:
            return
        for attempt in range(3):
            try:
                await update.effective_message.reply_text(
                    text, reply_markup=reply_markup,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
                return
            except NetworkError:
                if attempt == 2:
                    raise
                logger.warning(
                    "Transient Telegram network error; retrying reply",
                    extra={"chat_id": getattr(update.effective_chat, "id", None)},
                )
                await asyncio.sleep(0.5 * (2**attempt))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_user(update)
        await self._reply(update, format_start())

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_user(update)
        await self._reply(update, format_help())

    async def soi(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_user(update)
        command_text = getattr(update.effective_message, "text", "") or ""
        command_name = "analyze" if command_text.lower().startswith("/analyze") else "soi"
        if not context.args:
            await self._reply(update, format_usage(command_name, "<MÃ>"))
            return
        ticker = normalize_ticker(context.args[0])
        if len(context.args) != 1 or not is_valid_ticker_format(ticker):
            await self._reply(update, format_usage(command_name, "<MÃ>"))
            return
        lookup = await self.signal_service.lookup(ticker)
        await self._send_stock_lookup(update, lookup)

    async def _send_stock_lookup(self, update: Update, lookup: SignalLookup) -> None:
        ticker = lookup.ticker
        provider = self.signal_service.provider
        if lookup.signal and isinstance(provider, StrategySignalProvider) and hasattr(provider.engine, "store"):
            store = provider.engine.store
            overview = await asyncio.to_thread(store.stock_overview,
                                               ticker, lookup.signal.data_time.date().isoformat())
            reference = (overview.get("quote") or {}).get("close")
            live_result, news_result = await asyncio.gather(
                asyncio.to_thread(lambda: DNSEMarketClient(BOT_DIR / ".env").get_latest_trade(ticker, reference)),
                asyncio.to_thread(fetch_company_news, ticker, 2),
                return_exceptions=True,
            )
            if isinstance(live_result, Exception):
                logger.warning("DNSE latest trade unavailable for %s: %s", ticker, live_result)
                live_trade = None
            else:
                live_trade = live_result
                stored_day = (overview.get("quote") or {}).get("trade_date")
                live_day = live_trade["time"].date().isoformat()
                if stored_day and live_day < stored_day:
                    live_trade = None
                else:
                    live_trade["reference_close"] = await asyncio.to_thread(
                        store.close_before, ticker, live_day)
            if isinstance(news_result, Exception):
                logger.warning("CafeF news unavailable for %s: %s", ticker, news_result)
                news = []
            else:
                news = news_result
            await self._reply(update, format_stock_overview(lookup.signal, overview, news, live_trade),
                              InlineKeyboardMarkup([[
                                  InlineKeyboardButton("📈 Biểu đồ giá", callback_data=f"chart:{ticker}"),
                                  InlineKeyboardButton("💧 Dòng tiền", callback_data=f"flow:{ticker}"),
                              ]]))
        else:
            await self._reply(update, format_lookup_results([lookup], self.settings.timezone))

    async def chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        try:
            await query.answer()
        except TelegramError as exc:
            logger.warning("Telegram callback acknowledgment failed; trying chart delivery: %s", exc)
        callback = query.data or ""
        money_flow = callback.startswith("flow:")
        ticker = callback.split(":", 1)[-1]
        await self._send_chart(update, context, ticker, money_flow=money_flow)

    async def chart_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_user(update)
        if len(context.args) != 1 or not is_valid_ticker_format(context.args[0]):
            await self._reply(update, format_usage("chart", "<MÃ>"))
            return
        await self._send_chart(update, context, normalize_ticker(context.args[0]))

    async def _send_chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE, ticker: str,
                          money_flow: bool = False) -> None:
        if not is_valid_ticker_format(ticker):
            await self._reply(update, "Mã cổ phiếu không hợp lệ.")
            return
        provider = self.signal_service.provider
        if not isinstance(provider, StrategySignalProvider) or not hasattr(provider.engine, "store"):
            await self._reply(update, "Biểu đồ chỉ có khi dùng dữ liệu chiến lược đã lưu.")
            return
        if not await self.signal_service.ticker_exists(ticker):
            await self._reply(update, "Không tìm thấy mã trong danh sách cổ phiếu.")
            return
        try:
            render = render_money_flow_chart if money_flow else render_stock_chart
            image = await asyncio.to_thread(render, provider.engine.store, ticker)
        except (ValueError, ImportError, OSError) as exc:
            logger.warning("Chart rendering unavailable for %s: %s", ticker, exc)
            await self._reply(update, f"Chưa thể vẽ biểu đồ {ticker}: {exc}")
            return
        chat = update.effective_chat
        if chat is None:
            return
        content = image.getvalue()
        caption = (f"💧 {ticker} — CMF(20) từ OHLCV cuối ngày; chỉ báo áp lực mua/bán, "
                   "không phải dòng tiền giao dịch thực tế."
                   if money_flow else
                   f"📈 {ticker} — tối đa 252 phiên cuối ngày; giá thô, không phải giá điều chỉnh.")
        filename = f"{ticker}_cmf20.png" if money_flow else f"{ticker}.png"
        for attempt in range(2):
            try:
                await context.bot.send_photo(
                    chat_id=chat.id, photo=InputFile(content, filename=filename),
                    caption=caption, write_timeout=30,
                )
                return
            except BadRequest as exc:
                logger.warning("Telegram rejected chart photo for %s; sending PNG document: %s", ticker, exc)
                await context.bot.send_document(
                    chat_id=chat.id, document=InputFile(content, filename=filename),
                    caption=caption, write_timeout=30,
                )
                return
            except NetworkError:
                if attempt:
                    raise
                await asyncio.sleep(0.5)

    async def scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_user(update)
        try:
            signals = await self.signal_service.scan()
        except ProviderError as exc:
            await self._reply(update, f"Chưa thể quét thị trường: {exc}. Bot đang cập nhật dữ liệu cuối ngày.")
            return
        provider = self.signal_service.provider
        await self._reply(update, format_scan(
            signals, self.settings.timezone,
            getattr(provider, "last_breadth", None), getattr(provider, "last_refresh_error", None),
            getattr(provider, "last_scan_date", None),
        ))

    async def watchlist(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = await self._ensure_user(update)
        tickers = (await self.subscriptions.list(chat_id))[:10]
        provider = self.signal_service.provider
        if isinstance(provider, StrategySignalProvider):
            async def cached_lookup(ticker: str) -> SignalLookup:
                try:
                    return SignalLookup(ticker, await provider.get_saved_signal(ticker))
                except TickerNotFoundError:
                    return SignalLookup(ticker, error_code="not_found")
                except ProviderError as exc:
                    logger.warning("Stored watchlist signal unavailable for %s: %s", ticker, exc)
                    return SignalLookup(ticker, error_code="provider_error")

            lookups = await asyncio.gather(*(cached_lookup(ticker) for ticker in tickers))
        else:
            lookups = await self.signal_service.lookup_many(tickers)
        if isinstance(provider, StrategySignalProvider) and hasattr(provider.engine, "store"):
            async def stored_overview(lookup: SignalLookup) -> dict | None:
                try:
                    signal_day = lookup.signal.data_time.date().isoformat() if lookup.signal else None
                    return await asyncio.to_thread(
                        provider.engine.store.stock_overview, lookup.ticker, signal_day)
                except (OSError, ValueError) as exc:
                    logger.warning("Stored watchlist price unavailable for %s: %s", lookup.ticker, exc)
                    return None

            overviews = await asyncio.gather(*(stored_overview(lookup) for lookup in lookups))
        else:
            overviews = [None] * len(lookups)
        items = [(lookup.ticker, lookup.signal, lookup.error_code, overview)
                 for lookup, overview in zip(lookups, overviews)]
        await self._reply(update, format_watchlist(items))

    async def model_portfolio(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Illustrate an allocation from saved prior-session signals, without trading."""
        await self._ensure_user(update)
        provider = self.signal_service.provider
        if not isinstance(provider, StrategySignalProvider) or not hasattr(provider.engine, "store"):
            await self._reply(update, "Danh mục mô phỏng chỉ hỗ trợ dữ liệu chiến lược đã lưu.")
            return
        try:
            cfg = await asyncio.to_thread(BacktestConfig.from_json, BOT_DIR / "backtest_config.json")
            sectors = await asyncio.to_thread(provider.engine._sector_map)
            if not sectors:
                session = await asyncio.to_thread(
                    provider.engine.store.last_scan_session, provider._current_time().date())
                if not session:
                    await self._reply(update, "Chưa có phiên cuối ngày hợp lệ để lập danh mục mô phỏng. Xem /status.")
                    return
                result = ModelPortfolio(session, cfg.initial_capital, (), cfg.initial_capital,
                                        None, 0, 0, 0,
                                        "Thiếu industry_map.csv: chưa kiểm tra được giới hạn 35%/ngành; "
                                        "không quét toàn thị trường để tránh chờ lâu.")
            else:
                signals = await self.signal_service.scan()
                session = provider.last_scan_date
                if not signals or not session:
                    await self._reply(update, "Chưa có phiên cuối ngày hợp lệ để lập danh mục mô phỏng. Xem /status.")
                    return
                result = await asyncio.to_thread(propose_portfolio, signals, provider.engine, cfg, session)
            content = (await asyncio.to_thread(render_model_portfolio, result)).getvalue()
        except (ValueError, OSError, ImportError) as exc:
            logger.exception("Model portfolio could not be built: %s", exc)
            await self._reply(update, "Chưa thể lập danh mục mô phỏng từ dữ liệu hiện có. Xem log để biết chi tiết.")
            return
        caption = format_model_portfolio(result)
        chat = update.effective_chat
        if chat is None:
            return
        try:
            await context.bot.send_photo(chat_id=chat.id,
                                         photo=InputFile(content, filename="model_portfolio.png"),
                                         caption=caption, write_timeout=30)
        except BadRequest:
            await context.bot.send_document(chat_id=chat.id,
                                            document=InputFile(content, filename="model_portfolio.png"),
                                            caption=caption, write_timeout=30)

    async def _paper_broker(self, update: Update) -> tuple[int, PaperBroker] | None:
        if getattr(update.effective_chat, "type", "private") != "private":
            await self._reply(update, "Tài khoản giao dịch ảo chỉ dùng trong chat riêng với bot.")
            return None
        chat_id = await self._ensure_user(update)
        provider = self.signal_service.provider
        if not isinstance(provider, StrategySignalProvider) or not hasattr(provider.engine, "store"):
            await self._reply(update, "Tài khoản mô phỏng cần SIGNAL_PROVIDER=strategy và dữ liệu giá đã lưu.")
            return None
        cfg = await asyncio.to_thread(BacktestConfig.from_json, BOT_DIR / "backtest_config.json")
        return chat_id, PaperBroker(self.database, cfg)

    async def paper(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        prepared = await self._paper_broker(update)
        if prepared is None:
            return
        chat_id, broker = prepared
        await asyncio.to_thread(broker.ensure_account, chat_id)
        await asyncio.to_thread(broker.process, chat_id)
        snapshot = await asyncio.to_thread(broker.snapshot, chat_id)
        buttons = InlineKeyboardMarkup([[InlineKeyboardButton("📋 Lệnh chờ", callback_data="paper:orders"),
                                         InlineKeyboardButton("📜 Lịch sử", callback_data="paper:history")]])
        await self._reply(update, format_paper_account(snapshot), buttons)

    async def _paper_place(self, update: Update, context: ContextTypes.DEFAULT_TYPE, side: str) -> None:
        prepared = await self._paper_broker(update)
        if prepared is None:
            return
        chat_id, broker = prepared
        command = "paperbuy" if side == "BUY" else "papersell"
        if (len(context.args) != 2 or not context.args[1].isascii()
                or not context.args[1].isdecimal() or len(context.args[1]) > 9):
            await self._reply(update, format_usage(command, "<MÃ> <SỐ_CP>"))
            return
        await asyncio.to_thread(broker.ensure_account, chat_id)
        await asyncio.to_thread(broker.process, chat_id)
        try:
            order_id = await asyncio.to_thread(broker.place, chat_id, context.args[0], side, int(context.args[1]))
        except PaperOrderError as exc:
            await self._reply(update, f"Không nhận lệnh mô phỏng: {exc}")
            return
        await self._reply(update, f"Đã xếp lệnh ảo #{order_id}: {side} {normalize_ticker(context.args[0])} "
                          f"{int(context.args[1]):,} cp. Chưa khớp, chưa thay đổi số dư. "
                          "Bot sẽ đối chiếu Open của phiên hợp lệ sau ngày đặt lệnh khi bạn mở /paper hoặc /paperorders. "
                          "Có thể hủy bằng /papercancel <ID>.")

    async def paper_buy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._paper_place(update, context, "BUY")

    async def paper_sell(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._paper_place(update, context, "SELL")

    async def _paper_orders(self, update: Update, pending_only: bool) -> None:
        prepared = await self._paper_broker(update)
        if prepared is None:
            return
        chat_id, broker = prepared
        await asyncio.to_thread(broker.ensure_account, chat_id)
        await asyncio.to_thread(broker.process, chat_id)
        orders = await asyncio.to_thread(broker.orders, chat_id, 10, pending_only)
        await self._reply(update, format_paper_orders(orders, pending_only))

    async def paper_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._paper_orders(update, True)

    async def paper_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._paper_orders(update, False)

    async def paper_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        prepared = await self._paper_broker(update)
        if prepared is None:
            return
        chat_id, broker = prepared
        if len(context.args) != 1 or not context.args[0].isdigit():
            await self._reply(update, format_usage("papercancel", "<ID>"))
            return
        await asyncio.to_thread(broker.process, chat_id)
        canceled = await asyncio.to_thread(broker.cancel, chat_id, int(context.args[0]))
        await self._reply(update, "Đã hủy lệnh ảo đang chờ." if canceled else
                          "Không tìm thấy lệnh chờ này hoặc lệnh đã được xử lý.")

    async def paper_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        try:
            await query.answer()
        except TelegramError as exc:
            logger.warning("Paper callback acknowledgment failed: %s", exc)
        await self._paper_orders(update, query.data == "paper:orders")

    async def add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = await self._ensure_user(update)
        if not context.args:
            await self._reply(update, format_usage("add", "<MÃ> <MÃ>"))
            return
        parsed = parse_tickers(" ".join(context.args))
        supported: list[str] = []
        unsupported = list(parsed.invalid)
        for ticker in parsed.tickers:
            if await self.signal_service.ticker_exists(ticker):
                supported.append(ticker)
            else:
                unsupported.append(ticker)
        result = await self.subscriptions.add(chat_id, supported)
        await self._reply(
            update,
            format_add_result(result.added, result.existing, unsupported, result.full),
        )

    async def delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = await self._ensure_user(update)
        if not context.args:
            await self._reply(update, format_usage("remove", "<MÃ> <MÃ>"))
            return
        parsed = parse_tickers(" ".join(context.args))
        removed, missing = await self.subscriptions.remove(chat_id, parsed.tickers)
        await self._reply(update, format_delete_result(removed, missing, parsed.invalid))

    async def alert(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = await self._ensure_user(update)
        if len(context.args) != 1 or context.args[0].lower() not in {"on", "off"}:
            await self._reply(update, format_usage("alert", "on hoặc /alert off"))
            return
        enabled = context.args[0].lower() == "on"
        await self.subscriptions.set_alerts(chat_id, enabled)
        await self._reply(update, format_alert_state(enabled))

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_user(update)
        database_ok = await asyncio.to_thread(self.database.healthcheck)
        now = datetime.now(ZoneInfo(self.settings.timezone))
        await self._reply(
            update,
            format_status(
                self.signal_service.provider.name,
                database_ok,
                now,
                self.signal_service.provider.latest_data_time(),
            ),
        )

    async def text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_user(update)
        text = update.effective_message.text if update.effective_message else ""
        if not looks_like_ticker_input(text):
            await self._reply(update, format_plain_text_help())
            return
        parsed = parse_tickers(text, self.settings.max_tickers_per_message)
        if len(parsed.tickers) == 1 and not parsed.invalid and not parsed.truncated:
            lookup = await self.signal_service.lookup(parsed.tickers[0])
            await self._send_stock_lookup(update, lookup)
            return
        results = await self.signal_service.lookup_many(parsed.tickers)
        message = format_lookup_results(results, self.settings.timezone)
        if parsed.truncated:
            message = f"{message}\n\n{format_limit_notice(parsed.truncated)}"
        await self._reply(update, message)

    async def error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = getattr(getattr(update, "effective_chat", None), "id", None)
        error_id = secrets.token_hex(4)
        error = context.error
        exc_info = (
            (type(error), error, error.__traceback__)
            if isinstance(error, BaseException)
            else None
        )
        logger.error(
            "Unhandled Telegram handler error id=%s",
            error_id,
            exc_info=exc_info,
            extra={"chat_id": chat_id},
        )
        message = getattr(update, "effective_message", None)
        if message:
            if isinstance(error, NetworkError):
                user_message = "Kết nối Telegram tạm thời gián đoạn. Vui lòng thử lại sau vài giây."
            else:
                user_message = f"Đã có lỗi ngoài dự kiến. Mã lỗi: {error_id}. Vui lòng thử lại sau."
            try:
                await message.reply_text(user_message)
            except TelegramError:
                logger.warning(
                    "Could not deliver error message id=%s",
                    error_id,
                    extra={"chat_id": chat_id},
                )

    def register(self, application: Application) -> None:
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.help))
        application.add_handler(CommandHandler(["analyze", "soi"], self.soi))
        application.add_handler(CommandHandler("chart", self.chart_command))
        application.add_handler(CallbackQueryHandler(self.chart, pattern=r"^(chart|flow):[A-Z0-9]{1,10}$"))
        application.add_handler(CommandHandler("scan", self.scan))
        application.add_handler(CommandHandler(["portfolio", "danhmuc"], self.watchlist))
        application.add_handler(CommandHandler("modelportfolio", self.model_portfolio))
        application.add_handler(CommandHandler("paper", self.paper))
        application.add_handler(CommandHandler("paperbuy", self.paper_buy))
        application.add_handler(CommandHandler("papersell", self.paper_sell))
        application.add_handler(CommandHandler("paperorders", self.paper_orders))
        application.add_handler(CommandHandler("paperhistory", self.paper_history))
        application.add_handler(CommandHandler("papercancel", self.paper_cancel))
        application.add_handler(CallbackQueryHandler(self.paper_callback, pattern=r"^paper:(orders|history)$"))
        application.add_handler(CommandHandler("add", self.add))
        application.add_handler(CommandHandler(["remove", "del"], self.delete))
        application.add_handler(CommandHandler("alert", self.alert))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.text))
        application.add_error_handler(self.error)
