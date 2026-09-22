"""Local paper-trading ledger. No broker order endpoint is called here.

Orders are accepted on calendar day D and can only be filled from the first
subsequent finalized, valid daily bar. Fills use that bar's raw open plus the
configured slippage; accounting is updated atomically in SQLite. The bar is
observed after the session, so the display is a delayed simulation, not live
execution. No T+2 settlement or corporate-action cash ledger is modeled.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from app.backtest_v1 import BacktestConfig
from app.bot.validators import is_valid_ticker_format, normalize_ticker
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

    @staticmethod
    def _quote(conn, ticker: str, as_of: str):
        return conn.execute("""SELECT trade_date,open,close FROM market_prices
            WHERE symbol=? AND trade_date<=? AND is_final=1 AND quality_flags IS NULL
              AND open>0 AND close>0 AND volume>0
            ORDER BY trade_date DESC LIMIT 1""", (ticker, as_of)).fetchone()

    def place(self, chat_id: int, ticker: str, side: str, shares: int) -> int:
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
            return int(cursor.lastrowid)

    def _buy_cost(self, price: float, shares: int) -> int:
        fill = max(1, int(round(price * (1 + self.config.slippage_fraction))))
        gross = fill * shares
        return gross + int(round(gross * self.config.buy_fee_fraction))

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
                    events.append({"id": order["id"], "status": "REJECTED", "note": rejection})
                else:
                    conn.execute("""UPDATE paper_orders SET status='FILLED',fill_date=?,fill_price=?,
                        fee=?,tax=?,cash_amount=?,realized_pnl=? WHERE id=?""",
                        (bar["trade_date"], fill, fee, tax, cash_amount, pnl, order["id"]))
                    events.append({"id": order["id"], "status": "FILLED", "ticker": order["ticker"],
                                   "side": order["side"], "fill_date": bar["trade_date"], "fill_price": fill})
        return events

    def cancel(self, chat_id: int, order_id: int) -> bool:
        with self.database.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("UPDATE paper_orders SET status='CANCELED',note='Hủy bởi người dùng' "
                                  "WHERE id=? AND chat_id=? AND status='PENDING'", (order_id, chat_id))
            return cursor.rowcount == 1

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
                rows = conn.execute("SELECT * FROM paper_orders WHERE chat_id=? AND status='PENDING' "
                                    "ORDER BY id DESC LIMIT ?", (chat_id, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM paper_orders WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                                    (chat_id, limit)).fetchall()
            return [dict(row) for row in rows]


def format_paper_account(snapshot: dict) -> str:
    equity = (f"{snapshot['equity']:,.0f} VNĐ" if snapshot["equity"] is not None
              else "chưa xác định vì có mã thiếu giá")
    lines = ["🧪 TÀI KHOẢN GIAO DỊCH MÔ PHỎNG — KHÔNG PHẢI TIỀN THẬT",
             f"Vốn ban đầu: {snapshot['initial_cash']:,.0f} VNĐ",
             f"Tiền mặt: {snapshot['cash']:,.0f} VNĐ",
             f"Giá trị cổ phiếu theo Close: {snapshot['market_value']:,.0f} VNĐ",
             f"Tổng tài sản ước tính: {equity}",
             f"Lệnh chờ: {snapshot['pending']}", "", "Vị thế:"]
    if not snapshot["positions"]:
        lines.append("Chưa có cổ phiếu; các lệnh mới chờ phiên giá tiếp theo.")
    for item in snapshot["positions"]:
        if item["market_value"] is None:
            lines.append(f"• {item['ticker']}: {item['shares']:,} cp — chưa có giá định giá.")
        else:
            pnl = item["market_value"] - item["cost_basis"]
            lines.append(f"• {item['ticker']}: {item['shares']:,} cp | Close {item['close']:,.0f} "
                         f"({item['trade_date']}) | Lãi/lỗ chưa thực hiện {pnl:+,.0f} VNĐ")
    lines.extend(["", "/paperbuy <MÃ> <SỐ_CP> · /papersell <MÃ> <SỐ_CP>",
                  "/paperorders · /paperhistory · /papercancel <ID>",
                  "Khớp mô phỏng tại Open phiên hợp lệ sau ngày đặt lệnh, khi dữ liệu cuối ngày đã về. "
                  "Không có khớp thời gian thực, T+2 hay điều chỉnh quyền doanh nghiệp."])
    return "\n".join(lines)


def format_paper_orders(orders: list[dict], pending_only: bool) -> str:
    heading = "Lệnh mô phỏng đang chờ" if pending_only else "10 lệnh mô phỏng gần nhất"
    if not orders:
        return f"{heading}: chưa có."
    lines = [f"📋 {heading}"]
    for order in orders:
        entry = (f"#{order['id']} {order['side']} {order['ticker']} {order['shares']:,} cp "
                 f"— {order['status']}")
        if order["status"] == "FILLED":
            entry += f" | Open {order['fill_price']:,.0f} ({order['fill_date']})"
            if order["realized_pnl"] is not None:
                entry += f" | PnL {order['realized_pnl']:+,.0f} VNĐ"
        elif order["note"]:
            entry += f" | {order['note']}"
        lines.append(entry)
    if pending_only:
        lines.append("Hủy lệnh chờ: /papercancel <ID>")
    return "\n".join(lines)
