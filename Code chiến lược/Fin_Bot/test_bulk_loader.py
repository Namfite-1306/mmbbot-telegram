from technical_engine import load_clean_ohlcv_directory


DATA_DIR = "data/processed/ohlcv"


df = load_clean_ohlcv_directory(
    DATA_DIR
)


print("\n" + "=" * 60)
print("KIỂM TRA BULK LOADER")
print("=" * 60)

print(
    "Số dòng:",
    len(df)
)

print(
    "Số mã:",
    df["symbol"].nunique()
)

print(
    "Số ngày khác nhau:",
    df["date"].nunique()
)

print(
    "\n5 dòng đầu:"
)

print(
    df.head()
)

print(
    "\n5 dòng cuối:"
)

print(
    df.tail()
)

print(
    "\nSố dòng theo mã:"
)

print(
    df.groupby("symbol")
    .size()
    .describe()
)