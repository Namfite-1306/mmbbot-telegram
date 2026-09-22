from market_data import (
    get_stock_universe,
    collect_all_stocks
)


# =========================================================
# LẤY STOCK UNIVERSE
# =========================================================

universe = get_stock_universe()

print("Tổng số mã:", len(universe))


# =========================================================
# CHỈ TEST 10 MÃ
# =========================================================

test_symbols = universe["symbol"].head(10).tolist()

print("\n10 mã test:")
print(test_symbols)


# =========================================================
# CHẠY COLLECTION
# =========================================================

results = collect_all_stocks(
    symbols=test_symbols,
    start_date="2025-01-01",
    end_date="2026-09-20",
    delay=2
)


# =========================================================
# KẾT QUẢ
# =========================================================

print("\n" + "=" * 60)
print("KẾT QUẢ BULK TEST")
print("=" * 60)

print(
    "\nTổng mã:",
    len(results)
)

print(
    "\nTrạng thái:"
)

print(
    results["status"].value_counts()
)

print(
    "\nChi tiết:"
)

print(results)