import os
import glob
import pandas as pd


# ============================================================
# CONFIG
# ============================================================

RAW_DIR = "data/raw/ohlcv"

OUTPUT_FILE = "data/data_quality_report.csv"

EXPECTED_COLUMNS = [
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
]


# ============================================================
# 1. KIỂM TRA 1 FILE
# ============================================================

def check_file(file_path):

    symbol = os.path.basename(file_path).replace(
        ".csv", ""
    )

    result = {
        "symbol": symbol,
        "status": "OK",
        "rows": 0,
        "duplicate_rows": 0,
        "null_rows": 0,
        "invalid_ohlc_rows": 0,
        "negative_volume_rows": 0,
        "date_min": None,
        "date_max": None,
        "error": None,
    }

    try:

        df = pd.read_csv(file_path)

        result["rows"] = len(df)

        # ----------------------------------------------------
        # Kiểm tra columns
        # ----------------------------------------------------

        missing_columns = [
            col
            for col in EXPECTED_COLUMNS
            if col not in df.columns
        ]

        if missing_columns:

            result["status"] = "INVALID_COLUMNS"

            result["error"] = (
                "Thiếu cột: "
                + ", ".join(missing_columns)
            )

            return result

        # ----------------------------------------------------
        # Chuẩn hóa
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Duplicate
        # ----------------------------------------------------

        result["duplicate_rows"] = int(
            df.duplicated(
                subset=["symbol", "date"]
            ).sum()
        )

        # ----------------------------------------------------
        # Null
        # ----------------------------------------------------

        result["null_rows"] = int(
            df[
                EXPECTED_COLUMNS
            ].isnull().any(axis=1).sum()
        )

        # ----------------------------------------------------
        # Date range
        # ----------------------------------------------------

        valid_dates = df["date"].dropna()

        if not valid_dates.empty:

            result["date_min"] = (
                valid_dates.min().strftime("%Y-%m-%d")
            )

            result["date_max"] = (
                valid_dates.max().strftime("%Y-%m-%d")
            )

        # ----------------------------------------------------
        # OHLC validation
        #
        # High phải >= Open
        # High phải >= Close
        # Low phải <= Open
        # Low phải <= Close
        # ----------------------------------------------------

        invalid_ohlc = (
            (df["high"] < df["open"])
            |
            (df["high"] < df["close"])
            |
            (df["low"] > df["open"])
            |
            (df["low"] > df["close"])
        )

        result["invalid_ohlc_rows"] = int(
            invalid_ohlc.sum()
        )

        # ----------------------------------------------------
        # Volume
        # ----------------------------------------------------

        result["negative_volume_rows"] = int(
            (df["volume"] < 0).sum()
        )

        # ----------------------------------------------------
        # Tổng hợp status
        # ----------------------------------------------------

        if result["duplicate_rows"] > 0:

            result["status"] = "CHECK_DUPLICATE"

        elif result["null_rows"] > 0:

            result["status"] = "CHECK_NULL"

        elif result["invalid_ohlc_rows"] > 0:

            result["status"] = "CHECK_OHLC"

        elif result["negative_volume_rows"] > 0:

            result["status"] = "CHECK_VOLUME"

        return result

    except Exception as e:

        result["status"] = "ERROR"

        result["error"] = str(e)

        return result


# ============================================================
# 2. QUÉT TOÀN BỘ FILE
# ============================================================

files = glob.glob(
    os.path.join(
        RAW_DIR,
        "*.csv"
    )
)

print("=" * 60)
print("DATA QUALITY CHECK")
print("=" * 60)

print(
    f"Tìm thấy {len(files)} file CSV"
)


results = []


for i, file_path in enumerate(files, start=1):

    result = check_file(file_path)

    results.append(result)

    if i % 100 == 0:

        print(
            f"Đã kiểm tra: {i}/{len(files)}"
        )


# ============================================================
# 3. DATAFRAME REPORT
# ============================================================

report = pd.DataFrame(results)

report = report.sort_values(
    ["status", "symbol"]
).reset_index(drop=True)


# ============================================================
# 4. LƯU REPORT
# ============================================================

report.to_csv(
    OUTPUT_FILE,
    index=False,
    encoding="utf-8-sig"
)


# ============================================================
# 5. SUMMARY
# ============================================================

print("\n" + "=" * 60)
print("KẾT QUẢ")
print("=" * 60)

print(
    f"Tổng số file: {len(report)}"
)

print("\nTrạng thái:")

print(
    report["status"].value_counts()
)


print("\nTổng số dòng:")

print(
    report["rows"].sum()
)


print("\nTổng duplicate:")

print(
    report["duplicate_rows"].sum()
)


print("\nTổng null:")

print(
    report["null_rows"].sum()
)


print("\nTổng OHLC không hợp lệ:")

print(
    report["invalid_ohlc_rows"].sum()
)


print("\nTổng volume âm:")

print(
    report["negative_volume_rows"].sum()
)


# ============================================================
# 6. CÁC MÃ CẦN KIỂM TRA
# ============================================================

problem_report = report[
    report["status"] != "OK"
]

print("\n" + "=" * 60)
print("CÁC MÃ CẦN KIỂM TRA")
print("=" * 60)

if problem_report.empty:

    print(
        "Không phát hiện vấn đề dữ liệu."
    )

else:

    print(
        problem_report.to_string(
            index=False
        )
    )


print("\nĐã lưu:")

print(
    OUTPUT_FILE
)
