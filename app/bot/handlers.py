from __future__ import annotations

import asyncio
import logging
import secrets
from contextvars import ContextVar
from datetime import datetime
from difflib import get_close_matches
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
from app.dnse_market_data import DNSEDataError, DNSEMarketClient
from app.market_store import BOT_DIR
from app.trading_calendar import previous_trading_day
from app.providers.strategy_signal_provider import StrategySignalProvider
from app.providers.base import ProviderError, TickerNotFoundError
from app.stock_chart import render_money_flow_chart, render_stock_chart
from app.backtest_v1 import BacktestConfig
from app.model_portfolio import ModelPortfolio, format_model_portfolio, propose_portfolio, render_model_portfolio
from app.paper_broker import (PaperBroker, PaperOrderError, format_paper_account,
                              format_paper_orders, render_paper_portfolio)


logger = logging.getLogger(__name__)
_waiting_message: ContextVar[object | None] = ContextVar("waiting_message", default=None)

_COMMAND_ALIASES = {
    "analyze": "analyze", "soi": "soi", "chart": "chart",
    "add": "add", "remove": "remove", "del": "remove",
    "papercapital": "papercapital", "paperposition": "paperposition",
    "paperbuy": "paperbuy", "buy": "paperbuy", "mua": "paperbuy",
    "papersell": "papersell", "sell": "papersell", "ban": "papersell",
    "papercancel": "papercancel", "alert": "alert", "digest": "digest",
}
_COMMAND_ARGUMENTS = {
    "analyze": "<MÃ>", "soi": "<MÃ>", "chart": "<MÃ>",
    "add": "<MÃ> [MÃ...]", "remove": "<MÃ> [MÃ...]",
    "papercapital": "<VỐN_VNĐ>",
    "paperposition": "<MÃ> <SỐ_CP> <GIÁ_VỐN_VNĐ>",
    "paperbuy": "<MÃ> <SỐ_CP>", "papersell": "<MÃ> <SỐ_CP>",
    "papercancel": "<ID hoặc MÃ>", "alert": "on hoặc /alert off",
    "digest": "on hoặc /digest off",
}


def _invalid_manual_command_guidance(text: str) -> str | None:
    """Return usage only when a known slash command has invalid arguments."""
    parts = text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return None
    typed = parts[0][1:].split("@", 1)[0].lower()
    command = _COMMAND_ALIASES.get(typed)
    if command is None:
        return None
    args = parts[1:]
    if command in {"analyze", "soi", "chart"}:
        valid = len(args) == 1 and is_valid_ticker_format(args[0])
    elif command in {"add", "remove"}:
        parsed = parse_tickers(" ".join(args))
        valid = bool(args) and bool(parsed.tickers) and not parsed.invalid
    elif command == "papercapital":
        valid = len(args) == 1 and args[0].isascii() and args[0].isdecimal()
    elif command == "paperposition":
        valid = (len(args) == 3 and is_valid_ticker_format(args[0])
                 and all(value.isascii() and value.isdecimal() for value in args[1:]))
    elif command in {"paperbuy", "papersell"}:
        valid = (len(args) == 2 and is_valid_ticker_format(args[0])
                 and args[1].isascii() and args[1].isdecimal())
    elif command == "papercancel":
        valid = (len(args) == 1 and
                 ((args[0].isascii() and args[0].isdecimal())
                  or is_valid_ticker_format(args[0])))
    else:
        valid = len(args) == 1 and args[0].lower() in {"on", "off"}
    return None if valid else format_usage(command, _COMMAND_ARGUMENTS[command])


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
        waiting = _waiting_message.get()
        for attempt in range(3):
            try:
                options = {"reply_markup": reply_markup,
                           "link_preview_options": LinkPreviewOptions(is_disabled=True)}
                if waiting is not None:
                    await waiting.edit_text(text, **options)
                    _waiting_message.set(None)
                else:
                    await update.effective_message.reply_text(text, **options)
                return
            except BadRequest:
                if waiting is None:
                    raise
                waiting = None
            except NetworkError:
                if attempt == 2:
                    raise
                logger.warning(
                    "Transient Telegram network error; retrying reply",
                    extra={"chat_id": getattr(update.effective_chat, "id", None)},
                )
                await asyncio.sleep(0.5 * (2**attempt))

    @staticmethod
    def _with_waiting(handler):
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            message = update.effective_message
            if message is None:
                await handler(update, context)
                return
            waiting = await message.reply_text("Vui lòng chờ phản hồi")
            token = _waiting_message.set(waiting)
            try:
                await handler(update, context)
            finally:
                remaining = _waiting_message.get()
                if remaining is not None:
                    try:
                        await remaining.delete()
                    except TelegramError:
                        logger.warning("Could not remove waiting message")
                _waiting_message.reset(token)
        return wrapped

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_user(update)
        await self._reply(update, format_start())

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_user(update)
        await self._reply(update, format_help())

    async def unknown_command(self, update: Update,
                              context: ContextTypes.DEFAULT_TYPE) -> None:
        """Never leave a manually typed slash command without a response."""
        await self._ensure_user(update)
        raw = (getattr(update.effective_message, "text", "") or "").split(maxsplit=1)[0]
        command = raw.split("@", 1)[0]
        typed = command.removeprefix("/").lower()
        matches = get_close_matches(typed, _COMMAND_ALIASES, n=1, cutoff=0.55)
        suggestion = ""
        if matches:
            canonical = _COMMAND_ALIASES[matches[0]]
            suggestion = (f"\nCó phải bạn muốn dùng /{matches[0]}?\n"
                          f"{format_usage(canonical, _COMMAND_ARGUMENTS[canonical])}")
        await self._reply(
            update,
            f"Không nhận ra lệnh {command or 'này'}.{suggestion}\n"
            "Dùng /help để xem toàn bộ lệnh hợp lệ.",
        )

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
        lookup = await self.signal_service.lookup(ticker, saved_only=True)
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
                asyncio.wait_for(asyncio.to_thread(
                    lambda: DNSEMarketClient(BOT_DIR / ".env").get_latest_trade(ticker, reference)), 8),
                asyncio.wait_for(asyncio.to_thread(fetch_company_news, ticker, 2), 6),
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
                              ], [
                                  InlineKeyboardButton("🟢 Mua ảo", callback_data=f"trade:buy:{ticker}"),
                                  InlineKeyboardButton("🔴 Bán ảo", callback_data=f"trade:sell:{ticker}"),
                              ], [
                                  InlineKeyboardButton("🧪 Danh mục ảo", callback_data="paper:account"),
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
            signals = await self.signal_service.scan(
                allow_previous_session=True, vn100_only=True)
        except ProviderError as exc:
            await self._reply(update, f"Chưa thể quét thị trường: {exc}. Bot đang cập nhật dữ liệu cuối ngày.")
            return
        provider = self.signal_service.provider
        scan_date = getattr(provider, "last_scan_date", None)
        expected_scan_date = None
        if scan_date and isinstance(provider, StrategySignalProvider) and hasattr(provider.engine, "store"):
            expected_scan_date = previous_trading_day(provider._current_time().date()).isoformat()
        await self._reply(update, format_scan(
            signals, self.settings.timezone,
            getattr(provider, "last_breadth", None), getattr(provider, "last_refresh_error", None),
            scan_date, expected_scan_date=expected_scan_date,
            vn100_mode=True,
        ))
        if (signals and isinstance(provider, StrategySignalProvider)
                and getattr(update.effective_chat, "type", "private") == "private"
                and update.effective_message):
            actionable = [signal for signal in signals
                          if signal.action and signal.action.value in {"BUY", "SELL"}][:5]
            rows = [[
                InlineKeyboardButton(f"🔎 Soi {signal.ticker}",
                                     callback_data=f"lookup:{signal.ticker}"),
                InlineKeyboardButton(
                    f"{'🟢 Mua' if signal.action.value == 'BUY' else '🔴 Bán'} {signal.ticker}",
                    callback_data=f"trade:{signal.action.value.lower()}:{signal.ticker}"),
            ] for signal in actionable]
            if not rows:
                rows.append([
                    InlineKeyboardButton("🟢 Mua ảo", callback_data="scan:buy"),
                    InlineKeyboardButton("🔴 Bán ảo", callback_data="scan:sell"),
                ])
            rows.append([InlineKeyboardButton("🧪 Xem danh mục ảo",
                                              callback_data="paper:account")])
            buttons = InlineKeyboardMarkup(rows)
            await update.effective_message.reply_text(
                f"Bạn muốn mua/bán ảo mã nào? Tín hiệu quét thuộc phiên {scan_date or 'chưa xác định'}. "
                "Lệnh ảo sẽ khớp ngay theo giá API hiện hành; không đặt lệnh thật.", reply_markup=buttons)

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
        except (ProviderError, ValueError, OSError, ImportError) as exc:
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

    async def _send_paper_chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                snapshot: dict) -> None:
        bot = getattr(context, "bot", None)
        chat = update.effective_chat
        if bot is None or chat is None:
            return
        try:
            content = (await asyncio.to_thread(render_paper_portfolio, snapshot)).getvalue()
            caption = ("📊 Phân bổ tài khoản ảo theo giá Close cuối cùng đã lưu. "
                       "Không phải tài sản hay khớp lệnh tại sàn.")
            if snapshot["unpriced"]:
                caption += f" {snapshot['unpriced']} mã thiếu giá chưa được vẽ."
            try:
                await bot.send_photo(chat_id=chat.id, photo=InputFile(content, filename="paper_portfolio.png"),
                                     caption=caption, write_timeout=30)
            except BadRequest:
                await bot.send_document(chat_id=chat.id,
                                        document=InputFile(content, filename="paper_portfolio.png"),
                                        caption=caption, write_timeout=30)
        except (PaperOrderError, ImportError, OSError, TelegramError) as exc:
            logger.warning("Paper portfolio chart unavailable: %s", exc)

    async def paper(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        prepared = await self._paper_broker(update)
        if prepared is None:
            return
        chat_id, broker = prepared
        await asyncio.to_thread(broker.ensure_account, chat_id)
        await asyncio.to_thread(broker.process, chat_id)
        snapshot = await asyncio.to_thread(broker.snapshot, chat_id)
        buttons = InlineKeyboardMarkup([[InlineKeyboardButton("📋 Lệnh chờ", callback_data="paper:orders"),
                                         InlineKeyboardButton("📜 Lịch sử", callback_data="paper:history")],
                                        [InlineKeyboardButton("📊 Biểu đồ tròn", callback_data="paper:chart")]])
        await self._reply(update, format_paper_account(snapshot), buttons)
        await self._send_paper_chart(update, context, snapshot)

    async def paper_capital(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        prepared = await self._paper_broker(update)
        if prepared is None:
            return
        if (len(context.args) != 1 or not context.args[0].isascii()
                or not context.args[0].isdecimal() or len(context.args[0]) > 16):
            await self._reply(update, format_usage("papercapital", "<VỐN_VNĐ>"))
            return
        chat_id, broker = prepared
        await asyncio.to_thread(broker.ensure_account, chat_id)
        try:
            await asyncio.to_thread(broker.set_capital, chat_id, int(context.args[0]))
        except PaperOrderError as exc:
            await self._reply(update, f"Không đổi được vốn ảo: {exc}")
            return
        snapshot = await asyncio.to_thread(broker.snapshot, chat_id)
        await self._reply(update, f"Đã đặt vốn góp mô phỏng thành {snapshot['initial_cash']:,.0f} VNĐ. "
                          f"Tiền mặt hiện có: {snapshot['cash']:,.0f} VNĐ. Dùng /paper để xem biểu đồ.")

    async def paper_position(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        prepared = await self._paper_broker(update)
        if prepared is None:
            return
        if (len(context.args) != 3 or any(not value.isascii() or not value.isdecimal()
                                           or len(value) > 10 for value in context.args[1:])):
            await self._reply(update, format_usage("paperposition", "<MÃ> <SỐ_CP> <GIÁ_VỐN_VNĐ>"))
            return
        chat_id, broker = prepared
        await asyncio.to_thread(broker.ensure_account, chat_id)
        try:
            await asyncio.to_thread(broker.set_position, chat_id, context.args[0],
                                    int(context.args[1]), int(context.args[2]))
        except PaperOrderError as exc:
            await self._reply(update, f"Không chỉnh được vị thế ảo: {exc}")
            return
        await self._reply(update, f"Đã lưu vị thế ảo {normalize_ticker(context.args[0])}: "
                          f"{int(context.args[1]):,} cp, giá vốn {int(context.args[2]):,} VNĐ/cp. "
                          "Đây là số liệu nhập tay, không phải lệnh khớp tại sàn. Dùng /paper để xem danh mục.")

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
        ticker = normalize_ticker(context.args[0])
        signal = None
        provider = self.signal_service.provider
        if isinstance(provider, StrategySignalProvider):
            try:
                signal = await provider.get_saved_signal(ticker)
            except ProviderError as exc:
                logger.info("Paper order signal snapshot unavailable: %s", exc)
        try:
            reference = await asyncio.to_thread(broker.latest_reference_close, ticker)
            live_trade = await asyncio.wait_for(asyncio.to_thread(
                lambda: DNSEMarketClient(BOT_DIR / ".env").get_latest_trade(ticker, reference)), 8)
        except (DNSEDataError, asyncio.TimeoutError) as exc:
            logger.warning("Current API quote unavailable for paper order %s: %s", ticker, exc)
            await self._reply(update, f"Không khớp lệnh ảo {ticker}: chưa lấy được giá API hiện hành. "
                              "Vui lòng thử lại sau; bot không dùng giá cuối ngày cũ để khớp thay thế.")
            return
        try:
            event = await asyncio.to_thread(
                broker.execute_at_quote, chat_id, ticker, side, int(context.args[1]),
                live_trade["price_vnd"], live_trade["time"], live_trade.get("source", "DNSE"), signal)
        except PaperOrderError as exc:
            await self._reply(update, f"Không khớp lệnh mô phỏng: {exc}")
            return
        snapshot = await asyncio.to_thread(broker.snapshot, chat_id)
        pnl = (f" | PnL đã thực hiện {event['realized_pnl']:+,.0f} VNĐ"
               if event["realized_pnl"] is not None else "")
        tax = f" · Thuế: {event['tax']:,} VNĐ" if event["tax"] else ""
        await self._reply(
            update,
            f"✅ Đã khớp lệnh ảo #{event['id']}: {side} {ticker} {event['shares']:,} cp\n"
            f"Giá API: {event['fill_price']:,.0f} VNĐ · Nguồn: {event['source']}\n"
            f"Thời điểm giá: {event['fill_time']} · Phí: {event['fee']:,} VNĐ"
            f"{tax}{pnl}\n"
            "Danh mục ảo đã được cập nhật ngay; đây không phải lệnh gửi ra sàn.\n\n"
            f"{format_paper_account(snapshot)}",
        )

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
        buttons = None
        if pending_only and orders:
            buttons = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"❌ Hủy #{order['id']} · {order['ticker']}",
                    callback_data=f"paper:cancel:{order['id']}",
                )
            ] for order in orders])
        await self._reply(update, format_paper_orders(orders, pending_only), buttons)

    async def paper_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._paper_orders(update, True)

    async def paper_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._paper_orders(update, False)

    async def paper_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        prepared = await self._paper_broker(update)
        if prepared is None:
            return
        chat_id, broker = prepared
        if len(context.args) != 1:
            await self._reply(update, format_usage("papercancel", "<ID hoặc MÃ>"))
            return
        target = context.args[0].strip()
        try:
            if target.isdigit():
                count = int(await asyncio.to_thread(broker.cancel, chat_id, int(target)))
                label = f"lệnh #{target}"
            else:
                ticker = normalize_ticker(target)
                count = await asyncio.to_thread(broker.cancel_ticker, chat_id, ticker)
                label = f"{count} lệnh của {ticker}"
        except PaperOrderError as exc:
            await self._reply(update, f"Không hủy được lệnh chờ: {exc}")
            return
        await self._reply(update, f"Đã hủy {label}." if count else
                          "Không tìm thấy lệnh đang chờ hoặc lệnh đã được xử lý.")

    async def paper_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        try:
            await query.answer()
        except TelegramError as exc:
            logger.warning("Paper callback acknowledgment failed: %s", exc)
        if query.data == "paper:account":
            await self.paper(update, context)
        elif query.data == "paper:chart":
            prepared = await self._paper_broker(update)
            if prepared is not None:
                chat_id, broker = prepared
                await asyncio.to_thread(broker.ensure_account, chat_id)
                snapshot = await asyncio.to_thread(broker.snapshot, chat_id)
                await self._send_paper_chart(update, context, snapshot)
        elif query.data and query.data.startswith("paper:cancel:"):
            prepared = await self._paper_broker(update)
            if prepared is None:
                return
            chat_id, broker = prepared
            try:
                order_id = int(query.data.rsplit(":", 1)[1])
            except (TypeError, ValueError):
                await self._reply(update, "Nút hủy lệnh không hợp lệ.")
                return
            canceled = await asyncio.to_thread(broker.cancel, chat_id, order_id)
            orders = await asyncio.to_thread(broker.orders, chat_id, 10, True)
            buttons = (InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"❌ Hủy #{order['id']} · {order['ticker']}",
                    callback_data=f"paper:cancel:{order['id']}",
                )
            ] for order in orders]) if orders else None)
            message = (f"Đã hủy lệnh chờ #{order_id}.\n\n" if canceled else
                       f"Lệnh #{order_id} không còn chờ hoặc không thuộc tài khoản này.\n\n")
            await self._reply(update, message + format_paper_orders(orders, True), buttons)
        else:
            await self._paper_orders(update, query.data == "paper:orders")

    async def scan_trade_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        await query.answer()
        if getattr(update.effective_chat, "type", "private") != "private":
            await self._reply(update, "Hãy mở chat riêng với bot để dùng tài khoản ảo.")
            return
        side = "paperbuy" if query.data == "scan:buy" else "papersell"
        await self._reply(update, f"Nhập /{side} <MÃ> <SỐ_CP> để đặt lệnh ảo. "
                          "Ví dụ: /" + side + " FPT 100. Lệnh ảo sẽ khớp ngay theo "
                          "giá API hiện hành và không được gửi ra sàn.")

    async def stock_action_callback(self, update: Update,
                                    context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        try:
            await query.answer()
        except TelegramError as exc:
            logger.warning("Stock action callback acknowledgment failed: %s", exc)
        parts = (query.data or "").split(":")
        if len(parts) == 2 and parts[0] == "lookup":
            ticker = normalize_ticker(parts[1])
            if not is_valid_ticker_format(ticker):
                await self._reply(update, "Mã cổ phiếu không hợp lệ.")
                return
            await self._send_stock_lookup(
                update, await self.signal_service.lookup(ticker, saved_only=True))
            return
        if len(parts) != 3 or parts[0] != "trade" or parts[1] not in {"buy", "sell"}:
            await self._reply(update, "Thao tác không hợp lệ.")
            return
        ticker = normalize_ticker(parts[2])
        if not is_valid_ticker_format(ticker) or not await self.signal_service.ticker_exists(ticker):
            await self._reply(update, f"Không lấy được dữ liệu cho mã {ticker or parts[2]}.")
            return
        if getattr(update.effective_chat, "type", "private") != "private":
            await self._reply(update, "Hãy mở chat riêng với bot để dùng tài khoản ảo.")
            return
        command = "paperbuy" if parts[1] == "buy" else "papersell"
        await self._reply(update, f"Nhập /{command} {ticker} <SỐ_CP> để xác nhận. "
                          "Bot dùng giá API hiện hành, không tự chọn số lượng và không đặt lệnh thật.")

    async def navigation_callback(self, update: Update,
                                  context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        try:
            await query.answer()
        except TelegramError as exc:
            logger.warning("Navigation callback acknowledgment failed: %s", exc)
        if query.data == "nav:portfolio":
            await self.watchlist(update, context)
        elif query.data == "nav:modelportfolio":
            await self.model_portfolio(update, context)
        else:
            await self._reply(update, "Liên kết không hợp lệ.")

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
            InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 Đến danh mục", callback_data="nav:portfolio"),
                InlineKeyboardButton("📊 Phân bổ vốn", callback_data="nav:modelportfolio"),
            ]]),
        )

    async def delete(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = await self._ensure_user(update)
        if not context.args:
            await self._reply(update, format_usage("remove", "<MÃ> <MÃ>"))
            return
        parsed = parse_tickers(" ".join(context.args))
        removed, missing = await self.subscriptions.remove(chat_id, parsed.tickers)
        await self._reply(
            update,
            format_delete_result(removed, missing, parsed.invalid),
            InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 Đến danh mục", callback_data="nav:portfolio"),
                InlineKeyboardButton("📊 Phân bổ vốn", callback_data="nav:modelportfolio"),
            ]]),
        )

    async def alert(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = await self._ensure_user(update)
        if len(context.args) != 1 or context.args[0].lower() not in {"on", "off"}:
            await self._reply(update, format_usage("alert", "on hoặc /alert off"))
            return
        enabled = context.args[0].lower() == "on"
        await self.subscriptions.set_alerts(chat_id, enabled)
        await self._reply(update, format_alert_state(enabled))

    async def digest(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = await self._ensure_user(update)
        if len(context.args) != 1 or context.args[0].lower() not in {"on", "off"}:
            enabled = await self.subscriptions.digest_enabled(chat_id)
            await self._reply(update, f"Tổng kết danh mục: {'đang bật' if enabled else 'đang tắt'}. "
                              "Dùng /digest on hoặc /digest off.")
            return
        if getattr(update.effective_chat, "type", "private") != "private":
            await self._reply(update, "Tổng kết danh mục chỉ gửi trong chat riêng với bot.")
            return
        enabled = context.args[0].lower() == "on"
        await self.subscriptions.set_digest(chat_id, enabled)
        await self._reply(update, f"Đã bật tổng kết danh mục sau {self.settings.digest_time:%H:%M} cho phiên trước có VNINDEX "
                          "cuối ngày hợp lệ. Tin sẽ ghi rõ ngày và độ bao phủ; /digest off để dừng."
                          if enabled else "Đã tắt tổng kết danh mục tự động.")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._ensure_user(update)
        database_ok = await asyncio.to_thread(self.database.healthcheck)
        now = datetime.now(ZoneInfo(self.settings.timezone))
        provider = self.signal_service.provider
        last_full_eod = expected_eod = None
        if isinstance(provider, StrategySignalProvider) and hasattr(provider.engine, "store"):
            last_full_eod = await asyncio.to_thread(provider.engine.store.last_eod_refresh_date)
            expected_eod = previous_trading_day(now.date()).isoformat()
        application = getattr(context, "application", None)
        refresh_health = (application.bot_data.get("eod_health") if application else None)
        await self._reply(
            update,
            format_status(
                provider.name,
                database_ok,
                now,
                provider.latest_data_time(),
                last_full_eod, expected_eod, refresh_health,
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
            lookup = await self.signal_service.lookup(parsed.tickers[0], saved_only=True)
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
            guidance = _invalid_manual_command_guidance(
                getattr(message, "text", "") or "")
            if guidance:
                user_message = guidance
            elif isinstance(error, NetworkError):
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
        application.add_handler(CommandHandler("start", self._with_waiting(self.start)))
        application.add_handler(CommandHandler("help", self._with_waiting(self.help)))
        application.add_handler(CommandHandler(["analyze", "soi"], self._with_waiting(self.soi)))
        application.add_handler(CommandHandler("chart", self._with_waiting(self.chart_command)))
        application.add_handler(CallbackQueryHandler(self.chart, pattern=r"^(chart|flow):[A-Z0-9]{1,10}$"))
        application.add_handler(CommandHandler("scan", self._with_waiting(self.scan)))
        application.add_handler(CommandHandler(["portfolio", "danhmuc"], self._with_waiting(self.watchlist)))
        application.add_handler(CommandHandler(
            ["modelportfolio", "phanbo"], self._with_waiting(self.model_portfolio)))
        application.add_handler(CommandHandler("paper", self._with_waiting(self.paper)))
        application.add_handler(CommandHandler("papercapital", self._with_waiting(self.paper_capital)))
        application.add_handler(CommandHandler("paperposition", self._with_waiting(self.paper_position)))
        application.add_handler(CommandHandler(
            ["paperbuy", "buy", "mua"], self._with_waiting(self.paper_buy)))
        application.add_handler(CommandHandler(
            ["papersell", "sell", "ban"], self._with_waiting(self.paper_sell)))
        application.add_handler(CommandHandler("paperorders", self._with_waiting(self.paper_orders)))
        application.add_handler(CommandHandler("paperhistory", self._with_waiting(self.paper_history)))
        application.add_handler(CommandHandler("papercancel", self._with_waiting(self.paper_cancel)))
        application.add_handler(CallbackQueryHandler(
            self.paper_callback,
            pattern=r"^paper:(orders|history|account|chart|cancel:\d+)$"))
        application.add_handler(CallbackQueryHandler(self.scan_trade_callback, pattern=r"^scan:(buy|sell)$"))
        application.add_handler(CallbackQueryHandler(
            self.stock_action_callback,
            pattern=r"^(lookup:[A-Z0-9]{1,10}|trade:(buy|sell):[A-Z0-9]{1,10})$"))
        application.add_handler(CallbackQueryHandler(
            self.navigation_callback, pattern=r"^nav:(portfolio|modelportfolio)$"))
        application.add_handler(CommandHandler("add", self._with_waiting(self.add)))
        application.add_handler(CommandHandler(["remove", "del"], self._with_waiting(self.delete)))
        application.add_handler(CommandHandler("alert", self._with_waiting(self.alert)))
        application.add_handler(CommandHandler("digest", self._with_waiting(self.digest)))
        application.add_handler(CommandHandler("status", self._with_waiting(self.status)))
        application.add_handler(MessageHandler(filters.COMMAND,
                                               self._with_waiting(self.unknown_command)))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._with_waiting(self.text)))
        application.add_error_handler(self.error)
