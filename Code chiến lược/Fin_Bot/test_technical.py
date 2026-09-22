import pandas as pd

from technical_engine import (
    load_data,
    clean_data,
    calculate_stop_loss,
    calculate_risk_per_share,
    calculate_position_size,
    apply_capital_limit,
    calculate_market_regime,
    evaluate_entry,
    evaluate_exit
)

from features import calculate_features


# ==========================================
# 1. CẤU HÌNH
# ==========================================

FILE_PATH = "data/data.xlsx"

CAPITAL = 100_000_000
RISK_PERCENT = 0.01


# ==========================================
# 2. ĐỌC VÀ LÀM SẠCH DỮ LIỆU
# ==========================================

df = load_data(FILE_PATH)

df = clean_data(df)


# ==========================================
# 3. TÍNH TECHNICAL FEATURES
# ==========================================

df = calculate_features(df)


# ==========================================
# 4. MARKET REGIME
# ==========================================

vn_index = pd.read_excel(
    FILE_PATH,
    sheet_name="VN-Index"
)

market_regime = calculate_market_regime(
    vn_index,
    df
)

df["date"] = pd.to_datetime(df["date"])

df = df.merge(
    market_regime[
        ["date", "breadth", "market_regime"]
    ],
    on="date",
    how="left"
)


# ==========================================
# 5. STOP LOSS + POSITION SIZE
# ==========================================

df["stop_loss"] = calculate_stop_loss(
    df["close"],
    df["atr14"]
)

df["risk_per_share"] = calculate_risk_per_share(
    df["close"],
    df["stop_loss"]
)

df["position_size"] = calculate_position_size(
    CAPITAL,
    RISK_PERCENT,
    df["risk_per_share"]
)

df["position_size_final"] = apply_capital_limit(
    CAPITAL,
    df["close"],
    df["position_size"]
)


# ==========================================
# 6. ENTRY SIGNAL
# ==========================================

df["entry_signal"] = (
    df["breakout"] &
    df["trend"] &
    df["rsi_ok"] &
    df["distance_ok"]
)


# ==========================================
# 7. KIỂM TRA FEATURES
# ==========================================

print("\n===== KIỂM TRA FEATURES =====")

print(
    df[
        [
            "symbol",
            "date",
            "close",
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
            "distance_ok"
        ]
    ].tail(10)
)


# ==========================================
# 8. KIỂM TRA ENTRY RESULT
# ==========================================

latest = df.iloc[-1]

entry_result = evaluate_entry(latest)

print("\n===== ENTRY RESULT =====")

print("Symbol:", latest["symbol"])
print("Date:", latest["date"])
print("Status:", entry_result["status"])

print("\nĐiều kiện:")

for condition, value in entry_result["conditions"].items():
    print(f"- {condition}: {value}")

print("\nLý do:")

for reason in entry_result["reasons"]:
    print("-", reason)


# ==========================================
# 9. KIỂM TRA EXIT RESULT
# ==========================================

exit_result = evaluate_exit(
    close=latest["close"],
    ema20=latest["ema20"],
    ema50=latest["ema50"],
    rsi14=latest["rsi14"],
    stop_loss=latest["stop_loss"]
)

print("\n===== EXIT RESULT =====")

print("Symbol:", latest["symbol"])
print("Date:", latest["date"])
print("Status:", exit_result["status"])

print("\nLý do:")

for reason in exit_result["reasons"]:
    print("-", reason)


# ==========================================
# 10. KIỂM TRA MỘT ENTRY SIGNAL
# ==========================================

entry_candidates = df[
    df["entry_signal"] == True
]

if len(entry_candidates) > 0:

    signal = entry_candidates.iloc[0]

    print("\n===== KIỂM TRA ENTRY SIGNAL =====")

    print("Symbol:", signal["symbol"])
    print("Date:", signal["date"])
    print("Close:", signal["close"])
    print("Breakout level:", signal["breakout_level"])
    print("Volume ratio:", signal["volume_ratio"])
    print("EMA20:", signal["ema20"])
    print("EMA50:", signal["ema50"])
    print("RSI14:", signal["rsi14"])
    print("ATR14:", signal["atr14"])
    print("EMA distance ATR:", signal["ema_distance_atr"])

    print("\n===== 6 ĐIỀU KIỆN =====")

    print(
        "1. Close > Breakout:",
        signal["close"] > signal["breakout_level"]
    )

    print(
        "2. Volume Ratio >= 1.5:",
        signal["volume_ratio"] >= 1.5
    )

    print(
        "3. Close > EMA20:",
        signal["close"] > signal["ema20"]
    )

    print(
        "4. EMA20 > EMA50:",
        signal["ema20"] > signal["ema50"]
    )

    print(
        "5. RSI14 > 50:",
        signal["rsi14"] > 50
    )

    print(
        "6. Distance <= 3 ATR:",
        signal["ema_distance_atr"] <= 3
    )

else:

    print("\nKhông tìm thấy Entry Signal.")


# ==========================================
# 11. TỔNG HỢP ENTRY SIGNAL
# ==========================================

print("\n===== TỔNG HỢP ENTRY SIGNAL =====")

print(
    entry_candidates[
        [
            "symbol",
            "date",
            "close",
            "stop_loss",
            "risk_per_share",
            "position_size",
            "position_size_final"
        ]
    ].tail(20)
)

print(
    "\nTổng số Entry signals:",
    len(entry_candidates)
)


print("\nSố Entry signals theo từng mã:")

print(
    entry_candidates["symbol"]
    .value_counts()
    .sort_index()
)


# ==========================================
# 12. MARKET REGIME
# ==========================================

print("\n===== MARKET REGIME =====")

print(
    df[
        [
            "date",
            "breadth",
            "market_regime"
        ]
    ].drop_duplicates()
    .tail(10)
)