import pandas as pd

from technical_engine import (
    calculate_stop_loss,
    calculate_risk_per_share,
    calculate_position_size,
    evaluate_exit,
)


# ============================================================
# 1. TÍNH SỐ CỔ PHIẾU CÓ THỂ MUA
# ============================================================

def calculate_shares_for_trade(
    capital,
    entry_price,
    stop_loss,
    risk_percent=0.01,
    lot_size=100,
):
    """
    Tính số cổ phiếu dựa trên:
    - Risk mỗi lệnh
    - Stop Loss
    - Giới hạn vốn hiện có

    Trả về số cổ phiếu là bội số của lot_size.
    """

    risk_per_share = calculate_risk_per_share(
        entry_price,
        stop_loss,
    )

    if risk_per_share <= 0:
        return 0

    position_size = calculate_position_size(
        capital=capital,
        risk_percent=risk_percent,
        risk_per_share=risk_per_share,
        lot_size=lot_size,
    )

    max_shares = (
        capital // entry_price // lot_size
    ) * lot_size

    shares = min(
        position_size,
        max_shares,
    )

    return int(shares)


# ============================================================
# 2. PORTFOLIO BACKTEST
# ============================================================

def portfolio_backtest(
    technical_data,
    fundamental_data=None,
    symbols=None,
    start_date=None,
    end_date=None,
    use_fundamental=False,
    fundamental_threshold=50,
    initial_capital=100_000_000,
    risk_percent=0.01,
    lot_size=100,
):
    """
    Portfolio backtest.

    Nguyên tắc thời gian:
    - Signal được xác định bằng dữ liệu đóng cửa ngày T.
    - Entry được thực hiện tại Open của phiên tiếp theo.
    - Exit được thực hiện tại Open của phiên tiếp theo.
    - ATR dùng cho Stop Loss là ATR của ngày tín hiệu T.

    Như vậy không sử dụng dữ liệu của ngày T để giao dịch tại Open T.
    """

    df = technical_data.copy()

    df["date"] = pd.to_datetime(
        df["date"],
        errors="coerce",
    )

    df = df.dropna(subset=["date"])

    df = df.sort_values(
        ["date", "symbol"]
    ).reset_index(drop=True)

    if symbols is not None:
        df = df[df["symbol"].isin(symbols)]

    df = df.reset_index(drop=True)

    if start_date is not None:
        start_date = pd.to_datetime(start_date)
    else:
        start_date = df["date"].min()

    if end_date is not None:
        end_date = pd.to_datetime(end_date)
    else:
        end_date = df["date"].max()

    # --------------------------------------------------------
    # Fundamental
    # --------------------------------------------------------

    if fundamental_data is not None:
        fundamental = fundamental_data.copy()

        fundamental["announcement_date"] = pd.to_datetime(
            fundamental["announcement_date"],
            errors="coerce",
        )

        fundamental = fundamental.sort_values(
            "announcement_date"
        )
    else:
        fundamental = None

    # --------------------------------------------------------
    # Portfolio state
    # --------------------------------------------------------

    cash = float(initial_capital)

    # Vị thế đã thực sự được mua.
    positions = {}

    # Lệnh Entry đã được phát hiện ở ngày T,
    # chờ thực hiện ở Open phiên kế tiếp.
    pending_entries = {}

    # Lệnh Exit đã được phát hiện ở ngày T,
    # chờ thực hiện ở Open phiên kế tiếp.
    pending_exits = {}

    trades = []
    equity_history = []

    # ========================================================
    # DUYỆT THEO NGÀY
    # ========================================================

    dates = sorted(df["date"].unique())

    for current_date in dates:

        daily_data = df[
            df["date"] == current_date
        ]

        # ====================================================
        # 1. THỰC HIỆN EXIT ĐÃ PHÁT HIỆN Ở NGÀY TRƯỚC
        # ====================================================

        for symbol in list(pending_exits.keys()):

            row = daily_data[
                daily_data["symbol"] == symbol
            ]

            # Mã không giao dịch trong ngày này.
            # Chờ phiên giao dịch tiếp theo của chính mã đó.
            if row.empty:
                continue

            if symbol not in positions:
                del pending_exits[symbol]
                continue

            row = row.iloc[0]
            position = positions[symbol]

            exit_price = row["open"]
            shares = position["shares"]

            proceeds = shares * exit_price
            cash += proceeds

            pnl = (
                exit_price
                - position["entry_price"]
            ) * shares

            return_pct = (
                (
                    exit_price
                    / position["entry_price"]
                ) - 1
            ) * 100

            trades.append({
                "symbol": symbol,
                "entry_date": position["entry_date"],
                "exit_date": current_date,
                "entry_price": position["entry_price"],
                "exit_price": exit_price,
                "shares": shares,
                "capital_used": position["capital_used"],
                "pnl": pnl,
                "return_pct": return_pct,
                "win": pnl > 0,
                "fundamental_score": position[
                    "fundamental_score"
                ],
                "exit_signal_date": pending_exits[symbol]["signal_date"],
                "exit_reason": pending_exits[symbol]["reason"],
            })

            del positions[symbol]
            del pending_exits[symbol]

        # ====================================================
        # 2. THỰC HIỆN ENTRY ĐÃ PHÁT HIỆN Ở NGÀY TRƯỚC
        # ====================================================

        for symbol in list(pending_entries.keys()):

            row = daily_data[
                daily_data["symbol"] == symbol
            ]

            if row.empty:
                continue

            # Nếu vì lý do nào đó đã có vị thế,
            # hủy Entry đang chờ.
            if symbol in positions:
                del pending_entries[symbol]
                continue

            row = row.iloc[0]
            order = pending_entries[symbol]

            entry_price = row["open"]

            # ATR lấy từ ngày tín hiệu T,
            # không lấy ATR của ngày thực hiện T+1.
            stop_loss = calculate_stop_loss(
                entry_price,
                order["atr14"],
            )

            shares = calculate_shares_for_trade(
                capital=cash,
                entry_price=entry_price,
                stop_loss=stop_loss,
                risk_percent=risk_percent,
                lot_size=lot_size,
            )

            if shares > 0:
                capital_used = shares * entry_price

                if capital_used <= cash:
                    cash -= capital_used

                    positions[symbol] = {
                        "entry_date": current_date,
                        "entry_price": entry_price,
                        "shares": shares,
                        "capital_used": capital_used,
                        "stop_loss": stop_loss,
                        "fundamental_score": order[
                            "fundamental_score"
                        ],
                    }

            # Dù mua được hay không, lệnh chờ này đã được xử lý.
            del pending_entries[symbol]

        # ====================================================
        # 3. TÌM TÍN HIỆU EXIT Ở NGÀY HIỆN TẠI
        #
        # Signal T -> Exit Open T+1
        # ====================================================

        for symbol in list(positions.keys()):

            if symbol in pending_exits:
                continue

            row = daily_data[
                daily_data["symbol"] == symbol
            ]

            if row.empty:
                continue

            row = row.iloc[0]
            position = positions[symbol]

            # Chỉ đánh giá tín hiệu bằng dữ liệu T.
            exit_signal = evaluate_exit(
                close=row["close"],
                ema20=row["ema20"],
                ema50=row["ema50"],
                rsi14=row["rsi14"],
                stop_loss=position["stop_loss"],
            )

            if exit_signal["status"] == "EXIT":

                pending_exits[symbol] = {
                    "signal_date":current_date,
                    "reason": ";" .join(
                        exit_signal["reasons"]
                    )
                }
                

        # ====================================================
        # 4. TÌM TÍN HIỆU ENTRY Ở NGÀY HIỆN TẠI
        #
        # Signal T -> Entry Open T+1
        # ====================================================

        # Chỉ phát hiện Entry trong khoảng backtest.
        if current_date >= start_date and current_date <= end_date:

            for _, row in daily_data.iterrows():

                symbol = row["symbol"]

                # Đã có vị thế hoặc đã có Entry chờ.
                if symbol in positions or symbol in pending_entries:
                    continue

                # Nếu đang chờ Exit thì không mở Entry mới.
                if symbol in pending_exits:
                    continue

                entry_conditions = (
                    bool(row.get("breakout", False))
                    and bool(row.get("volume_ok", False))
                    and bool(row.get("trend_ok", False))
                    and bool(row.get("rsi_ok", False))
                    and bool(row.get("distance_ok", False))
                )

                if not entry_conditions:
                    continue

                # --------------------------------------------
                # Fundamental filter
                # --------------------------------------------

                fundamental_score = None

                if use_fundamental:

                    if fundamental is None:
                        continue

                    available = fundamental[
                        (fundamental["symbol"] == symbol)
                        & (
                            fundamental["announcement_date"]
                            <= current_date
                        )
                    ]

                    if available.empty:
                        continue

                    latest = available.iloc[-1]

                    fundamental_score = latest[
                        "fundamental_score_100"
                    ]

                    if (
                        pd.isna(fundamental_score)
                        or fundamental_score < fundamental_threshold
                    ):
                        continue

                # --------------------------------------------
                # Tìm phiên giao dịch kế tiếp của chính mã.
                # --------------------------------------------

                symbol_data = df[
                    (df["symbol"] == symbol)
                    & (df["date"] > current_date)
                ].sort_values("date")

                if symbol_data.empty:
                    continue

                next_row = symbol_data.iloc[0]

                # Không đặt lệnh thực hiện ngoài thời gian backtest.
                if next_row["date"] > end_date:
                    continue

                # Chỉ lưu Signal + ATR.
                # Giá Open thật sự chỉ được biết ở ngày T+1.
                pending_entries[symbol] = {
                    "signal_date": current_date,
                    "execution_date": next_row["date"],
                    "atr14": row["atr14"],
                    "fundamental_score": fundamental_score,
                }

        # ====================================================
        # 5. TÍNH EQUITY CUỐI NGÀY
        # ====================================================

        equity = cash

        for symbol, position in positions.items():

            row = daily_data[
                daily_data["symbol"] == symbol
            ]

            if row.empty:
                # Không giao dịch trong ngày -> dùng giá cuối cùng
                # đã biết của vị thế.
                current_price = position["entry_price"]
            else:
                current_price = row.iloc[0]["close"]

            market_value = (
                position["shares"]
                * current_price
            )

            equity += market_value

        equity_history.append({
            "date": current_date,
            "cash": cash,
            "positions": len(positions),
            "equity": equity,
        })

    # ========================================================
    # 6. ĐÓNG CÁC VỊ THẾ CÒN LẠI CUỐI KỲ
    # ========================================================

    if positions:

        for symbol, position in list(positions.items()):

            symbol_data = df[
                df["symbol"] == symbol
            ].sort_values("date")

            # Chỉ lấy dữ liệu trong khoảng backtest.
            symbol_data = symbol_data[
                symbol_data["date"] <= end_date
            ]

            if symbol_data.empty:
                continue

            last_row = symbol_data.iloc[-1]

            exit_price = last_row["close"]
            shares = position["shares"]

            proceeds = shares * exit_price
            cash += proceeds

            pnl = (
                exit_price
                - position["entry_price"]
            ) * shares

            return_pct = (
                (
                    exit_price
                    / position["entry_price"]
                ) - 1
            ) * 100

            trades.append({
                "symbol": symbol,
                "entry_date": position["entry_date"],
                "exit_date": last_row["date"],
                "entry_price": position["entry_price"],
                "exit_price": exit_price,
                "shares": shares,
                "capital_used": position["capital_used"],
                "pnl": pnl,
                "return_pct": return_pct,
                "win": pnl > 0,
                "fundamental_score": position[
                    "fundamental_score"
                ],
                "exit_reason": "Cuối kỳ backtest",
            })

            del positions[symbol]

    # ========================================================
    # 7. DATAFRAME
    # ========================================================

    trades_df = pd.DataFrame(trades)

    equity_df = pd.DataFrame(
        equity_history
    )

    return trades_df, equity_df


# ============================================================
# 8. PERFORMANCE
# ============================================================

def calculate_portfolio_performance(
    trades_df,
    equity_df,
    initial_capital=100_000_000,
):
    """
    Tính các chỉ số performance của portfolio.
    """

    final_capital = (
        equity_df["equity"].iloc[-1]
        if not equity_df.empty
        else initial_capital
    )

    total_return_pct = (
        (
            final_capital
            / initial_capital
        ) - 1
    ) * 100

    if trades_df.empty:
        win_rate = 0
        average_return = 0
        profit_factor = 0
    else:
        win_rate = (
            trades_df["win"].mean()
            * 100
        )

        average_return = (
            trades_df["return_pct"].mean()
        )

        gross_profit = trades_df.loc[
            trades_df["pnl"] > 0,
            "pnl",
        ].sum()

        gross_loss = abs(
            trades_df.loc[
                trades_df["pnl"] < 0,
                "pnl",
            ].sum()
        )

        if gross_loss == 0:
            profit_factor = float("inf")
        else:
            profit_factor = (
                gross_profit
                / gross_loss
            )

    if equity_df.empty:
        max_drawdown_pct = 0
    else:
        equity = equity_df["equity"]
        running_max = equity.cummax()

        drawdown = (
            equity
            / running_max
            - 1
        ) * 100

        max_drawdown_pct = drawdown.min()

    return {
        "initial_capital": initial_capital,
        "final_capital": final_capital,
        "total_return_pct": total_return_pct,
        "total_trades": len(trades_df),
        "win_rate": win_rate,
        "average_return_pct": average_return,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown_pct,
    }
