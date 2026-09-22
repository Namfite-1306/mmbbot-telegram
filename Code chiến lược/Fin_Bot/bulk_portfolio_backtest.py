import pandas as pd
import os

from technical_engine import load_clean_ohlcv_directory
from features import calculate_features
from portfolio_backtest import portfolio_backtest


# ============================================================
# CONFIG
# ============================================================

DATA_DIR = "data/processed/ohlcv"

START_DATE = "2026-03-01"
END_DATE = "2026-09-18"

INITIAL_CAPITAL = 100_000_000
RISK_PERCENT = 0.01
LOT_SIZE = 100


# ============================================================
# 1. LOAD TOÀN BỘ OHLCV
# ============================================================

print("=" * 60)
print("BULK PORTFOLIO BACKTEST - TECHNICAL ONLY")
print("=" * 60)

print("\n[1] Loading OHLCV...")

df = load_clean_ohlcv_directory(DATA_DIR)

print(f"Rows: {len(df):,}")
print(f"Symbols: {df['symbol'].nunique():,}")
print(
    f"Date range: "
    f"{df['date'].min().date()} -> {df['date'].max().date()}"
)


# ============================================================
# 2. TÍNH FEATURES CHO TỪNG MÃ
# ============================================================

print("\n[2] Calculating technical features...")

feature_frames = []

symbols = sorted(df["symbol"].dropna().unique())

for i, symbol in enumerate(symbols, start=1):

    stock_df = df[df["symbol"] == symbol].copy()

    if stock_df.empty:
        continue

    try:
        stock_features = calculate_features(stock_df)

        # Các điều kiện Entry
        stock_features["volume_ok"] = (
            stock_features["volume_ratio"] >= 1.5
        )

        stock_features["trend_ok"] = (
            (stock_features["close"] > stock_features["ema20"])
            & (stock_features["ema20"] > stock_features["ema50"])
        )

        stock_features["rsi_ok"] = (
            stock_features["rsi14"] > 50
        )

        stock_features["distance_ok"] = (
            stock_features["ema_distance_atr"] <= 3
        )

        feature_frames.append(stock_features)

    except Exception as e:
        print(f"ERROR {symbol}: {e}")

    if i % 100 == 0:
        print(f"Processed: {i:,}/{len(symbols):,}")


# ============================================================
# 3. GỘP FEATURES
# ============================================================

print("\n[3] Combining features...")

technical_data = pd.concat(
    feature_frames,
    ignore_index=True
)

technical_data["date"] = pd.to_datetime(
    technical_data["date"],
    errors="coerce"
)

technical_data = technical_data.sort_values(
    ["date", "symbol"]
).reset_index(drop=True)

print(f"Feature rows: {len(technical_data):,}")
print(f"Feature symbols: {technical_data['symbol'].nunique():,}")


# ============================================================
# 4. BACKTEST
# ============================================================

print("\n[4] Running portfolio backtest...")

trades_df, equity_df = portfolio_backtest(
    technical_data=technical_data,
    fundamental_data=None,
    symbols=None,
    start_date=START_DATE,
    end_date=END_DATE,
    use_fundamental=False,
    initial_capital=INITIAL_CAPITAL,
    risk_percent=RISK_PERCENT,
    lot_size=LOT_SIZE,
)


# ============================================================
# 5. PERFORMANCE
# ============================================================

from portfolio_backtest import calculate_portfolio_performance

performance = calculate_portfolio_performance(
    trades_df=trades_df,
    equity_df=equity_df,
    initial_capital=INITIAL_CAPITAL,
)


# ============================================================
# 6. OUTPUT
# ============================================================

print("\n" + "=" * 60)
print("BULK BACKTEST RESULT")
print("=" * 60)

print(f"Initial capital:       {performance['initial_capital']:,.0f}")
print(f"Final capital:         {performance['final_capital']:,.0f}")
print(f"Total return (%):      {performance['total_return_pct']:.4f}")
print(f"Total trades:          {performance['total_trades']}")
print(f"Win rate (%):          {performance['win_rate']:.2f}")
print(
    f"Average return/trade:  "
    f"{performance['average_return_pct']:.4f}%"
)
print(f"Profit factor:         {performance['profit_factor']:.4f}")
print(
    f"Max drawdown (%):      "
    f"{performance['max_drawdown_pct']:.4f}"
)

print(
    f"\nSymbols with trades: "
    f"{trades_df['symbol'].nunique() if not trades_df.empty else 0}"
)


# ============================================================
# 7. TOP TRADED SYMBOLS
# ============================================================

if not trades_df.empty:

    print("\n" + "=" * 60)
    print("TOP TRADED SYMBOLS")
    print("=" * 60)

    top_symbols = (
        trades_df
        .groupby("symbol")
        .size()
        .sort_values(ascending=False)
        .head(20)
    )

    print(top_symbols)


# ============================================================
# 8. SAVE RESULTS
# ============================================================

os.makedirs("data", exist_ok=True)

trades_path = "data/trades_bulk_technical.csv"
equity_path = "data/equity_bulk_technical.csv"

trades_df.to_csv(
    trades_path,
    index=False,
    encoding="utf-8-sig"
)

equity_df.to_csv(
    equity_path,
    index=False,
    encoding="utf-8-sig"
)

print("\n" + "=" * 60)
print("ĐÃ LƯU")
print("=" * 60)

print(trades_path)
print(equity_path)