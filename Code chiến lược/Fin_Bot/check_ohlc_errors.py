import os
import glob
import pandas as pd


RAW_DIR = "data/raw/ohlcv"


files = glob.glob(
    os.path.join(RAW_DIR, "*.csv")
)


total_errors = 0


print("=" * 70)
print("CHI TIẾT CÁC DÒNG OHLC BẤT THƯỜNG")
print("=" * 70)


for file_path in files:

    symbol = os.path.basename(
        file_path
    ).replace(".csv", "")

    df = pd.read_csv(file_path)

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
    ]

    for col in numeric_columns:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df["date"] = pd.to_datetime(
        df["date"],
        errors="coerce"
    )

    # ========================================================
    # Xác định từng loại lỗi
    # ========================================================

    error_high_open = (
        df["high"] < df["open"]
    )

    error_high_close = (
        df["high"] < df["close"]
    )

    error_low_open = (
        df["low"] > df["open"]
    )

    error_low_close = (
        df["low"] > df["close"]
    )

    error_mask = (
        error_high_open
        |
        error_high_close
        |
        error_low_open
        |
        error_low_close
    )

    errors = df[error_mask].copy()

    if errors.empty:
        continue

    print("\n" + "-" * 70)

    print(
        f"MÃ: {symbol}"
    )

    for _, row in errors.iterrows():

        problems = []

        if (
            row["high"]
            < row["open"]
        ):
            problems.append(
                "HIGH < OPEN"
            )

        if (
            row["high"]
            < row["close"]
        ):
            problems.append(
                "HIGH < CLOSE"
            )

        if (
            row["low"]
            > row["open"]
        ):
            problems.append(
                "LOW > OPEN"
            )

        if (
            row["low"]
            > row["close"]
        ):
            problems.append(
                "LOW > CLOSE"
            )

        print(
            f"Date: {row['date'].date()}"
        )

        print(
            f"Open : {row['open']}"
        )

        print(
            f"High : {row['high']}"
        )

        print(
            f"Low  : {row['low']}"
        )

        print(
            f"Close: {row['close']}"
        )

        print(
            "Lỗi  : "
            + ", ".join(problems)
        )

        print()

        total_errors += 1


print("=" * 70)

print(
    f"TỔNG SỐ DÒNG LỖI: {total_errors}"
)

print("=" * 70)