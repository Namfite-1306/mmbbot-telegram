"""Local paper-trading ledger. No real broker order endpoint is called here.

New Telegram paper orders are filled immediately from a validated market-data
API quote and recorded atomically in SQLite. ``process`` remains only for
legacy pending orders created by older versions. No T+2 settlement or
corporate-action cash ledger is modeled.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO
import math
from typing import Callable
from zoneinfo import ZoneInfo

from app.backtest_v1 import BacktestConfig
from app.bot.validators import is_valid_ticker_format, normalize_ticker
from app.models import Signal
from app.storage.database import Database


class PaperOrderError(ValueError):
    """A paper order does not pass the account or market-data checks."""


class PaperBroker:
    def __init__(self, database: Database, config: BacktestConfig,
                 now: Callable[[], datetime] | None = None):
        self.database = database
        self.config = config
        self.now = now or (lambda: datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")))

    def _clock(self) -> datetime:
        value = self.now()
        if value.tzinfo is None:
            raise ValueError("Paper clock must include timezone")
        return value.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))

    def ensure_account(self, chat_id: int) -> None:
        instant = self._clock().isoformat(timespec="seconds")
        with self.database.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO paper_accounts(chat_id,initial_cash,cash,created_at) "
                         "VALUES(?,?,?,?)",
                         (chat_id, int(self.config.initial_capital), int(self.config.initial_capital), instant))

    def set_capital(self, chat_id: int, amount: int) -> None:
        """Change contributed virtual capital; keep positions and PnL unchanged."""
        if isinstance(amount, bool) or not isinstance(amount, int) or not 1 <= amount <= 10**15:
            raise PaperOrderError("Vốn mô phỏng phải là số nguyên VNĐ từ 1 đến 10^15.")
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account = conn.execute("SELECT initial_cash,cash FROM paper_accounts WHERE chat_id=?",
                                   (chat_id,)).fetchone()
            if account is None:
                raise PaperOrderError("Hãy dùng /paper để tạo tài khoản mô phỏng trước.")
            delta = amount - account["initial_cash"]
            reserved = conn.execute("SELECT COALESCE(SUM(estimated_cost),0) FROM paper_orders "
                                    "WHERE chat_id=? AND side='BUY' AND status='PENDING'", (chat_id,)).fetchone()[0]
            if account["cash"] + delta < reserved:
                raise PaperOrderError("Không thể giảm vốn: tiền mặt còn lại phải đủ cho lệnh mua đang chờ.")
            conn.execute("UPDATE paper_accounts SET initial_cash=?, cash=cash+? WHERE chat_id=?",
                         (amount, delta, chat_id))
            conn.execute("""INSERT INTO paper_adjustments
                (chat_id,kind,old_amount,new_amount,created_at) VALUES(?,'CAPITAL',?,?,?)""",
                (chat_id, account["initial_cash"], amount, self._clock().isoformat(timespec="seconds")))

    def set_position(self, chat_id: int, ticker: str, shares: int, cost_per_share: int) -> None:
        """Import/correct an existing holding without inventing an exchange fill."""
        ticker = normalize_ticker(ticker)
        if not is_valid_ticker_format(ticker):
            raise PaperOrderError("Mã cổ phiếu không hợp lệ.")
        if (isinstance(shares, bool) or not isinstance(shares, int) or shares < 0 or shares > 10**9
                or isinstance(cost_per_share, bool) or not isinstance(cost_per_share, int)
                or cost_per_share < 0 or (shares and cost_per_share == 0)
                or (not shares and cost_per_share != 0)
                or cost_per_share > 10**9):
            raise PaperOrderError("Nhập số cổ phiếu và giá vốn nguyên VNĐ; dùng 0 0 để xóa vị thế.")
        basis = shares * cost_per_share
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account = conn.execute("SELECT initial_cash FROM paper_accounts WHERE chat_id=?",
                                   (chat_id,)).fetchone()
            if account is None:
                raise PaperOrderError("Hãy dùng /paper để tạo tài khoản mô phỏng trước.")
            stock = conn.execute("SELECT exchange FROM market_symbols WHERE symbol=? AND security_type='STOCK'",
                                 (ticker,)).fetchone()
            if stock is None or stock["exchange"] not in {"HSX", "HOSE"}:
                raise PaperOrderError("Bản demo chỉ nhận cổ phiếu HOSE có trong dữ liệu.")
            pending = conn.execute("SELECT 1 FROM paper_orders WHERE chat_id=? AND ticker=? AND status='PENDING'",
                                   (chat_id, ticker)).fetchone()
            if pending:
                raise PaperOrderError("Hãy xử lý hoặc hủy lệnh chờ của mã này trước khi chỉnh vị thế.")
            old = conn.execute("SELECT shares,cost_basis FROM paper_positions WHERE chat_id=? AND ticker=?",
                               (chat_id, ticker)).fetchone()
            old_shares, old_basis = (old["shares"], old["cost_basis"]) if old else (0, 0)
            if shares and not old:
                count = conn.execute("SELECT COUNT(*) FROM paper_positions WHERE chat_id=?",
                                     (chat_id,)).fetchone()[0]
                if count >= self.config.max_positions:
                    raise PaperOrderError(f"Tối đa {self.config.max_positions} vị thế HOSE trong tài khoản demo.")
            new_capital = account["initial_cash"] + basis - old_basis
            if new_capital <= 0 or new_capital > 10**15:
                raise PaperOrderError("Điều chỉnh vị thế khiến tổng vốn góp không hợp lệ.")
            if shares:
                conn.execute("""INSERT INTO paper_positions(chat_id,ticker,shares,cost_basis)
                    VALUES(?,?,?,?) ON CONFLICT(chat_id,ticker) DO UPDATE SET
                    shares=excluded.shares,cost_basis=excluded.cost_basis""",
                    (chat_id, ticker, shares, basis))
            else:
                conn.execute("DELETE FROM paper_positions WHERE chat_id=? AND ticker=?", (chat_id, ticker))
            conn.execute("UPDATE paper_accounts SET initial_cash=? WHERE chat_id=?", (new_capital, chat_id))
            conn.execute("""INSERT INTO paper_adjustments
                (chat_id,kind,ticker,old_amount,new_amount,old_basis,new_basis,created_at)
                VALUES(?,'POSITION',?,?,?,?,?,?)""",
                (chat_id, ticker, old_shares, shares, old_basis, basis,
                 self._clock().isoformat(timespec="seconds")))

    @staticmethod
    def _quote(conn, ticker: str, as_of: str):
        return conn.execute("""SELECT trade_date,open,close FROM market_prices
            WHERE symbol=? AND trade_date<=? AND is_final=1 AND quality_flags IS NULL
              AND open>0 AND close>0 AND volume>0
            ORDER BY trade_date DESC LIMIT 1""", (ticker, as_of)).fetchone()

    def place(self, chat_id: int, ticker: str, side: str, shares: int,
              signal: Signal | None = None) -> int:
        ticker = normalize_ticker(ticker)
        side = side.upper()
        if not is_valid_ticker_format(ticker) or side not in {"BUY", "SELL"}:
            raise PaperOrderError("Mã hoặc chiều lệnh không hợp lệ.")
        if isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0 or shares % self.config.lot_size:
            raise PaperOrderError(f"Khối lượng phải là bội số dương của {self.config.lot_size} cổ phiếu.")
        now = self._clock()
        today = now.date().isoformat()
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account = conn.execute("SELECT cash FROM paper_accounts WHERE chat_id=?", (chat_id,)).fetchone()
            if account is None:
                raise PaperOrderError("Hãy dùng /paper để tạo tài khoản mô phỏng trước.")
            stock = conn.execute("SELECT exchange FROM market_symbols WHERE symbol=? AND security_type='STOCK'",
                                 (ticker,)).fetchone()
            if stock is None or stock["exchange"] not in {"HSX", "HOSE"}:
                raise PaperOrderError("Bản demo chỉ nhận cổ phiếu HOSE có trong dữ liệu.")
            quote = self._quote(conn, ticker, today)
            if quote is None or (now.date() - datetime.fromisoformat(quote["trade_date"]).date()) > timedelta(days=7):
                raise PaperOrderError("Thiếu giá cuối ngày hợp lệ trong 7 ngày gần đây; không nhận lệnh.")
            reference = int(round(quote["close"]))
            if side == "BUY":
                existing = conn.execute("SELECT 1 FROM paper_positions WHERE chat_id=? AND ticker=?",
                                        (chat_id, ticker)).fetchone()
                if not existing:
                    held_names = {row[0] for row in conn.execute(
                        "SELECT ticker FROM paper_positions WHERE chat_id=?", (chat_id,))}
                    pending_names = {row[0] for row in conn.execute(
                        "SELECT DISTINCT ticker FROM paper_orders WHERE chat_id=? AND side='BUY' AND status='PENDING'",
                        (chat_id,))}
                    if len(held_names | pending_names | {ticker}) > self.config.max_positions:
                        raise PaperOrderError(f"Tối đa {self.config.max_positions} vị thế HOSE trong tài khoản demo.")
                estimated = self._buy_cost(reference, shares)
                reserved = conn.execute("SELECT COALESCE(SUM(estimated_cost),0) FROM paper_orders "
                                        "WHERE chat_id=? AND side='BUY' AND status='PENDING'", (chat_id,)).fetchone()[0]
                if estimated > account["cash"] - reserved:
                    raise PaperOrderError("Tiền mặt khả dụng không đủ sau khi trừ các lệnh mua đang chờ.")
            else:
                held = conn.execute("SELECT shares FROM paper_positions WHERE chat_id=? AND ticker=?",
                                    (chat_id, ticker)).fetchone()
                reserved_shares = conn.execute("SELECT COALESCE(SUM(shares),0) FROM paper_orders "
                                               "WHERE chat_id=? AND ticker=? AND side='SELL' AND status='PENDING'",
                                               (chat_id, ticker)).fetchone()[0]
                if held is None or shares > held["shares"] - reserved_shares:
                    raise PaperOrderError("Không đủ cổ phiếu khả dụng; bản demo không bán khống.")
                estimated = 0
            cursor = conn.execute("""INSERT INTO paper_orders
                (chat_id,ticker,side,shares,status,requested_at,requested_date,reference_close,estimated_cost)
                VALUES(?,?,?,?,'PENDING',?,?,?,?)""",
                (chat_id, ticker, side, shares, now.isoformat(timespec="seconds"), today, reference, estimated))
            order_id = int(cursor.lastrowid)
            if signal is not None and signal.ticker == ticker:
                conn.execute("""INSERT INTO paper_journal
                    (order_id,chat_id,signal_status,signal_action,signal_score,signal_date,updated_at)
                    VALUES(?,?,?,?,?,?,?)""",
                    (order_id, chat_id, signal.status.value,
                     signal.action.value if signal.action else None,
                     signal.score if signal.score_components and all(
                         signal.score_components.get(key) is not None for key in ("T", "F", "M")) else None,
                     signal.data_time.date().isoformat(), now.isoformat(timespec="seconds")))
            return order_id

    @staticmethod
    def _journal_text(value: str) -> str:
        cleaned = " ".join(value.split())
        if not 1 <= len(cleaned) <= 160:
            raise PaperOrderError("Mỗi ghi chú phải có từ 1 đến 160 ký tự.")
        return cleaned

    def record_plan(self, chat_id: int, order_id: int, thesis: str, exit_rule: str) -> None:
        thesis, exit_rule = self._journal_text(thesis), self._journal_text(exit_rule)
        with self.database.connect() as conn:
            order = conn.execute("SELECT 1 FROM paper_orders WHERE id=? AND chat_id=?",
                                 (order_id, chat_id)).fetchone()
            if order is None:
                raise PaperOrderError("Không tìm thấy lệnh ảo này trong tài khoản của bạn.")
            conn.execute("""INSERT INTO paper_journal(order_id,chat_id,thesis,exit_rule,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET
                thesis=excluded.thesis,exit_rule=excluded.exit_rule,updated_at=excluded.updated_at""",
                (order_id, chat_id, thesis, exit_rule, self._clock().isoformat(timespec="seconds")))

    def record_review(self, chat_id: int, order_id: int, reflection: str) -> None:
        reflection = self._journal_text(reflection)
        with self.database.connect() as conn:
            order = conn.execute("SELECT status FROM paper_orders WHERE id=? AND chat_id=?",
                                 (order_id, chat_id)).fetchone()
            if order is None:
                raise PaperOrderError("Không tìm thấy lệnh ảo này trong tài khoản của bạn.")
            if order["status"] != "FILLED":
                raise PaperOrderError("Chỉ đánh giá lại lệnh đã khớp mô phỏng.")
            conn.execute("""INSERT INTO paper_journal(order_id,chat_id,reflection,updated_at)
                VALUES(?,?,?,?) ON CONFLICT(order_id) DO UPDATE SET
                reflection=excluded.reflection,updated_at=excluded.updated_at""",
                (order_id, chat_id, reflection, self._clock().isoformat(timespec="seconds")))

    def _buy_cost(self, price: float, shares: int) -> int:
        fill = max(1, int(round(price * (1 + self.config.slippage_fraction))))
        gross = fill * shares
        return gross + int(round(gross * self.config.buy_fee_fraction))

    def latest_reference_close(self, ticker: str) -> float | None:
        """Return a stored close only for validating the live API price unit."""
        ticker = normalize_ticker(ticker)
        with self.database.connect() as conn:
            quote = self._quote(conn, ticker, self._clock().date().isoformat())
            return float(quote["close"]) if quote else None

    def execute_at_quote(self, chat_id: int, ticker: str, side: str, shares: int,
                         price: float, quote_time: datetime, quote_source: str,
                         signal: Signal | None = None) -> dict:
        """Fill a virtual order immediately at a validated current API quote."""
        ticker = normalize_ticker(ticker)
        side = side.upper()
        if not is_valid_ticker_format(ticker) or side not in {"BUY", "SELL"}:
            raise PaperOrderError("Mã hoặc chiều lệnh không hợp lệ.")
        if isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0 or shares % self.config.lot_size:
            raise PaperOrderError(f"Khối lượng phải là bội số dương của {self.config.lot_size} cổ phiếu.")
        if (isinstance(price, bool) or not isinstance(price, (int, float))
                or not math.isfinite(float(price)) or price <= 0):
            raise PaperOrderError("API không trả về giá hiện hành hợp lệ.")
        if quote_time.tzinfo is None or quote_time.utcoffset() is None:
            raise PaperOrderError("API không trả về thời gian giá có múi giờ.")
        now = self._clock()
        quote_time = quote_time.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
        if quote_time > now + timedelta(minutes=5):
            raise PaperOrderError("Thời gian giá API nằm trong tương lai.")
        if now - quote_time > timedelta(days=7):
            raise PaperOrderError("Giá API đã quá 7 ngày; không khớp lệnh ảo.")
        source = " ".join(str(quote_source).upper().split())[:30]
        if not source:
            raise PaperOrderError("API không cung cấp tên nguồn giá.")

        fill = max(1, int(round(float(price))))
        gross = fill * shares
        fee_rate = self.config.buy_fee_fraction if side == "BUY" else self.config.sell_fee_fraction
        fee = int(round(gross * fee_rate))
        tax = int(round(gross * self.config.sell_tax_fraction)) if side == "SELL" else 0
        requested_at = now.isoformat(timespec="seconds")
        quoted_at = quote_time.isoformat(timespec="seconds")

        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            account = conn.execute("SELECT cash FROM paper_accounts WHERE chat_id=?", (chat_id,)).fetchone()
            if account is None:
                raise PaperOrderError("Hãy dùng /paper để tạo tài khoản mô phỏng trước.")
            stock = conn.execute("SELECT exchange FROM market_symbols WHERE symbol=? AND security_type='STOCK'",
                                 (ticker,)).fetchone()
            if stock is None or stock["exchange"] not in {"HSX", "HOSE"}:
                raise PaperOrderError("Bản demo chỉ nhận cổ phiếu HOSE có trong dữ liệu.")
            held = conn.execute("SELECT shares,cost_basis FROM paper_positions WHERE chat_id=? AND ticker=?",
                                (chat_id, ticker)).fetchone()

            if side == "BUY":
                cost = gross + fee
                position_count = conn.execute("SELECT COUNT(*) FROM paper_positions WHERE chat_id=?",
                                              (chat_id,)).fetchone()[0]
                if cost > account["cash"]:
                    raise PaperOrderError("Tiền mặt không đủ để khớp ngay theo giá API hiện hành.")
                if held is None and position_count >= self.config.max_positions:
                    raise PaperOrderError(f"Tối đa {self.config.max_positions} vị thế HOSE trong tài khoản demo.")
                conn.execute("UPDATE paper_accounts SET cash=cash-? WHERE chat_id=?", (cost, chat_id))
                conn.execute("""INSERT INTO paper_positions(chat_id,ticker,shares,cost_basis)
                    VALUES(?,?,?,?) ON CONFLICT(chat_id,ticker) DO UPDATE SET
                    shares=shares+excluded.shares,cost_basis=cost_basis+excluded.cost_basis""",
                    (chat_id, ticker, shares, cost))
                estimated, cash_amount, pnl = cost, -cost, None
            else:
                if held is None or held["shares"] < shares:
                    raise PaperOrderError("Không đủ cổ phiếu khả dụng; bản demo không bán khống.")
                proceeds = gross - fee - tax
                allocated_cost = (held["cost_basis"] if shares == held["shares"]
                                  else int(round(held["cost_basis"] * shares / held["shares"])))
                pnl = proceeds - allocated_cost
                conn.execute("UPDATE paper_accounts SET cash=cash+? WHERE chat_id=?", (proceeds, chat_id))
                if shares == held["shares"]:
                    conn.execute("DELETE FROM paper_positions WHERE chat_id=? AND ticker=?", (chat_id, ticker))
                else:
                    conn.execute("UPDATE paper_positions SET shares=shares-?,cost_basis=cost_basis-? "
                                 "WHERE chat_id=? AND ticker=?",
                                 (shares, allocated_cost, chat_id, ticker))
                estimated, cash_amount = 0, proceeds

            cursor = conn.execute("""INSERT INTO paper_orders
                (chat_id,ticker,side,shares,status,requested_at,requested_date,reference_close,
                 estimated_cost,fill_date,fill_price,fee,tax,cash_amount,realized_pnl,
                 quote_source,quote_time,note)
                VALUES(?,?,?,?,'FILLED',?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (chat_id, ticker, side, shares, requested_at, now.date().isoformat(), fill,
                 estimated, quote_time.date().isoformat(), fill, fee, tax, cash_amount, pnl,
                 source, quoted_at, "Khớp ngay theo giá API; không phải lệnh thật"))
            order_id = int(cursor.lastrowid)
            if signal is not None and signal.ticker == ticker:
                conn.execute("""INSERT INTO paper_journal
                    (order_id,chat_id,signal_status,signal_action,signal_score,signal_date,updated_at)
                    VALUES(?,?,?,?,?,?,?)""",
                    (order_id, chat_id, signal.status.value,
                     signal.action.value if signal.action else None,
                     signal.score if signal.score_components and all(
                         signal.score_components.get(key) is not None for key in ("T", "F", "M")) else None,
                     signal.data_time.date().isoformat(), requested_at))
            return {"id": order_id, "status": "FILLED", "ticker": ticker, "side": side,
                    "shares": shares, "fill_price": fill, "fill_time": quoted_at,
                    "source": source, "fee": fee, "tax": tax, "realized_pnl": pnl}

    def process(self, chat_id: int) -> list[dict]:
        """Settle only orders with a later finalized bar; safe to call repeatedly."""
        today = self._clock().date().isoformat()
        events: list[dict] = []
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            orders = conn.execute("SELECT * FROM paper_orders WHERE chat_id=? AND status='PENDING' ORDER BY id",
                                  (chat_id,)).fetchall()
            for order in orders:
                bar = conn.execute("""SELECT trade_date,open FROM market_prices WHERE symbol=?
                    AND trade_date>? AND trade_date<=? AND is_final=1 AND quality_flags IS NULL
                    AND open>0 AND volume>0 ORDER BY trade_date LIMIT 1""",
                    (order["ticker"], order["requested_date"], today)).fetchone()
                if bar is None:
                    continue
                shares = order["shares"]
                slip = self.config.slippage_fraction * (1 if order["side"] == "BUY" else -1)
                fill = max(1, int(round(bar["open"] * (1 + slip))))
                gross = fill * shares
                fee_rate = self.config.buy_fee_fraction if order["side"] == "BUY" else self.config.sell_fee_fraction
                fee = int(round(gross * fee_rate))
                tax = int(round(gross * self.config.sell_tax_fraction)) if order["side"] == "SELL" else 0
                account = conn.execute("SELECT cash FROM paper_accounts WHERE chat_id=?", (chat_id,)).fetchone()
                held = conn.execute("SELECT shares,cost_basis FROM paper_positions WHERE chat_id=? AND ticker=?",
                                    (chat_id, order["ticker"])).fetchone()
                rejection = None
                if order["side"] == "BUY":
                    cost = gross + fee
                    position_count = conn.execute("SELECT COUNT(*) FROM paper_positions WHERE chat_id=?",
                                                  (chat_id,)).fetchone()[0]
                    if cost > account["cash"]:
                        rejection = "Giá mở cửa tăng; không đủ tiền mặt khi khớp."
                    elif held is None and position_count >= self.config.max_positions:
                        rejection = "Đã đạt số vị thế tối đa khi khớp."
                    else:
                        conn.execute("UPDATE paper_accounts SET cash=cash-? WHERE chat_id=?", (cost, chat_id))
                        conn.execute("""INSERT INTO paper_positions(chat_id,ticker,shares,cost_basis)
                            VALUES(?,?,?,?) ON CONFLICT(chat_id,ticker) DO UPDATE SET
                            shares=shares+excluded.shares,cost_basis=cost_basis+excluded.cost_basis""",
                            (chat_id, order["ticker"], shares, cost))
                        cash_amount, pnl = -cost, None
                elif held is None or held["shares"] < shares:
                    rejection = "Không đủ cổ phiếu khi khớp."
                else:
                    proceeds = gross - fee - tax
                    allocated_cost = (held["cost_basis"] if shares == held["shares"]
                                      else int(round(held["cost_basis"] * shares / held["shares"])))
                    pnl = proceeds - allocated_cost
                    conn.execute("UPDATE paper_accounts SET cash=cash+? WHERE chat_id=?", (proceeds, chat_id))
                    if shares == held["shares"]:
                        conn.execute("DELETE FROM paper_positions WHERE chat_id=? AND ticker=?",
                                     (chat_id, order["ticker"]))
                    else:
                        conn.execute("UPDATE paper_positions SET shares=shares-?,cost_basis=cost_basis-? "
                                     "WHERE chat_id=? AND ticker=?",
                                     (shares, allocated_cost, chat_id, order["ticker"]))
                    cash_amount = proceeds
                if rejection:
                    conn.execute("UPDATE paper_orders SET status='REJECTED',note=? WHERE id=?",
                                 (rejection, order["id"]))
                    conn.execute("""INSERT OR IGNORE INTO paper_order_events(order_id,chat_id,created_at)
                        VALUES(?,?,?)""", (order["id"], chat_id, self._clock().isoformat(timespec="seconds")))
                    events.append({"id": order["id"], "status": "REJECTED", "note": rejection})
                else:
                    conn.execute("""UPDATE paper_orders SET status='FILLED',fill_date=?,fill_price=?,
                        fee=?,tax=?,cash_amount=?,realized_pnl=? WHERE id=?""",
                        (bar["trade_date"], fill, fee, tax, cash_amount, pnl, order["id"]))
                    conn.execute("""INSERT OR IGNORE INTO paper_order_events(order_id,chat_id,created_at)
                        VALUES(?,?,?)""", (order["id"], chat_id, self._clock().isoformat(timespec="seconds")))
                    events.append({"id": order["id"], "status": "FILLED", "ticker": order["ticker"],
                                   "side": order["side"], "fill_date": bar["trade_date"], "fill_price": fill})
        return events

    def pending_accounts(self) -> list[int]:
        with self.database.connect() as conn:
            return [row[0] for row in conn.execute(
                "SELECT DISTINCT chat_id FROM paper_orders WHERE status='PENDING'")]

    def unsent_order_events(self) -> list[dict]:
        with self.database.connect() as conn:
            rows = conn.execute("""SELECT e.order_id,e.chat_id,o.ticker,o.side,o.shares,
                o.status,o.fill_date,o.fill_price,o.note FROM paper_order_events e
                JOIN paper_orders o ON o.id=e.order_id
                WHERE e.sent_at IS NULL ORDER BY e.order_id LIMIT 50""").fetchall()
            return [dict(row) for row in rows]

    def mark_order_event_sent(self, order_id: int) -> None:
        with self.database.connect() as conn:
            conn.execute("UPDATE paper_order_events SET sent_at=? WHERE order_id=? AND sent_at IS NULL",
                         (self._clock().isoformat(timespec="seconds"), order_id))

    def cancel(self, chat_id: int, order_id: int) -> bool:
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("UPDATE paper_orders SET status='CANCELED',note='Hủy bởi người dùng' "
                                  "WHERE id=? AND chat_id=? AND status='PENDING'", (order_id, chat_id))
            return cursor.rowcount == 1

    def cancel_ticker(self, chat_id: int, ticker: str) -> int:
        """Cancel all pending legacy orders for one ticker in this account."""
        ticker = normalize_ticker(ticker)
        if not is_valid_ticker_format(ticker):
            raise PaperOrderError("Mã cổ phiếu không hợp lệ.")
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE paper_orders SET status='CANCELED',note='Hủy bởi người dùng theo mã' "
                "WHERE chat_id=? AND ticker=? AND status='PENDING'",
                (chat_id, ticker),
            )
            return cursor.rowcount

    def snapshot(self, chat_id: int) -> dict:
        with self.database.connect() as conn:
            account = conn.execute("SELECT * FROM paper_accounts WHERE chat_id=?", (chat_id,)).fetchone()
            if account is None:
                raise PaperOrderError("Chưa có tài khoản mô phỏng.")
            positions = []
            market_value = 0
            unpriced = 0
            for row in conn.execute("SELECT * FROM paper_positions WHERE chat_id=? ORDER BY ticker", (chat_id,)):
                quote = self._quote(conn, row["ticker"], self._clock().date().isoformat())
                close = int(round(quote["close"])) if quote else None
                value = close * row["shares"] if close else None
                market_value += value or 0
                unpriced += value is None
                positions.append({"ticker": row["ticker"], "shares": row["shares"],
                                  "cost_basis": row["cost_basis"], "close": close,
                                  "trade_date": quote["trade_date"] if quote else None,
                                  "market_value": value})
            pending = conn.execute("SELECT COUNT(*) FROM paper_orders WHERE chat_id=? AND status='PENDING'",
                                   (chat_id,)).fetchone()[0]
            return {"initial_cash": account["initial_cash"], "cash": account["cash"],
                    "positions": positions, "market_value": market_value,
                    "equity": account["cash"] + market_value if not unpriced else None,
                    "unpriced": unpriced, "pending": pending}

    def orders(self, chat_id: int, limit: int = 10, pending_only: bool = False) -> list[dict]:
        with self.database.connect() as conn:
            if pending_only:
                rows = conn.execute("""SELECT o.*,j.thesis,j.exit_rule,j.reflection,
                    j.signal_status,j.signal_action,j.signal_score,j.signal_date FROM paper_orders o
                    LEFT JOIN paper_journal j ON j.order_id=o.id AND j.chat_id=o.chat_id
                    WHERE o.chat_id=? AND o.status='PENDING' ORDER BY o.id DESC LIMIT ?""",
                    (chat_id, limit)).fetchall()
            else:
                rows = conn.execute("""SELECT o.*,j.thesis,j.exit_rule,j.reflection,
                    j.signal_status,j.signal_action,j.signal_score,j.signal_date FROM paper_orders o
                    LEFT JOIN paper_journal j ON j.order_id=o.id AND j.chat_id=o.chat_id
                    WHERE o.chat_id=? ORDER BY o.id DESC LIMIT ?""", (chat_id, limit)).fetchall()
            return [dict(row) for row in rows]


def format_paper_account(snapshot: dict) -> str:
    equity = (f"{snapshot['equity']:,.0f} VNĐ" if snapshot["equity"] is not None
              else "chưa xác định vì có mã thiếu giá")
    lines = ["🧪 TÀI KHOẢN GIAO DỊCH MÔ PHỎNG — KHÔNG PHẢI TIỀN THẬT",
             f"Vốn góp mô phỏng: {snapshot['initial_cash']:,.0f} VNĐ",
             f"Tiền mặt: {snapshot['cash']:,.0f} VNĐ",
             f"Giá trị cổ phiếu theo Close: {snapshot['market_value']:,.0f} VNĐ",
             f"Tổng tài sản ước tính: {equity}",
             f"Lệnh chờ cũ: {snapshot['pending']}", "", "Vị thế:"]
    if not snapshot["positions"]:
        lines.append("Chưa có cổ phiếu trong danh mục ảo.")
    for item in snapshot["positions"]:
        if item["market_value"] is None:
            lines.append(f"• {item['ticker']}: {item['shares']:,} cp — chưa có giá định giá.")
        else:
            pnl = item["market_value"] - item["cost_basis"]
            lines.append(f"• {item['ticker']}: {item['shares']:,} cp | Close {item['close']:,.0f} "
                         f"({item['trade_date']}) | Lãi/lỗ chưa thực hiện {pnl:+,.0f} VNĐ")
    lines.extend(["", "/paperbuy <MÃ> <SỐ_CP> · /papersell <MÃ> <SỐ_CP>",
                  "Lệnh ảo mới khớp ngay theo giá khớp gần nhất từ API và không được gửi ra sàn. "
                  "Không mô phỏng T+2 hay điều chỉnh quyền doanh nghiệp."])
    return "\n".join(lines)


def render_paper_portfolio(snapshot: dict) -> BytesIO:
    """Current virtual holdings by last valid close, never a broker balance."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    known = [(item["ticker"], item["market_value"]) for item in snapshot["positions"]
             if item["market_value"] is not None and item["market_value"] > 0]
    values = [value for _, value in known] + [snapshot["cash"]]
    names = [name for name, _ in known] + ["Tiền mặt"]
    if sum(values) <= 0:
        raise PaperOrderError("Chưa có giá trị tài sản để vẽ biểu đồ.")
    fig = Figure(figsize=(8, 6), dpi=135, facecolor="#20252c")
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    ax.set_facecolor("#20252c")
    colors = ["#63c74b", "#f1bd48", "#4e9ed5", "#dd7792", "#a88dd0"]
    wedges, _ = ax.pie(values, colors=colors[:len(known)] + ["#718096"], startangle=90,
                       wedgeprops={"width": 0.32, "edgecolor": "#20252c"})
    total = sum(values)
    ax.text(0, 0.06, f"{total / 1_000_000:,.1f} triệu", ha="center", va="center",
            color="white", fontsize=21, fontweight="bold")
    ax.text(0, -0.14, "tài sản ảo", ha="center", color="#c5ccd5", fontsize=12)
    ax.legend(wedges, [f"{name}: {value / total:.1%}" for name, value in zip(names, values)],
              loc="lower center", bbox_to_anchor=(0.5, -0.13), ncol=2,
              frameon=False, labelcolor="white")
    ax.set_title("Phân bổ tài khoản ảo theo Close đã lưu", color="white", fontsize=16, pad=20)
    output = BytesIO()
    output.name = "paper_portfolio.png"
    fig.savefig(output, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    output.seek(0)
    return output


def format_paper_orders(orders: list[dict], pending_only: bool) -> str:
    heading = "Lệnh mô phỏng đang chờ" if pending_only else "10 lệnh mô phỏng gần nhất"
    if not orders:
        return f"{heading}: chưa có."
    lines = [f"📋 {heading}"]
    for order in orders:
        entry = (f"#{order['id']} {order['side']} {order['ticker']} {order['shares']:,} cp "
                 f"— {order['status']}")
        if order["status"] == "FILLED":
            if order.get("quote_source"):
                entry += (f" | {order['quote_source']} {order['fill_price']:,.0f} "
                          f"({order.get('quote_time') or order['fill_date']})")
            else:
                entry += f" | Open {order['fill_price']:,.0f} ({order['fill_date']})"
            if order["realized_pnl"] is not None:
                entry += f" | PnL {order['realized_pnl']:+,.0f} VNĐ"
        elif order["note"]:
            entry += f" | {order['note']}"
        lines.append(entry)
        if order.get("signal_date"):
            score = (f"{order['signal_score']:.1f}/100" if order["signal_score"] is not None
                     else "chưa có điểm tổng")
            label = order["signal_action"] or order.get("signal_status") or "CHƯA CÓ TÍN HIỆU"
            lines.append(f"  Tín hiệu lúc đặt: {label} · {score} · {order['signal_date']}")
        if order.get("thesis"):
            lines.append(f"  Lý do: {order['thesis']} | Thoát khi: {order['exit_rule']}")
        if order.get("reflection"):
            lines.append(f"  Đánh giá sau lệnh: {order['reflection']}")
    if pending_only:
        lines.append("Hủy bằng nút bên dưới hoặc /papercancel <ID hoặc MÃ>.")
    message = "\n".join(lines)
    return message if len(message) <= 3900 else message[:3850] + "\n… Nhật ký dài; chỉ hiển thị phần đầu."
