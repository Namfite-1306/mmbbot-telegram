import pandas as pd
import numpy as np

from technical_engine import (
    calculate_stop_loss,
    evaluate_exit
)


# =========================================================
# 1. TECHNICAL ENTRY
# =========================================================

def is_entry_signal(row):
    """
    Entry tại cuối ngày T.
    Việc thực thi lệnh sẽ được thực hiện ở ngày T+1.
    """

    return (
        bool(row["breakout"])
        and bool(row["volume_ok"])
        and bool(row["trend_ok"])
        and bool(row["rsi_ok"])
        and bool(row["distance_ok"])
    )


# =========================================================
# 2. PREPARE TECHNICAL DATA
# =========================================================

def prepare_technical_data(df):
    df = df.copy()

    df["date"] = pd.to_datetime(
        df["date"],
        errors="coerce"
    )

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "ema20",
        "ema50",
        "rsi14",
        "atr14",
        "volume_ratio",
        "breakout_level",
        "ema_distance_atr"
    ]

    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

    # Technical entry conditions
    df["volume_ok"] = (
        df["volume_ratio"] >= 1.5
    )

    df["trend_ok"] = (
        (df["close"] > df["ema20"])
        & (df["ema20"] > df["ema50"])
    )

    df["rsi_ok"] = (
        df["rsi14"] > 50
    )

    df["distance_ok"] = (
        df["ema_distance_atr"] <= 3
    )

    df = df.sort_values(
        "date"
    ).reset_index(drop=True)

    return df


# =========================================================
# 3. PREPARE FUNDAMENTAL DATA
# =========================================================

def prepare_fundamental_data(df):
    df = df.copy()

    df["report_period"] = pd.to_datetime(
        df["report_period"],
        errors="coerce"
    )

    df["announcement_date"] = pd.to_datetime(
        df["announcement_date"],
        errors="coerce"
    )

    df = df.sort_values(
        [
            "symbol",
            "announcement_date",
            "report_period"
        ]
    )

    return df


# =========================================================
# 4. GET FUNDAMENTAL AS OF TRADING DATE
# =========================================================

def get_fundamental_asof(
    fundamental_df,
    symbol,
    trading_date
):
    """
    Lấy báo cáo tài chính mới nhất mà thị trường
    đã có thông tin tại trading_date.

    Điều kiện quan trọng:

        announcement_date <= trading_date
    """

    data = fundamental_df[
        (
            fundamental_df["symbol"]
            == symbol
        )
        & (
            fundamental_df["announcement_date"]
            <= trading_date
        )
    ]

    if data.empty:
        return None

    data = data.sort_values(
        [
            "announcement_date",
            "report_period"
        ]
    )

    return data.iloc[-1]


# =========================================================
# 5. FUNDAMENTAL FILTER
# =========================================================

def fundamental_pass(
    fundamental_df,
    symbol,
    trading_date,
    threshold=50
):
    row = get_fundamental_asof(
        fundamental_df,
        symbol,
        trading_date
    )

    if row is None:
        return False, None

    score = row.get(
        "fundamental_score_100",
        np.nan
    )

    if pd.isna(score):
        return False, row

    return (
        score >= threshold,
        row
    )


# =========================================================
# 6. SINGLE STOCK BACKTEST
# =========================================================

def backtest_stock(
    technical_df,
    fundamental_df=None,
    symbol=None,
    use_fundamental=False,
    fundamental_threshold=50,
    initial_capital=100_000_000
):

    df = prepare_technical_data(
        technical_df
    )

    if symbol is None and "symbol" in df.columns:
        symbol = df["symbol"].iloc[0]

    trades = []

    in_position = False

    entry_date = None
    entry_price = None
    stop_loss = None
    entry_fundamental_score = None

    # -----------------------------------------------------
    # Chỉ dùng đủ dữ liệu có thể tính indicator
    # -----------------------------------------------------

    for i in range(len(df) - 1):

        row = df.iloc[i]
        next_row = df.iloc[i + 1]

        current_date = row["date"]

        # =================================================
        # ENTRY
        # =================================================

        if not in_position:

            technical_entry = is_entry_signal(
                row
            )

            if not technical_entry:
                continue

            # ---------------------------------------------
            # Fundamental filter
            # ---------------------------------------------

            fundamental_row = None

            if use_fundamental:

                passed, fundamental_row = (
                    fundamental_pass(
                        fundamental_df,
                        symbol,
                        current_date,
                        fundamental_threshold
                    )
                )

                if not passed:
                    continue

            # ---------------------------------------------
            # Execute next day
            # ---------------------------------------------

            entry_date = next_row["date"]

            entry_price = next_row["open"]

            if pd.isna(entry_price):
                continue

            stop_loss = calculate_stop_loss(
                entry_price,
                row["atr14"]
            )

            if fundamental_row is not None:
                entry_fundamental_score = (
                    fundamental_row[
                        "fundamental_score_100"
                    ]
                )

            in_position = True

            continue

        # =================================================
        # EXIT
        # =================================================

        exit_signal = evaluate_exit(
            close=row["close"],
            ema20=row["ema20"],
            ema50=row["ema50"],
            rsi14=row["rsi14"],
            stop_loss=stop_loss
        )

        if exit_signal["status"] == "EXIT":

            exit_date = next_row["date"]

            exit_price = next_row["open"]

            if pd.isna(exit_price):
                continue

            return_pct = (
                (
                    exit_price
                    - entry_price
                )
                / entry_price
            ) * 100

            trades.append({
                "symbol": symbol,
                "entry_date": entry_date,
                "entry_price": entry_price,
                "exit_date": exit_date,
                "exit_price": exit_price,
                "return_pct": return_pct,
                "win": return_pct > 0,
                "fundamental_score":
                    entry_fundamental_score
            })

            in_position = False

            entry_date = None
            entry_price = None
            stop_loss = None
            entry_fundamental_score = None

    # =====================================================
    # FORCE EXIT AT LAST AVAILABLE CLOSE
    # =====================================================

    if in_position:

        last_row = df.iloc[-1]

        exit_date = last_row["date"]

        exit_price = last_row["close"]

        if not pd.isna(exit_price):

            return_pct = (
                (
                    exit_price
                    - entry_price
                )
                / entry_price
            ) * 100

            trades.append({
                "symbol": symbol,
                "entry_date": entry_date,
                "entry_price": entry_price,
                "exit_date": exit_date,
                "exit_price": exit_price,
                "return_pct": return_pct,
                "win": return_pct > 0,
                "fundamental_score":
                    entry_fundamental_score
            })

    return pd.DataFrame(trades)


# =========================================================
# 7. BACKTEST MULTIPLE STOCKS
# =========================================================

def backtest_multiple_stocks(
    technical_data,
    fundamental_data=None,
    symbols=None,
    use_fundamental=False,
    fundamental_threshold=50,
    initial_capital=100_000_000
):

    all_trades = []

    if symbols is None:

        symbols = (
            technical_data["symbol"]
            .dropna()
            .unique()
            .tolist()
        )

    for symbol in symbols:

        stock_data = technical_data[
            technical_data["symbol"]
            == symbol
        ].copy()

        if stock_data.empty:
            continue

        trades = backtest_stock(
            technical_df=stock_data,
            fundamental_df=fundamental_data,
            symbol=symbol,
            use_fundamental=use_fundamental,
            fundamental_threshold=fundamental_threshold,
            initial_capital=initial_capital
        )

        if not trades.empty:
            all_trades.append(trades)

    if not all_trades:
        return pd.DataFrame()

    return pd.concat(
        all_trades,
        ignore_index=True
    )


# =========================================================
# 8. PERFORMANCE METRICS
# =========================================================

def calculate_performance(
    trades,
    initial_capital=100_000_000
):

    if trades is None or trades.empty:

        return {
            "total_trades": 0,
            "win_rate": 0,
            "total_return_pct": 0,
            "average_return_pct": 0,
            "profit_factor": 0,
            "max_drawdown_pct": 0
        }

    trades = trades.copy()
    # Sắp xếp giao dịch theo thời gian thực tế
    trades["entry_date"] = pd.to_datetime(
        trades["entry_date"],
        errors="coerce"
    )

    trades = trades.sort_values(
        "entry_date"
    ).reset_index(drop=True)

    trades["return_decimal"] = (
        trades["return_pct"] / 100
    )

    # -----------------------------------------------------
    # Win rate
    # -----------------------------------------------------

    win_rate = (
        trades["win"].mean()
        * 100
    )

    # -----------------------------------------------------
    # Simple compounded return
    # -----------------------------------------------------

    equity = initial_capital

    equity_curve = []

    for ret in trades[
        "return_decimal"
    ]:

        equity *= (
            1 + ret
        )

        equity_curve.append(
            equity
        )

    total_return_pct = (
        (
            equity
            / initial_capital
        ) - 1
    ) * 100

    # -----------------------------------------------------
    # Average trade
    # -----------------------------------------------------

    average_return_pct = (
        trades["return_pct"]
        .mean()
    )

    # -----------------------------------------------------
    # Profit factor
    # -----------------------------------------------------

    gross_profit = trades.loc[
        trades["return_pct"] > 0,
        "return_pct"
    ].sum()

    gross_loss = abs(
        trades.loc[
            trades["return_pct"] < 0,
            "return_pct"
        ].sum()
    )

    if gross_loss == 0:
        profit_factor = np.inf
    else:
        profit_factor = (
            gross_profit
            / gross_loss
        )

    # -----------------------------------------------------
    # Maximum drawdown
    # -----------------------------------------------------

    equity_series = pd.Series(
        equity_curve
    )

    running_max = (
        equity_series
        .cummax()
    )

    drawdown = (
        (
            equity_series
            - running_max
        )
        / running_max
    ) * 100

    max_drawdown_pct = (
        drawdown.min()
    )

    return {
        "total_trades": len(trades),
        "win_rate": win_rate,
        "total_return_pct": total_return_pct,
        "average_return_pct": average_return_pct,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown_pct
    }