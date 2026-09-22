from fundamental_engine import (
    build_fundamental_features
)


FILE_PATH = "data/data.xlsx"


# =========================================================
# BUILD
# =========================================================

df = build_fundamental_features(
    FILE_PATH
)


# =========================================================
# LATEST ALL STOCKS
# =========================================================

latest_all = (
    df
    .sort_values("report_period")
    .groupby("symbol")
    .tail(1)
    .sort_values(
        "fundamental_score_100",
        ascending=False
    )
)


print("\n")
print("=" * 130)
print("FUNDAMENTAL V2.3 - LATEST ALL STOCKS")
print("=" * 130)


columns = [
    "symbol",
    "industry",
    "report_period",
    "revenue_growth",
    "profit_growth",
    "roe_ttm",
    "debt_to_equity",
    "cfo_positive",
    "pe",
    "pb",
    "industry_pe_median",
    "industry_pb_median",
    "debt_score",
    "pe_score",
    "pb_score",
    "fundamental_score",
    "max_score",
    "fundamental_score_100"
]


print(
    latest_all[columns]
    .to_string(index=False)
)


# =========================================================
# BANK CHECK
# =========================================================

print("\n")
print("=" * 130)
print("BANK CHECK")
print("=" * 130)


bank_df = latest_all[
    latest_all["industry"] == "BANK"
]


print(
    bank_df[
        [
            "symbol",
            "debt_to_equity",
            "debt_score",
            "pe",
            "industry_pe_median",
            "pb",
            "industry_pb_median",
            "fundamental_score_100"
        ]
    ].to_string(index=False)
)


# =========================================================
# VRE CHECK
# =========================================================

print("\n")
print("=" * 130)
print("VRE CHECK")
print("=" * 130)


vre = latest_all[
    latest_all["symbol"] == "VRE"
]


print(
    vre[
        columns
    ].to_string(index=False)
)


# =========================================================
# LOOK-AHEAD CHECK
# =========================================================

print("\n")
print("=" * 130)
print("LOOK-AHEAD CHECK")
print("=" * 130)


# VRE theo thời gian
vre_history = (
    df[
        df["symbol"] == "VRE"
    ]
    .sort_values("report_period")
)


print(
    vre_history[
        [
            "report_period",
            "pe",
            "industry_pe_median",
            "pb",
            "industry_pb_median"
        ]
    ].tail(10).to_string(index=False)
)


# =========================================================
# SCORE DISTRIBUTION
# =========================================================

print("\n")
print("=" * 130)
print("SCORE DISTRIBUTION")
print("=" * 130)


print(
    df["fundamental_score_100"]
    .describe()
)


print("\n")
print("Fundamental V2.3 completed successfully.")