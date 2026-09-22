from market_data import (
    collect_with_retry,
    load_raw_ohlcv
)


# =========================================================
# TEST 1: Lấy + validate + lưu FPT
# =========================================================

result = collect_with_retry(
    symbol="FPT",
    start_date="2025-01-01",
    end_date="2026-09-20"
)

print("\n===== KẾT QUẢ =====")
print(result)


# =========================================================
# TEST 2: Đọc lại dữ liệu từ storage
# =========================================================

df = load_raw_ohlcv("FPT")

print("\n===== ĐỌC LẠI STORAGE =====")

if df is not None:

    print(
        f"Số dòng: {len(df)}"
    )

    print(
        f"Các cột: {df.columns.tolist()}"
    )

    print("\n5 dòng cuối:")
    print(df.tail())

else:

    print("Không tìm thấy file FPT")