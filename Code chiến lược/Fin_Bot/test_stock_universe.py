from stock_universe import (
    load_stock_metadata,
    get_stock_universe,
    get_stock_info
)


print("===== STOCK METADATA =====")

df = load_stock_metadata()

print(df)


print("\n===== STOCK UNIVERSE =====")

universe = get_stock_universe()

print(universe)
print(f"Số lượng mã: {len(universe)}")


print("\n===== TEST FPT =====")

info = get_stock_info("FPT")

print(info)


print("\n===== TEST VRE =====")

info = get_stock_info("VRE")

print(info)


print("\n===== TEST UNKNOWN =====")

info = get_stock_info("ABC")

print(info)