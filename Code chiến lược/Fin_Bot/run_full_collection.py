from market_data import (
    get_stock_universe,
    collect_all_stocks
)


print("=" * 60)
print("FINTECH BOT - FULL DATA COLLECTION")
print("=" * 60)


# =========================================================
# 1. LẤY STOCK UNIVERSE
# =========================================================

universe = get_stock_universe()

symbols = universe["symbol"].tolist()

print(
    f"\nTổng số mã cần xử lý: {len(symbols)}"
)


# =========================================================
# 2. CHẠY BULK COLLECTION
# =========================================================

results = collect_all_stocks(
    symbols=symbols,
    start_date="2025-01-01",
    end_date="2026-09-20",
    delay=2
)


# =========================================================
# 3. TỔNG KẾT
# =========================================================

print("\n")
print("=" * 60)
print("KẾT QUẢ CUỐI")
print("=" * 60)

print(
    f"\nTổng số mã: {len(results)}"
)

print("\nTrạng thái:")

print(
    results["status"].value_counts()
)


print("\nTổng số dòng:")

print(
    results["rows"].sum()
)


# =========================================================
# 4. DANH SÁCH FAILED
# =========================================================

failed = results[
    results["status"] == "FAILED"
]

print("\n" + "=" * 60)
print("DANH SÁCH MÃ FAILED")
print("=" * 60)

if failed.empty:

    print("✓ Không có mã FAILED")

else:

    print(
        failed[
            ["symbol", "error"]
        ].to_string(index=False)
    )