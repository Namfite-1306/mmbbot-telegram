import pandas as pd

from technical_engine import (
    load_data,
    clean_data
)

from features import (
    calculate_features
)

from fundamental_engine import (
    build_fundamental_features
)

from backtest_engine import (
    backtest_multiple_stocks,
    calculate_performance
)


# =========================================================
# CONFIG
# =========================================================

FILE_PATH = "data/data.xlsx"

START_DATE = "2026-03-01"
END_DATE = "2026-09-18"

FUNDAMENTAL_THRESHOLD = 50

INITIAL_CAPITAL = 100_000_000


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
    "VRE"
]


# =========================================================
# 1. LOAD TECHNICAL DATA
# =========================================================

print("\n")
print("=" * 100)
print("BACKTEST V1 - LOAD TECHNICAL DATA")
print("=" * 100)


df = load_data(FILE_PATH)

df = clean_data(df)


# Chỉ lấy 15 mã
# KHÔNG lọc ngày ở đây
df = df[
    df["symbol"].isin(SYMBOLS)
].copy()


# Chuẩn hóa ngày
df["date"] = pd.to_datetime(
    df["date"],
    errors="coerce"
)


# =========================================================
# 2. CALCULATE TECHNICAL FEATURES
# =========================================================

print("\n")
print("=" * 100)
print("CALCULATING TECHNICAL FEATURES")
print("=" * 100)


technical_parts = []


for symbol in SYMBOLS:

    stock = df[
        df["symbol"] == symbol
    ].copy()

    if stock.empty:
        continue

    # Sắp xếp toàn bộ lịch sử theo thời gian
    stock = stock.sort_values(
        "date"
    ).reset_index(drop=True)

    # QUAN TRỌNG:
    # Tính EMA20, EMA50, EMA200, RSI, ATR...
    # trên TOÀN BỘ lịch sử
    stock = calculate_features(
        stock
    )

    technical_parts.append(
        stock
    )


technical_data = pd.concat(
    technical_parts,
    ignore_index=True
)


# =========================================================
# 3. CHỈ LỌC GIAI ĐOẠN BACKTEST
# =========================================================

technical_data = technical_data[
    (technical_data["date"] >= START_DATE)
    & (technical_data["date"] <= END_DATE)
].copy()


print(
    f"Technical rows: {len(technical_data):,}"
)

print(
    f"Symbols: "
    f"{technical_data['symbol'].nunique()}"
)
# =========================================================
# 3. LOAD FUNDAMENTAL DATA
# =========================================================

print("\n")
print("=" * 100)
print("LOADING FUNDAMENTAL DATA")
print("=" * 100)


fundamental_data = (
    build_fundamental_features(
        FILE_PATH
    )
)


print(
    f"Fundamental rows: "
    f"{len(fundamental_data):,}"
)


# =========================================================
# 4. TECHNICAL-ONLY BACKTEST
# =========================================================

print("\n")
print("=" * 100)
print("A. TECHNICAL-ONLY BACKTEST")
print("=" * 100)


technical_trades = (
    backtest_multiple_stocks(
        technical_data=technical_data,
        fundamental_data=fundamental_data,
        symbols=SYMBOLS,
        use_fundamental=False,
        initial_capital=INITIAL_CAPITAL
    )
)


technical_metrics = (
    calculate_performance(
        technical_trades,
        INITIAL_CAPITAL
    )
)


print("\nTechnical-only results:")

for key, value in technical_metrics.items():

    if isinstance(value, float):

        print(
            f"{key}: {value:.2f}"
        )

    else:

        print(
            f"{key}: {value}"
        )


# =========================================================
# 5. TECHNICAL + FUNDAMENTAL
# =========================================================

print("\n")
print("=" * 100)
print("B. TECHNICAL + FUNDAMENTAL BACKTEST")
print("=" * 100)


fundamental_trades = (
    backtest_multiple_stocks(
        technical_data=technical_data,
        fundamental_data=fundamental_data,
        symbols=SYMBOLS,
        use_fundamental=True,
        fundamental_threshold=FUNDAMENTAL_THRESHOLD,
        initial_capital=INITIAL_CAPITAL
    )
)


fundamental_metrics = (
    calculate_performance(
        fundamental_trades,
        INITIAL_CAPITAL
    )
)


print("\nTechnical + Fundamental results:")

for key, value in fundamental_metrics.items():

    if isinstance(value, float):

        print(
            f"{key}: {value:.2f}"
        )

    else:

        print(
            f"{key}: {value}"
        )


# =========================================================
# 6. COMPARISON
# =========================================================

print("\n")
print("=" * 100)
print("BACKTEST COMPARISON")
print("=" * 100)


comparison = pd.DataFrame([
    {
        "strategy": "Technical-only",
        **technical_metrics
    },
    {
        "strategy": "Technical + Fundamental",
        **fundamental_metrics
    }
])


print(
    comparison.to_string(
        index=False
    )
)


# =========================================================
# 7. TRADES
# =========================================================

print("\n")
print("=" * 100)
print("TECHNICAL-ONLY TRADES")
print("=" * 100)


if technical_trades.empty:

    print("Không có giao dịch.")

else:

    print(
        technical_trades.to_string(
            index=False
        )
    )


print("\n")
print("=" * 100)
print("TECHNICAL + FUNDAMENTAL TRADES")
print("=" * 100)


if fundamental_trades.empty:

    print("Không có giao dịch.")

else:

    print(
        fundamental_trades.to_string(
            index=False
        )
    )