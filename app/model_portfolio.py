"""Read-only, point-in-time illustration of a possible next-session portfolio.

This is not a trade ledger: sizing uses the signal close because the next open
is unknown, and no simulated position is persisted as an actual holding.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO

import pandas as pd

from app.backtest_v1 import BacktestConfig, order_shares
from app.models import Action, Signal, SignalStatus
from app.strategy_engine import StrategyDataError, VietcapStrategyEngine


@dataclass(frozen=True)
class ProposedPosition:
    ticker: str
    sector: str
    shares: int
    reference_close: float
    estimated_cost: float
    score: float


@dataclass(frozen=True)
class ModelPortfolio:
    session: str
    capital: float
    positions: tuple[ProposedPosition, ...]
    cash: float
    buy_count: int | None
    missing_sector: int
    missing_adjusted: int
    missing_sizing_data: int
    blocking_reason: str | None = None


def propose_portfolio(signals: list[Signal], engine: VietcapStrategyEngine,
                      cfg: BacktestConfig, session: str) -> ModelPortfolio:
    """Use only eligible HOSE BUY signals from the same final scan session."""
    universe = engine.universe()
    sectors = engine._sector_map()
    eligible = [s for s in signals if s.status is SignalStatus.SUCCESS and s.action is Action.BUY
                and s.data_time.date().isoformat() == session
                and universe.get(s.ticker) in {"HSX", "HOSE"}]
    eligible.sort(key=lambda s: (-s.score, s.ticker))
    cash = float(cfg.initial_capital)
    positions: list[ProposedPosition] = []
    sector_exposure: dict[str, float] = {}
    missing_sector = missing_adjusted = missing_sizing_data = 0
    for signal in eligible:
        if len(positions) >= cfg.max_positions:
            break
        sector = sectors.get(signal.ticker)
        if not sector:
            missing_sector += 1
            continue
        try:
            prices, _ = engine.prices(signal.ticker, pd.Timestamp(session))
        except (StrategyDataError, ValueError, KeyError, OSError):
            missing_sizing_data += 1
            continue
        if prices.empty or prices.iloc[-1]["date"].date().isoformat() != session:
            missing_sizing_data += 1
            continue
        if not bool(prices.iloc[-1]["has_adjusted_history"]):
            missing_adjusted += 1
            continue
        raw_close = float(prices.iloc[-1]["display_close"])
        previous = prices.iloc[:-1].tail(20)
        if len(previous) < 20 or raw_close <= 0:
            missing_sizing_data += 1
            continue
        prev_close = prices["close"].shift(1)
        true_range = pd.concat([prices["high"] - prices["low"],
                                (prices["high"] - prev_close).abs(),
                                (prices["low"] - prev_close).abs()], axis=1).max(axis=1)
        atr = float(true_range.tail(14).mean())
        adv20 = float((previous["display_close"] * previous["volume"]).mean())
        factor = float(prices.iloc[-1]["close"]) / raw_close
        regime = next((r.split(": ", 1)[1] for r in signal.reasons
                       if r.startswith("Market regime: ")), "UNKNOWN")
        if not all(math.isfinite(v) and v > 0 for v in (atr, adv20, factor)) or regime not in {"BULL", "NEUTRAL"}:
            missing_sizing_data += 1
            continue
        row = pd.Series({"signal_atr": atr, "signal_adv20": adv20,
                         "signal_adjustment_factor": factor, "signal_regime": regime})
        indicative_fill = raw_close * (1 + cfg.slippage_fraction)
        shares = order_shares(row, cash, cfg.initial_capital,
                              sector_exposure.get(sector, 0), indicative_fill, cfg)
        while shares and sector_exposure.get(sector, 0) + shares * indicative_fill * (1 + cfg.buy_fee_fraction) > cfg.initial_capital * cfg.max_sector_fraction:
            shares -= cfg.lot_size
        if shares == 0:
            continue
        cost = shares * indicative_fill * (1 + cfg.buy_fee_fraction)
        if cost > cash:
            continue
        positions.append(ProposedPosition(signal.ticker, sector, shares, raw_close, cost, signal.score))
        cash -= cost
        sector_exposure[sector] = sector_exposure.get(sector, 0) + cost
    return ModelPortfolio(session, cfg.initial_capital, tuple(positions), cash,
                          len(eligible), missing_sector, missing_adjusted, missing_sizing_data)


def format_model_portfolio(result: ModelPortfolio) -> str:
    lines = ["📊 Danh mục mô phỏng — không phải cổ phiếu đang nắm giữ",
             f"Phiên tín hiệu: {result.session} | Vốn giả định: {result.capital:,.0f} VNĐ",
             (f"BUY đủ điều kiện tín hiệu: {result.buy_count} mã HOSE"
              if result.buy_count is not None else "Chưa quét BUY vì thiếu điều kiện phân bổ vốn")]
    for item in result.positions:
        lines.append(f"• {item.ticker}: {item.shares:,} cp × {item.reference_close:,.0f} VNĐ "
                     f"≈ {item.estimated_cost / result.capital:.1%} vốn | điểm {item.score:.0f} | {item.sector}")
    lines.append(f"💵 Tiền mặt: {result.cash:,.0f} VNĐ ({result.cash / result.capital:.1%})")
    if not result.positions:
        lines.append("Chưa có vị thế đề xuất đủ điều kiện; giữ 100% tiền mặt.")
    if result.blocking_reason:
        lines.append(result.blocking_reason)
    if result.missing_sector:
        lines.append(f"Thiếu ngành: {result.missing_sector} mã; chưa thể kiểm tra giới hạn 35%/ngành.")
    if result.missing_adjusted:
        lines.append(f"Thiếu adjusted OHLC: {result.missing_adjusted} mã; chưa phân bổ vốn.")
    if result.missing_sizing_data:
        lines.append(f"Thiếu dữ liệu tính khối lượng/rủi ro: {result.missing_sizing_data} mã.")
    lines.append("Tỷ trọng chỉ ước tính tại Close; giá Open phiên kế tiếp, phí và khớp lệnh thực tế có thể khác. "
                 "Không phải khuyến nghị đầu tư.")
    return "\n".join(lines)


def render_model_portfolio(result: ModelPortfolio) -> BytesIO:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    names = [item.ticker for item in result.positions] + ["Tiền mặt"]
    values = [item.estimated_cost for item in result.positions] + [result.cash]
    colors = ["#63c74b", "#f1bd48", "#4e9ed5"][:len(result.positions)] + ["#718096"]
    fig = Figure(figsize=(8, 6), dpi=135, facecolor="#20252c")
    FigureCanvasAgg(fig)
    ax = fig.subplots()
    ax.set_facecolor("#20252c")
    wedges, _ = ax.pie(values, colors=colors, startangle=90,
                       wedgeprops={"width": 0.32, "edgecolor": "#20252c"})
    ax.text(0, 0.08, f"{result.capital / 1_000_000:.0f} triệu", ha="center", va="center",
            color="white", fontsize=25, fontweight="bold")
    ax.text(0, -0.14, "vốn mô phỏng", ha="center", color="#c5ccd5", fontsize=12)
    ax.legend(wedges, [f"{name}: {value / result.capital:.1%}" for name, value in zip(names, values)],
              loc="lower center", bbox_to_anchor=(0.5, -0.13), ncol=2,
              frameon=False, labelcolor="white")
    ax.set_title(f"Danh mục mô phỏng • {result.session}", color="white", fontsize=17, pad=20)
    output = BytesIO()
    output.name = "model_portfolio.png"
    fig.savefig(output, format="png", facecolor=fig.get_facecolor(), bbox_inches="tight")
    output.seek(0)
    return output
