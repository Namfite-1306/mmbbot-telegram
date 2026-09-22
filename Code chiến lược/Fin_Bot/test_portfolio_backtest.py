import pandas as pd

from technical_engine import load_data, clean_data
from features import calculate_features
from fundamental_engine import build_fundamental_features

from portfolio_backtest import (
    portfolio_backtest,
    calculate_portfolio_performance,
)


# ============================================================
# CONFIG
# ============================================================

FILE_PATH = "data/data.xlsx"

START_DATE = "2026-03-01"
END_DATE = "2026-09-18"

INITIAL_CAPITAL = 100_000_000

RISK_PERCENT = 0.01

FUNDAMENTAL_THRESHOLD = 50

SYMBOLS = [
    "BID",
    "CTG",
    "FPT",
    "GAS",
    "HPG",
    "MBB",
    "MSN",
    "MWG",
    "PLX",
    "SSI",
    "VCB",
    "VHM",
    "VIC",
    "VNM",
    "VRE",
]


# ============================================================
# 1. LOAD TECHNICAL DATA
# ============================================================

df = load_data(FILE_PATH)

df = clean_data(df)

df["date"] = pd.to_datetime(
    df["date"],
    errors="coerce"
)

df = df[
    df["symbol"].isin(SYMBOLS)
].copy()


# ============================================================
# 2. CALCULATE FEATURES
#
# QUAN TRỌNG:
# Tính indicator trên toàn bộ lịch sử trước,
# sau đó mới filter thời gian backtest.
# ============================================================

feature_list = []

for symbol in SYMBOLS:

    symbol_df = df[
        df["symbol"] == symbol
    ].copy()

    if symbol_df.empty:
        continue

    features = calculate_features(
        symbol_df
    )

    feature_list.append(features)


technical_data = pd.concat(
    feature_list,
    ignore_index=True
)

technical_data["date"] = pd.to_datetime(
    technical_data["date"],
    errors="coerce"
)
# ============================================================
# 2.5. TẠO CÁC ĐIỀU KIỆN ENTRY
# ============================================================

technical_data["volume_ok"] = (
    technical_data["volume_ratio"] >= 1.5
)

technical_data["trend_ok"] = (
    (technical_data["close"] > technical_data["ema20"])
    &
    (technical_data["ema20"] > technical_data["ema50"])
)

technical_data["rsi_ok"] = (
    technical_data["rsi14"] > 50
)

technical_data["distance_ok"] = (
    technical_data["ema_distance_atr"] <= 3
)
#2.6 FILTER BACKTEST PERIOD
technical_data = technical_data[
    (
        technical_data["date"]
        >= pd.to_datetime(START_DATE)
    )
    &
    (
        technical_data["date"]
        <= pd.to_datetime(END_DATE)
    )
].copy()

# KIỂM TRA ENTRY SIGNAL

print("\nKiểm tra điều kiện Entry:")

print(
    "Breakout:",
    technical_data["breakout"].sum()
)

print(
    "Volume OK:",
    technical_data["volume_ok"].sum()
)

print(
    "Trend OK:",
    technical_data["trend_ok"].sum()
)

print(
    "RSI OK:",
    technical_data["rsi_ok"].sum()
)

print(
    "Distance OK:",
    technical_data["distance_ok"].sum()
)
technical_data["entry_signal"] = (
    technical_data["breakout"]
    &
    technical_data["volume_ok"]
    &
    technical_data["trend_ok"]
    &
    technical_data["rsi_ok"]
    &
    technical_data["distance_ok"]
)

print(
    "ENTRY SIGNAL:",
    technical_data["entry_signal"].sum()
)
print(
    "Technical rows:",
    len(technical_data)
)

print(
    "Symbols:",
    technical_data["symbol"].nunique()
)


# ============================================================
# 3. LOAD FUNDAMENTAL
# ============================================================

fundamental_data = build_fundamental_features(
    FILE_PATH
)


# ============================================================
# 4. TECHNICAL ONLY
# ============================================================

print("\n" + "=" * 60)
print("PORTFOLIO BACKTEST - TECHNICAL ONLY")
print("=" * 60)


trades_technical, equity_technical = portfolio_backtest(
    technical_data=technical_data,
    fundamental_data=fundamental_data,
    symbols=SYMBOLS,
    start_date=START_DATE,
    end_date=END_DATE,
    use_fundamental=False,
    initial_capital=INITIAL_CAPITAL,
    risk_percent=RISK_PERCENT,
    lot_size=100,
)


performance_technical = calculate_portfolio_performance(
    trades_df=trades_technical,
    equity_df=equity_technical,
    initial_capital=INITIAL_CAPITAL,
)


for key, value in performance_technical.items():
    print(f"{key}: {value}")


print("\nTrades:")

if not trades_technical.empty:
    print(
        trades_technical.to_string(
            index=False
        )
    )
else:
    print("Không có giao dịch.")


# ============================================================
# 5. TECHNICAL + FUNDAMENTAL
# ============================================================

print("\n" + "=" * 60)
print("PORTFOLIO BACKTEST - TECHNICAL + FUNDAMENTAL")
print("=" * 60)


trades_fundamental, equity_fundamental = portfolio_backtest(
    technical_data=technical_data,
    fundamental_data=fundamental_data,
    symbols=SYMBOLS,
    start_date=START_DATE,
    end_date=END_DATE,
    use_fundamental=True,
    fundamental_threshold=FUNDAMENTAL_THRESHOLD,
    initial_capital=INITIAL_CAPITAL,
    risk_percent=RISK_PERCENT,
    lot_size=100,
)


performance_fundamental = calculate_portfolio_performance(
    trades_df=trades_fundamental,
    equity_df=equity_fundamental,
    initial_capital=INITIAL_CAPITAL,
)


for key, value in performance_fundamental.items():
    print(f"{key}: {value}")


print("\nTrades:")

if not trades_fundamental.empty:
    print(
        trades_fundamental.to_string(
            index=False
        )
    )
else:
    print("Không có giao dịch.")


# ============================================================
# 6. SO SÁNH
# ============================================================

print("\n" + "=" * 60)
print("SO SÁNH")
print("=" * 60)

comparison = pd.DataFrame([
    performance_technical,
    performance_fundamental,
])

comparison.index = [
    "Technical Only",
    "Technical + Fundamental",
]

print(
    comparison.to_string()
)


# ============================================================
# 7. EQUITY CURVE
# ============================================================

equity_technical.to_csv(
    "data/equity_technical.csv",
    index=False
)

equity_fundamental.to_csv(
    "data/equity_fundamental.csv",
    index=False
)

trades_technical.to_csv(
    "data/trades_technical.csv",
    index=False
)

trades_fundamental.to_csv(
    "data/trades_fundamental.csv",
    index=False
)

print("\nĐã lưu:")
print("data/equity_technical.csv")
print("data/equity_fundamental.csv")
print("data/trades_technical.csv")
print("data/trades_fundamental.csv")