import pandas as pd

from technical_engine import load_clean_ohlcv_directory
from features import calculate_features


# ============================================================
# 1. ĐỌC CLEAN OHLCV
# ============================================================

DATA_DIR = "data/processed/ohlcv"

df = load_clean_ohlcv_directory(DATA_DIR)


print("\n" + "=" * 60)
print("BẮT ĐẦU TÍNH TECHNICAL FEATURES")
print("=" * 60)


# ============================================================
# 2. TÍNH FEATURES
# ============================================================

features = calculate_features(df)


print("\nĐã tính Features thành công!")


# ============================================================
# 3. KIỂM TRA KÍCH THƯỚC
# ============================================================

print("\n" + "=" * 60)
print("1. KIỂM TRA SỐ DÒNG / SỐ MÃ")
print("=" * 60)

print(
    "OHLCV ban đầu:",
    len(df),
    "dòng"
)

print(
    "Features:",
    len(features),
    "dòng"
)

print(
    "Số mã:",
    features["symbol"].nunique()
)


if len(df) == len(features):
    print("PASS: Số dòng không thay đổi")
else:
    print("WARNING: Số dòng thay đổi")


# ============================================================
# 4. KIỂM TRA CÁC CỘT FEATURES
# ============================================================

expected_columns = [
    "ema20",
    "ema50",
    "ema200",
    "rsi14",
    "atr14",
    "volume_ratio",
    "breakout_level",
    "breakout",
    "trend",
    "rsi_ok",
    "ema_distance_atr",
    "distance_ok",
]


print("\n" + "=" * 60)
print("2. KIỂM TRA CÁC CỘT FEATURES")
print("=" * 60)

missing_columns = []

for col in expected_columns:

    if col in features.columns:
        print(f"PASS: {col}")
    else:
        print(f"FAIL: thiếu {col}")
        missing_columns.append(col)


# ============================================================
# 5. KIỂM TRA NULL
# ============================================================

print("\n" + "=" * 60)
print("3. KIỂM TRA NULL")
print("=" * 60)

for col in expected_columns:

    if col in features.columns:

        null_count = features[col].isna().sum()

        print(
            f"{col:<20}: {null_count}"
        )


# ============================================================
# 6. KIỂM TRA FEATURE THEO TỪNG MÃ
# ============================================================

print("\n" + "=" * 60)
print("4. KIỂM TRA FEATURES THEO MÃ")
print("=" * 60)

check_symbols = [
    "FPT",
    "HPG",
    "SSI",
]


for symbol in check_symbols:

    symbol_df = features[
        features["symbol"] == symbol
    ].copy()

    print(
        f"\n--- {symbol} ---"
    )

    print(
        "Số dòng:",
        len(symbol_df)
    )

    print(
        "Ngày đầu:",
        symbol_df["date"].min()
    )

    print(
        "Ngày cuối:",
        symbol_df["date"].max()
    )

    print(
        "\n5 dòng cuối:"
    )

    print(
        symbol_df[
            [
                "date",
                "close",
                "ema20",
                "ema50",
                "ema200",
                "rsi14",
                "atr14",
                "volume_ratio",
                "breakout",
                "trend",
                "rsi_ok",
                "distance_ok",
            ]
        ].tail()
    )


# ============================================================
# 7. KIỂM TRA ENTRY SIGNAL
# ============================================================

print("\n" + "=" * 60)
print("5. KIỂM TRA ENTRY SIGNAL")
print("=" * 60)

entry_count = features[
    features["breakout"]
    & features["trend"]
    & features["rsi_ok"]
    & features["distance_ok"]
].shape[0]

print(
    "Tổng Entry Signal:",
    entry_count
)


# ============================================================
# 8. THỐNG KÊ ENTRY THEO MÃ
# ============================================================

entry_by_symbol = (
    features[
        features["breakout"]
        & features["trend"]
        & features["rsi_ok"]
        & features["distance_ok"]
    ]
    .groupby("symbol")
    .size()
    .sort_values(ascending=False)
)


print("\nEntry theo mã:")

print(
    entry_by_symbol.head(20)
)


# ============================================================
# 9. KIỂM TRA GIÁ TRỊ BẤT THƯỜNG
# ============================================================

print("\n" + "=" * 60)
print("6. KIỂM TRA GIÁ TRỊ BẤT THƯỜNG")
print("=" * 60)


# EMA phải > 0
ema_error = (
    (features["ema20"] <= 0)
    | (features["ema50"] <= 0)
    | (features["ema200"] <= 0)
).sum()

print(
    "EMA <= 0:",
    ema_error
)


# RSI phải nằm trong 0-100
rsi_error = (
    (features["rsi14"] < 0)
    | (features["rsi14"] > 100)
).sum()

print(
    "RSI ngoài [0,100]:",
    rsi_error
)


# Volume ratio không được âm
volume_error = (
    features["volume_ratio"] < 0
).sum()

print(
    "Volume Ratio âm:",
    volume_error
)


# ============================================================
# 10. KẾT LUẬN
# ============================================================

print("\n" + "=" * 60)
print("KẾT QUẢ CUỐI CÙNG")
print("=" * 60)


if (
    len(df) == len(features)
    and len(missing_columns) == 0
    and ema_error == 0
    and rsi_error == 0
    and volume_error == 0
):

    print("✅ BULK FEATURES TEST: PASS")

else:

    print("❌ BULK FEATURES TEST: CẦN KIỂM TRA")