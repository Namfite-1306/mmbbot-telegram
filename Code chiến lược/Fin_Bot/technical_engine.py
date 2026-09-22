import pandas as pd
import numpy as np


# ============================================================
# 1. ĐỌC DỮ LIỆU
# ============================================================

def load_data(file_path):
    
    """
    Đọc sheet 'Lịch sử Vietcap' từ file Excel.
    """

    df = pd.read_excel(
        file_path,
        sheet_name="Lịch sử Vietcap"
    )

    print("Đã đọc dữ liệu thành công!")
    print(f"Số dòng: {len(df)}")
    print(f"Số cột: {len(df.columns)}")

    print("\nCác cột:")
    print(df.columns.tolist())

    return df

def clean_data(df):
    df = df.copy()

    df.columns = [col.strip().lower() for col in df.columns]

    if "time" in df.columns:
        df = df.rename(columns={"time": "date"})

    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    numeric_columns = ["open", "high", "low", "close", "volume"]

    for col in numeric_columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values(["symbol", "date"])

    df = df.drop_duplicates(subset=["symbol", "date"])

    df = df.reset_index(drop=True)

    return df
def load_clean_ohlcv_directory(directory_path):
    """
    Đọc toàn bộ dữ liệu OHLCV đã được cleaning
    từ thư mục data/processed/ohlcv/.

    Mỗi mã tương ứng với một file CSV.
    """

    import os
    import glob

    files = glob.glob(
        os.path.join(directory_path, "*.csv")
    )

    all_data = []

    print("=" * 60)
    print("ĐỌC CLEAN OHLCV DATA")
    print("=" * 60)

    print(
        f"Tìm thấy {len(files)} file CSV"
    )

    for i, file_path in enumerate(
        files,
        start=1
    ):

        try:

            df = pd.read_csv(file_path)

            # Chuẩn hóa tên cột
            df.columns = [
                col.strip().lower()
                for col in df.columns
            ]

            # Nếu dữ liệu vẫn dùng time
            if "time" in df.columns:
                df = df.rename(
                    columns={"time": "date"}
                )

            # Chuẩn hóa date
            df["date"] = pd.to_datetime(
                df["date"],
                errors="coerce"
            )

            # Chuẩn hóa numeric
            numeric_columns = [
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]

            for col in numeric_columns:

                df[col] = pd.to_numeric(
                    df[col],
                    errors="coerce"
                )

            # Chỉ giữ dữ liệu hợp lệ
            df = df.dropna(
                subset=[
                    "symbol",
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                ]
            )

            # Sort
            df = df.sort_values(
                ["symbol", "date"]
            )

            # Loại duplicate lần nữa để an toàn
            df = df.drop_duplicates(
                subset=[
                    "symbol",
                    "date"
                ]
            )

            all_data.append(df)

        except Exception as e:

            print(
                f"Lỗi file: {file_path}"
            )

            print(
                f"Chi tiết: {e}"
            )

        if i % 100 == 0:

            print(
                f"Đã đọc: {i}/{len(files)}"
            )

    if not all_data:

        raise ValueError(
            "Không đọc được dữ liệu OHLCV."
        )

    result = pd.concat(
        all_data,
        ignore_index=True
    )

    result = result.sort_values(
        ["symbol", "date"]
    ).reset_index(drop=True)

    print("\nĐọc dữ liệu hoàn tất!")

    print(
        f"Số dòng: {len(result)}"
    )

    print(
        f"Số mã: {result['symbol'].nunique()}"
    )

    print(
        f"Từ ngày: {result['date'].min().date()}"
    )

    print(
        f"Đến ngày: {result['date'].max().date()}"
    )

    return result
def calculate_ema_by_symbol(df, column="close", period=20):
    return (
        df.groupby("symbol")[column]
        .transform(
            lambda x: x.ewm(span=period, adjust=False).mean()
        )
    )
def calculate_rsi(close, period=14):
    delta = close.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    rsi = 100 - (100 / (1 + rs))

    rsi = rsi.where(avg_loss != 0, 100)

    return rsi
def calculate_rsi_by_symbol(df, column="close", period=14):
    return (
        df.groupby("symbol")[column]
        .transform(
            lambda x: calculate_rsi(x, period)
        )
    )
def calculate_atr(high, low, close, period=14):
    previous_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - previous_close).abs()
    tr3 = (low - previous_close).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    atr = true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    return atr
def calculate_atr_by_symbol(df, period=14):

    df = df.copy()

    result = pd.Series(index=df.index, dtype="float64")

    for symbol, group in df.groupby("symbol"):

        atr = calculate_atr(
            group["high"],
            group["low"],
            group["close"],
            period
        )

        result.loc[group.index] = atr.values

    return result
def calculate_volume_ratio(volume, period=20):
    volume_avg = volume.rolling(period).mean()
    volume_ratio = volume / volume_avg
    return volume_ratio
def calculate_volume_ratio_by_symbol(df, period=20):
    return (
        df.groupby("symbol")["volume"]
        .transform(lambda x: x.rolling(period).mean())
        .rdiv(df["volume"])
    )
def calculate_breakout_level(high, period=20):
    breakout_level = high.rolling(period).max().shift(1)
    return breakout_level
def calculate_breakout_level_by_symbol(df, period=20):
    return (
        df.groupby("symbol")["high"]
        .transform(
            lambda x: x.rolling(period).max().shift(1)
        )
    )
def check_breakout(close, breakout_level, volume_ratio):
    breakout = (
        (close > breakout_level) &
        (volume_ratio >= 1.5)
    )

    return breakout

def check_trend(close, ema20, ema50):
    trend = (
        (close > ema20) &
        (ema20 > ema50)
    )

    return trend
def check_rsi(rsi, threshold=50):
    return rsi > threshold
def check_distance_from_ema(close, ema20, atr14, max_atr=3):
    distance = (close - ema20) / atr14
    return distance <= max_atr

def generate_entry_signal(
    breakout,
    trend,
    rsi_ok,
    distance_ok
):
    signal = (
        breakout &
        trend &
        rsi_ok &
        distance_ok
    )

    return signal
def calculate_stop_loss(close, atr14, atr_multiplier=2):
    stop_loss = close - (atr_multiplier * atr14)
    return stop_loss
def calculate_risk_per_share(close, stop_loss):
    risk_per_share = close - stop_loss
    return risk_per_share
def calculate_position_size(
    capital,
    risk_percent,
    risk_per_share,
    lot_size=100
):
    risk_amount = capital * risk_percent

    shares = risk_amount / risk_per_share

    shares = (shares // lot_size) * lot_size

    return shares
def apply_capital_limit(
    capital,
    close,
    position_size,
    lot_size=100
):
    max_shares = (capital // close // lot_size) * lot_size

    position_size_final = position_size.where(
        position_size <= max_shares,
        max_shares
    )

    return position_size_final

def calculate_market_regime(market_df, stock_df):
    market_df = market_df.copy()
    stock_df = stock_df.copy()

    # -----------------------------
    # 1. Chuẩn hóa VN-Index
    # -----------------------------
    market_df["time"] = pd.to_datetime(
        market_df["time"],
        errors="coerce"
    )

    market_df = market_df.sort_values("time")

    market_df["ema50"] = (
        market_df["close"]
        .ewm(span=50, adjust=False)
        .mean()
    )

    market_df["ema200"] = (
        market_df["close"]
        .ewm(span=200, adjust=False)
        .mean()
    )

    # -----------------------------
    # 2. Tính breadth
    # -----------------------------
    stock_df = stock_df.sort_values(["symbol", "date"])

    stock_df["prev_close"] = (
        stock_df
        .groupby("symbol")["close"]
        .shift(1)
    )

    stock_df["advanced"] = (
        stock_df["close"] > stock_df["prev_close"]
    )

    breadth = (
        stock_df
        .groupby("date")["advanced"]
        .mean()
        .rename("breadth")
        .reset_index()
    )

    # -----------------------------
    # 3. Ghép VN-Index + Breadth
    # -----------------------------
    market_df["date"] = market_df["time"].dt.normalize()

    regime_df = market_df.merge(
        breadth,
        on="date",
        how="left"
    )

    # -----------------------------
    # 4. Xác định Market Regime
    # -----------------------------
    regime_df["market_regime"] = "NEUTRAL"

    bull_condition = (
        (regime_df["close"] > regime_df["ema50"]) &
        (regime_df["ema50"] > regime_df["ema200"]) &
        (regime_df["breadth"] >= 0.50)
    )

    bear_condition = (
        regime_df["ema50"] < regime_df["ema200"]
    )

    regime_df.loc[bull_condition, "market_regime"] = "BULL"
    regime_df.loc[bear_condition, "market_regime"] = "BEAR"

    return regime_df[
        [
            "date",
            "close",
            "ema50",
            "ema200",
            "breadth",
            "market_regime"
        ]
    ]
def evaluate_entry(features):
    """
    Đánh giá điều kiện Entry cho một dòng dữ liệu.
    """

    conditions = {
        "breakout": bool(features["breakout"]),
        "trend": bool(features["trend"]),
        "rsi": bool(features["rsi_ok"]),
        "distance": bool(features["distance_ok"]),
    }

    all_conditions_met = all(conditions.values())

    if all_conditions_met:
        status = "ENTRY"
    else:
        status = "NO_ENTRY"

    reasons = []

    if not conditions["breakout"]:
        reasons.append("Chưa Breakout + Volume")

    if not conditions["trend"]:
        reasons.append("Trend chưa đạt")

    if not conditions["rsi"]:
        reasons.append("RSI <= 50")

    if not conditions["distance"]:
        reasons.append("Giá quá xa EMA20")

    if not reasons:
        reasons.append("Đủ tất cả điều kiện Entry")

    return {
        "status": status,
        "conditions": conditions,
        "reasons": reasons
    }
def evaluate_exit(
    close,
    ema20,
    ema50,
    rsi14,
    stop_loss,
    support_level=None
):
    """
    Đánh giá điều kiện thoát vị thế.
    """

    reasons = []

    # 1. Stop Loss
    if close <= stop_loss:
        reasons.append("Giá chạm Stop Loss")

    # 2. Gãy EMA20
    if close < ema20:
        reasons.append("Giá đóng cửa dưới EMA20")

    # 3. EMA20 cắt xuống EMA50
    if ema20 < ema50:
        reasons.append("EMA20 dưới EMA50")

    # 4. RSI suy yếu
    if rsi14 < 50:
        reasons.append("RSI < 50")

    # 5. Gãy vùng hỗ trợ
    if support_level is not None and close < support_level:
        reasons.append("Giá phá vỡ vùng hỗ trợ")

    if reasons:
        status = "EXIT"
    else:
        status = "HOLD"

    return {
        "status": status,
        "reasons": reasons
    }