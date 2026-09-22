from market_data import (
    get_stock_universe,
    get_ohlcv
)


# ==========================================
# 1. Kiểm tra Stock Universe
# ==========================================

print("===== STOCK UNIVERSE =====")

universe = get_stock_universe()

print("Tổng số mã:", len(universe))

print("\nSố lượng theo sàn:")
print(universe["exchange"].value_counts().sort_index())


# ==========================================
# 2. Chọn 1 mã đại diện mỗi sàn
# ==========================================

test_symbols = {
    "HOSE": "FPT",
    "HNX": "APS",
    "UPCOM": "ABC"
}


# ==========================================
# 3. Test OHLCV từng sàn
# ==========================================

for exchange, symbol in test_symbols.items():

    print("\n" + "=" * 50)
    print(f"TEST {exchange} - {symbol}")
    print("=" * 50)

    # Kiểm tra mã có nằm trong Stock Universe không
    stock_info = universe[
        (universe["symbol"] == symbol) &
        (universe["exchange"] == exchange)
    ]

    print("\nThông tin mã:")

    if stock_info.empty:
        print(f"Không tìm thấy {symbol} trên {exchange}")
        continue

    print(
        stock_info[
            ["symbol", "exchange", "organ_name"]
        ].to_string(index=False)
    )

    # Lấy OHLCV
    df = get_ohlcv(
        symbol=symbol,
        start_date="2025-01-01",
        end_date="2026-09-20"
    )

    print("\nSố dòng:", len(df))

    print("\nCác cột:")
    print(df.columns.tolist())

    print("\n5 dòng đầu:")
    print(df.head())

    print("\n5 dòng cuối:")
    print(df.tail())

    print("\nMissing:")
    print(df.isna().sum())

    print("\nDuplicate:")
    print(
        df.duplicated(
            subset=["symbol", "date"]
        ).sum()
    )
# ==========================================
# 4. KIỂM TRA ADJUSTED PRICE
# ==========================================

print("\n" + "=" * 50)
print("KIỂM TRA ADJUSTED PRICE - FPT")
print("=" * 50)

print("\nCác cột hiện tại:")
print(df.columns.tolist())

adjusted_columns = [
    col for col in df.columns
    if "adjust" in col.lower()
    or "adj" in col.lower()
]

if adjusted_columns:
    print("\nCó cột adjusted price:")
    print(adjusted_columns)
    print(df[["date"] + adjusted_columns].tail())

else:
    print("\nKhông có adjusted price trong response hiện tại.")

from market_data import get_ohlcv

fpt = get_ohlcv(
    symbol="FPT",
    start_date="2025-01-01",
    end_date="2026-09-20"
)

print("\n===== KIỂM TRA ADJUSTED PRICE =====")

print("Các cột:")
print(fpt.columns.tolist())

adjusted_columns = [
    col for col in fpt.columns
    if "adjust" in col.lower()
    or "adj" in col.lower()
]

if adjusted_columns:
    print("Có adjusted price:", adjusted_columns)
    print(fpt[["date"] + adjusted_columns].tail())

else:
    print("Không có adjusted price trong response hiện tại.")