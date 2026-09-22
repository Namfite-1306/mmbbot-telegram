import pandas as pd

from technical_engine import (
    calculate_ema_by_symbol,
    calculate_rsi_by_symbol,
    calculate_atr_by_symbol,
    calculate_volume_ratio_by_symbol,
    calculate_breakout_level_by_symbol,
    check_breakout,
    check_trend,
    check_rsi,
    check_distance_from_ema,
)


def calculate_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tính toàn bộ Technical Features cho dữ liệu cổ phiếu.
    """

    df = df.copy()

    # =========================
    # 1. Các chỉ báo chính
    # =========================

    df["ema20"] = calculate_ema_by_symbol(
        df, "close", 20
    )

    df["ema50"] = calculate_ema_by_symbol(
        df, "close", 50
    )

    df["ema200"] = calculate_ema_by_symbol(
        df, "close", 200
    )

    df["rsi14"] = calculate_rsi_by_symbol(
        df, "close", 14
    )

    df["atr14"] = calculate_atr_by_symbol(
        df, 14
    )

    df["volume_ratio"] = calculate_volume_ratio_by_symbol(
        df, 20
    )

    # =========================
    # 2. Breakout
    # =========================

    df["breakout_level"] = calculate_breakout_level_by_symbol(
        df, 20
    )

    df["breakout"] = check_breakout(
        df["close"],
        df["breakout_level"],
        df["volume_ratio"]
    )

    # =========================
    # 3. Trend
    # =========================

    df["trend"] = check_trend(
        df["close"],
        df["ema20"],
        df["ema50"]
    )

    # =========================
    # 4. RSI
    # =========================

    df["rsi_ok"] = check_rsi(
        df["rsi14"]
    )

    # =========================
    # 5. Khoảng cách với EMA20
    # =========================

    df["ema_distance_atr"] = (
        (df["close"] - df["ema20"])
        / df["atr14"]
    )

    df["distance_ok"] = check_distance_from_ema(
        df["close"],
        df["ema20"],
        df["atr14"]
    )

    return df