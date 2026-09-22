"""Render one-year OHLCV and RSI from stored, finalized observations."""

from __future__ import annotations

from io import BytesIO

import pandas as pd

from app.market_store import MarketStore


def render_stock_chart(store: MarketStore, symbol: str) -> BytesIO:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.patches import Rectangle

    frame = store.prices(symbol, limit=252)
    if len(frame) < 20:
        raise ValueError(f"{symbol}: cần ít nhất 20 phiên cuối ngày hợp lệ để vẽ biểu đồ")
    frame = frame.reset_index(drop=True)
    dates = pd.to_datetime(frame["trade_date"])
    close = pd.to_numeric(frame["close"])
    change = close.diff()
    gain = change.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    loss = (-change.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, float("nan")))
    fig = Figure(figsize=(10, 8), dpi=135, layout="constrained")
    FigureCanvasAgg(fig)
    axes = fig.subplots(3, 1, sharex=True, height_ratios=[3, 1, 1])
    price_ax, volume_ax, rsi_ax = axes
    for pos, row in frame.iterrows():
        up = row["close"] >= row["open"]
        color = "#159a82" if up else "#dc5a64"
        price_ax.vlines(pos, row["low"], row["high"], color=color, linewidth=0.8)
        price_ax.add_patch(Rectangle((pos - 0.32, min(row["open"], row["close"])),
                                     0.64, max(abs(row["close"] - row["open"]), 0.01),
                                     color=color, linewidth=0))
        volume_ax.bar(pos, row["volume"], color=color, width=0.7)
    rsi_ax.plot(range(len(frame)), rsi, color="#7851a9", linewidth=1)
    rsi_ax.axhline(70, color="#dc5a64", linestyle="--", linewidth=0.8)
    rsi_ax.axhline(30, color="#159a82", linestyle="--", linewidth=0.8)
    rsi_ax.set_ylim(0, 100)
    rsi_ax.set_ylabel("RSI 14")
    volume_ax.set_ylabel("Khối lượng")
    price_ax.set_ylabel("VNĐ")
    price_ax.set_title(f"{symbol} · {dates.iloc[0]:%d/%m/%Y} – {dates.iloc[-1]:%d/%m/%Y} · Giá thô")
    ticks = list(range(0, len(frame), max(1, len(frame) // 7)))
    if ticks[-1] != len(frame) - 1:
        ticks.append(len(frame) - 1)
    rsi_ax.set_xticks(ticks, [dates.iloc[i].strftime("%d/%m/%y") for i in ticks])
    for axis in axes:
        axis.grid(alpha=0.15)
    image = BytesIO()
    image.name = f"{symbol}.png"
    fig.savefig(image, format="png")
    image.seek(0)
    return image


def calculate_cmf(frame: pd.DataFrame, periods: int = 20) -> pd.Series:
    """Chaikin Money Flow from finalized OHLCV; flat-range bars contribute zero."""
    high = pd.to_numeric(frame["high"])
    low = pd.to_numeric(frame["low"])
    close = pd.to_numeric(frame["close"])
    volume = pd.to_numeric(frame["volume"])
    spread = (high - low).replace(0, float("nan"))
    multiplier = ((close - low) - (high - close)).div(spread).fillna(0)
    flow_volume = multiplier * volume
    volume_sum = volume.rolling(periods, min_periods=periods).sum().replace(0, float("nan"))
    return flow_volume.rolling(periods, min_periods=periods).sum().div(volume_sum)


def render_money_flow_chart(store: MarketStore, symbol: str) -> BytesIO:
    """Plot CMF(20), a price/volume proxy rather than observed fund flows."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    frame = store.prices(symbol, limit=252).reset_index(drop=True)
    if len(frame) < 20:
        raise ValueError(f"{symbol}: cần ít nhất 20 phiên cuối ngày hợp lệ để vẽ dòng tiền")
    dates = pd.to_datetime(frame["trade_date"])
    close = pd.to_numeric(frame["close"])
    cmf = calculate_cmf(frame)
    positions = list(range(len(frame)))
    fig = Figure(figsize=(10, 6), dpi=135, layout="constrained")
    FigureCanvasAgg(fig)
    price_ax, flow_ax = fig.subplots(2, 1, sharex=True, height_ratios=[2, 1])
    price_ax.plot(positions, close, color="#3477bc", linewidth=1.5)
    price_ax.set_ylabel("Giá đóng cửa (VNĐ)")
    price_ax.set_title(f"{symbol} · {dates.iloc[0]:%d/%m/%Y} – {dates.iloc[-1]:%d/%m/%Y} · CMF(20)")
    flow_ax.bar(positions, cmf.fillna(0), width=0.8,
                color=["#159a82" if value >= 0 else "#dc5a64" for value in cmf.fillna(0)])
    flow_ax.axhline(0, color="#555555", linewidth=0.8)
    flow_ax.set_ylim(-1, 1)
    flow_ax.set_ylabel("CMF(20)")
    ticks = list(range(0, len(frame), max(1, len(frame) // 7)))
    if ticks[-1] != len(frame) - 1:
        ticks.append(len(frame) - 1)
    flow_ax.set_xticks(ticks, [dates.iloc[i].strftime("%d/%m/%y") for i in ticks])
    for axis in (price_ax, flow_ax):
        axis.grid(alpha=0.15)
    image = BytesIO()
    image.name = f"{symbol}_cmf20.png"
    fig.savefig(image, format="png")
    image.seek(0)
    return image
