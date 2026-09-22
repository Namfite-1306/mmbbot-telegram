import os
import glob
import pandas as pd


# ============================================================
# CONFIG
# ============================================================

RAW_DIR = "data/raw/ohlcv"
CLEAN_DIR = "data/processed/ohlcv"

os.makedirs(
    CLEAN_DIR,
    exist_ok=True
)


# ============================================================
# 1. CLEAN 1 FILE
# ============================================================

def clean_one_file(file_path):

    symbol = os.path.basename(
        file_path
    ).replace(".csv", "")

    df = pd.read_csv(file_path)

    # --------------------------------------------------------
    # Chuẩn hóa
    # --------------------------------------------------------

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
    ]

    for col in numeric_columns:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    original_rows = len(df)

    # --------------------------------------------------------
    # Duplicate
    # --------------------------------------------------------

    duplicate_mask = df.duplicated(
        subset=["symbol", "date"],
        keep="first"
    )

    duplicate_count = int(
        duplicate_mask.sum()
    )

    df = df[
        ~duplicate_mask
    ].copy()

    # --------------------------------------------------------
    # Null
    # --------------------------------------------------------

    null_mask = df[
        [
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    ].isnull().any(axis=1)

    null_count = int(
        null_mask.sum()
    )

    df = df[
        ~null_mask
    ].copy()

    # --------------------------------------------------------
    # OHLC bất thường
    # --------------------------------------------------------

    invalid_ohlc_mask = (
        (df["high"] < df["open"])
        |
        (df["high"] < df["close"])
        |
        (df["low"] > df["open"])
        |
        (df["low"] > df["close"])
    )

    invalid_ohlc_count = int(
        invalid_ohlc_mask.sum()
    )

    # --------------------------------------------------------
    # Loại dòng OHLC bất thường
    # --------------------------------------------------------

    df = df[
        ~invalid_ohlc_mask
    ].copy()


    # --------------------------------------------------------
    # Volume âm
    #
    # Tạo mask SAU khi DataFrame đã thay đổi
    # để tránh lỗi reindex.
    # --------------------------------------------------------

    negative_volume_mask = (
        df["volume"] < 0
    )

    negative_volume_count = int(
        negative_volume_mask.sum()
    )

    df = df[
        ~negative_volume_mask
    ].copy()
    # --------------------------------------------------------
    # Sort
    # --------------------------------------------------------

    df = df.sort_values(
        "date"
    ).reset_index(drop=True)

    # --------------------------------------------------------
    # Lưu CLEAN DATA
    # --------------------------------------------------------

    output_path = os.path.join(
        CLEAN_DIR,
        f"{symbol}.csv"
    )

    df.to_csv(
        output_path,
        index=False
    )

    return {
        "symbol": symbol,
        "original_rows": original_rows,
        "clean_rows": len(df),
        "removed_duplicates": duplicate_count,
        "removed_nulls": null_count,
        "removed_invalid_ohlc": invalid_ohlc_count,
        "removed_negative_volume": negative_volume_count,
    }


# ============================================================
# 2. CHẠY TOÀN BỘ
# ============================================================

files = glob.glob(
    os.path.join(
        RAW_DIR,
        "*.csv"
    )
)

print("=" * 60)
print("CLEAN OHLCV DATA")
print("=" * 60)

print(
    f"Tìm thấy {len(files)} file RAW"
)


results = []


for i, file_path in enumerate(
    files,
    start=1
):

    result = clean_one_file(
        file_path
    )

    results.append(result)

    if i % 100 == 0:

        print(
            f"Đã xử lý: {i}/{len(files)}"
        )


# ============================================================
# 3. REPORT
# ============================================================

report = pd.DataFrame(
    results
)

report.to_csv(
    "data/cleaning_report.csv",
    index=False,
    encoding="utf-8-sig"
)


# ============================================================
# 4. SUMMARY
# ============================================================

print("\n" + "=" * 60)
print("KẾT QUẢ CLEANING")
print("=" * 60)

print(
    "Tổng file:",
    len(report)
)

print(
    "Tổng dòng RAW:",
    report["original_rows"].sum()
)

print(
    "Tổng dòng CLEAN:",
    report["clean_rows"].sum()
)

print(
    "Duplicate bị loại:",
    report["removed_duplicates"].sum()
)

print(
    "Null bị loại:",
    report["removed_nulls"].sum()
)

print(
    "OHLC bất thường bị loại:",
    report["removed_invalid_ohlc"].sum()
)

print(
    "Volume âm bị loại:",
    report["removed_negative_volume"].sum()
)

print("\nĐã lưu:")

print(
    "data/cleaning_report.csv"
)

print(
    "data/processed/ohlcv/"
)