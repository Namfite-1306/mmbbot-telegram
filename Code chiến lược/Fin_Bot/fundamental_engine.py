import pandas as pd
import numpy as np


# =========================================================
# 1. LOAD DATA
# =========================================================

def load_fundamental_data(file_path):
    df = pd.read_excel(
        file_path,
        sheet_name="Tài chính DN"
    )

    print("Đã đọc dữ liệu tài chính!")
    print(f"Số dòng: {len(df)}")
    print(f"Số cột: {len(df.columns)}")

    print("\nCác cột:")
    print(df.columns.tolist())

    return df


# =========================================================
# 2. CLEAN DATA
# =========================================================

def clean_fundamental_data(df):
    df = df.copy()

    # Chuẩn hóa tên cột
    df.columns = (
        df.columns
        .str.strip()
        .str.lower()
    )

    # Chuẩn hóa symbol
    df["symbol"] = (
        df["symbol"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["report_period"] = pd.to_datetime(
    df["report_period"],
    errors="coerce"
    )

    df["announcement_date"] = pd.to_datetime(
        df["announcement_date"],
        errors="coerce"
    )

    # Các biến số
    numeric_columns = [
        "revenue",
        "npat",
        "equity",
        "debt",
        "cfo",
        "eps",
        "pe",
        "pb"
    ]

    for col in numeric_columns:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    # Sắp xếp
    df = df.sort_values(
        ["symbol", "report_period"]
    )

    # Xóa duplicate theo quý
    df = df.drop_duplicates(
        subset=["symbol", "report_period"]
    )

    df = df.reset_index(drop=True)

    return df


# =========================================================
# 3. REVENUE GROWTH YoY
# =========================================================

def calculate_revenue_growth(df):
    df = df.copy()

    # So với cùng quý năm trước
    df["previous_revenue_yoy"] = (
        df.groupby("symbol")["revenue"]
        .shift(4)
    )

    df["revenue_growth"] = np.where(
        df["previous_revenue_yoy"] != 0,
        (
            (
                df["revenue"]
                - df["previous_revenue_yoy"]
            )
            / df["previous_revenue_yoy"]
        ) * 100,
        np.nan
    )

    return df


# =========================================================
# 4. PROFIT GROWTH YoY
# =========================================================

def calculate_profit_growth(df):
    df = df.copy()

    # NPAT cùng quý năm trước
    df["previous_npat_yoy"] = (
        df.groupby("symbol")["npat"]
        .shift(4)
    )

    df["profit_growth"] = np.where(
        df["previous_npat_yoy"] != 0,
        (
            (
                df["npat"]
                - df["previous_npat_yoy"]
            )
            / df["previous_npat_yoy"]
        ) * 100,
        np.nan
    )

    return df


# =========================================================
# 5. TTM NPAT
# =========================================================

def calculate_ttm_npat(df):
    df = df.copy()

    df["npat_ttm"] = (
        df.groupby("symbol")["npat"]
        .transform(
            lambda x: x.rolling(
                window=4,
                min_periods=4
            ).sum()
        )
    )

    return df


# =========================================================
# 6. TTM ROE
# =========================================================

def calculate_roe_ttm(df):
    df = df.copy()

    # V2.1:
    # TTM NPAT / Equity hiện tại
    #
    # Chưa dùng average equity vì dataset hiện tại
    # chưa cung cấp rõ historical average equity theo
    # đúng cách cần thiết cho ROE.

    df["roe_ttm"] = np.where(
        df["equity"] != 0,
        (
            df["npat_ttm"]
            / df["equity"]
        ) * 100,
        np.nan
    )

    return df


# =========================================================
# 7. DEBT / EQUITY
# =========================================================

def calculate_debt_to_equity(df):
    df = df.copy()

    df["debt_to_equity"] = np.where(
        df["equity"] != 0,
        df["debt"] / df["equity"],
        np.nan
    )

    return df


# =========================================================
# 8. CFO
# =========================================================

def calculate_cfo_features(df):
    df = df.copy()

    # CFO dương
    df["cfo_positive"] = df["cfo"] > 0

    # CFO / Revenue
    df["cfo_margin"] = np.where(
        df["revenue"] != 0,
        (
            df["cfo"]
            / df["revenue"]
        ) * 100,
        np.nan
    )

    return df


# =========================================================
# 9. VALUATION
# =========================================================

def calculate_valuation_features(df):
    df = df.copy()

    # P/E hợp lệ
    df["pe_positive"] = (
        df["pe"] > 0
    )

    # P/B hợp lệ
    df["pb_positive"] = (
        df["pb"] > 0
    )

    return df


# =========================================================
# 10. LOAD INDUSTRY METADATA
# =========================================================

def load_stock_metadata(
    metadata_file="data/stock_metadata.csv"
):
    metadata = pd.read_csv(
        metadata_file
    )

    metadata.columns = (
        metadata.columns
        .str.strip()
        .str.lower()
    )

    metadata["symbol"] = (
        metadata["symbol"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    metadata["industry"] = (
        metadata["industry"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    return metadata


# =========================================================
# 11. MERGE INDUSTRY
# =========================================================

def merge_stock_metadata(
    df,
    metadata_file="data/stock_metadata.csv"
):
    df = df.copy()

    metadata = load_stock_metadata(
        metadata_file
    )

    metadata = metadata[
        ["symbol", "exchange", "industry"]
    ].drop_duplicates(
        subset=["symbol"]
    )

    df = df.merge(
        metadata,
        on="symbol",
        how="left"
    )

    return df


# =========================================================
# 12. FUNDAMENTAL FEATURES
# =========================================================

def calculate_fundamental_features(df):
    df = calculate_revenue_growth(df)

    df = calculate_profit_growth(df)

    df = calculate_ttm_npat(df)

    df = calculate_roe_ttm(df)

    df = calculate_debt_to_equity(df)

    df = calculate_cfo_features(df)

    df = calculate_valuation_features(df)

    return df


# =========================================================
# 13. INDUSTRY-AWARE DEBT SCORE
# =========================================================

def calculate_debt_score(df):
    df = df.copy()

    # -----------------------------------------------------
    # BANK:
    # Không dùng Debt / Equity trong scoring chung.
    #
    # Ngân hàng có cấu trúc bảng cân đối khác doanh nghiệp
    # thông thường nên không áp dụng ngưỡng D/E <= 1.
    # -----------------------------------------------------

    is_bank = df["industry"] == "BANK"

    df["debt_score"] = np.nan

    # -----------------------------------------------------
    # NON-BANK
    # -----------------------------------------------------

    non_bank = ~is_bank

    df.loc[
        non_bank & (df["debt_to_equity"] <= 1),
        "debt_score"
    ] = 10

    df.loc[
        non_bank
        & (df["debt_to_equity"] > 1)
        & (df["debt_to_equity"] <= 2),
        "debt_score"
    ] = 5

    df.loc[
        non_bank & (df["debt_to_equity"] > 2),
        "debt_score"
    ] = 0

    return df


# =========================================================
# 14. POINT-IN-TIME INDUSTRY VALUATION
# =========================================================

def calculate_valuation_score(df):
    df = df.copy()

    # Sắp xếp theo ngành và thời gian
    df = df.sort_values(
        ["industry", "report_period", "symbol"]
    ).reset_index(drop=True)

    # =====================================================
    # MEDIAN P/E THEO NGÀNH + TỪNG QUÝ
    # =====================================================

    period_median_pe = (
        df.assign(
            pe_valid=df["pe"].where(df["pe"] > 0)
        )
        .groupby(
            ["industry", "report_period"]
        )["pe_valid"]
        .transform("median")
    )

    # =====================================================
    # MEDIAN P/B THEO NGÀNH + TỪNG QUÝ
    # =====================================================

    period_median_pb = (
        df.assign(
            pb_valid=df["pb"].where(df["pb"] > 0)
        )
        .groupby(
            ["industry", "report_period"]
        )["pb_valid"]
        .transform("median")
    )

    df["industry_pe_median"] = period_median_pe
    df["industry_pb_median"] = period_median_pb

    # =====================================================
    # P/E SCORE
    # =====================================================

    df["pe_score"] = 0

    valid_pe = (
        (df["pe"] > 0)
        & (df["industry_pe_median"] > 0)
    )

    # P/E <= median ngành trong cùng quý
    df.loc[
        valid_pe
        & (
            df["pe"]
            <= df["industry_pe_median"]
        ),
        "pe_score"
    ] = 5

    # P/E > median ngành trong cùng quý
    df.loc[
        valid_pe
        & (
            df["pe"]
            > df["industry_pe_median"]
        ),
        "pe_score"
    ] = 2

    # =====================================================
    # P/B SCORE
    # =====================================================

    df["pb_score"] = 0

    valid_pb = (
        (df["pb"] > 0)
        & (df["industry_pb_median"] > 0)
    )

    # P/B <= median ngành trong cùng quý
    df.loc[
        valid_pb
        & (
            df["pb"]
            <= df["industry_pb_median"]
        ),
        "pb_score"
    ] = 5

    # P/B > median ngành trong cùng quý
    df.loc[
        valid_pb
        & (
            df["pb"]
            > df["industry_pb_median"]
        ),
        "pb_score"
    ] = 2

    return df
# =========================================================
# 15. CFO SCORE
# =========================================================

def calculate_cfo_score(df):
    df = df.copy()

    # CFO dương → 10 điểm
    # CFO âm → 0 điểm

    df["cfo_score"] = np.where(
        df["cfo_positive"],
        10,
        0
    )

    return df


# =========================================================
# 16. COMMON FUNDAMENTAL SCORE
# =========================================================

def calculate_common_score(df):
    df = df.copy()

    # -----------------------------------------------------
    # REVENUE GROWTH
    # 20 điểm
    # -----------------------------------------------------

    df["revenue_score"] = np.where(
        df["revenue_growth"] >= 15,
        20,
        0
    )

    # -----------------------------------------------------
    # PROFIT GROWTH
    # 20 điểm
    # -----------------------------------------------------

    df["profit_score"] = np.where(
        df["profit_growth"] >= 15,
        20,
        0
    )

    # -----------------------------------------------------
    # TTM ROE
    # 20 điểm
    # -----------------------------------------------------

    df["roe_score"] = np.where(
        df["roe_ttm"] >= 15,
        20,
        0
    )

    # -----------------------------------------------------
    # CFO
    # 10 điểm
    # -----------------------------------------------------

    df = calculate_cfo_score(df)

    # -----------------------------------------------------
    # DEBT / EQUITY
    # 10 điểm
    #
    # BANK = NaN / không áp dụng
    # NON-BANK = 0 / 5 / 10
    # -----------------------------------------------------

    df = calculate_debt_score(df)

    # -----------------------------------------------------
    # VALUATION
    #
    # P/E = 5 điểm
    # P/B = 5 điểm
    # -----------------------------------------------------

    df = calculate_valuation_score(df)

    # -----------------------------------------------------
    # TỔNG ĐIỂM
    # -----------------------------------------------------

    score_columns = [
        "revenue_score",
        "profit_score",
        "roe_score",
        "cfo_score",
        "pe_score",
        "pb_score"
    ]

    df["fundamental_score"] = (
        df[score_columns]
        .fillna(0)
        .sum(axis=1)
    )

    # Debt score chỉ cộng cho non-bank
    df["fundamental_score"] += (
        df["debt_score"]
        .fillna(0)
    )

    # Tổng tối đa:
    #
    # Revenue       20
    # Profit        20
    # ROE           20
    # CFO           10
    # Debt          10
    # P/E            5
    # P/B            5
    #
    # = 90 điểm
    #
    # Tuy nhiên BANK không sử dụng Debt Score,
    # nên maximum của BANK = 80.
    #
    # Để tất cả ngành có cùng thang 100,
    # chuẩn hóa lại theo số điểm có thể đạt.

    df["max_score"] = np.where(
        df["industry"] == "BANK",
        80,
        90
    )

    df["fundamental_score_100"] = (
        df["fundamental_score"]
        / df["max_score"]
        * 100
    )

    df = df.sort_values(
    ["symbol", "report_period"]
    ).reset_index(drop=True)

    return df


# =========================================================
# 17. BUILD FUNDAMENTAL FEATURES
# =========================================================

def build_fundamental_features(
    file_path,
    metadata_file="data/stock_metadata.csv"
):

    # Load
    df = load_fundamental_data(
        file_path
    )

    # Clean
    df = clean_fundamental_data(
        df
    )

    # Features
    df = calculate_fundamental_features(
        df
    )

    # Industry metadata
    df = merge_stock_metadata(
        df,
        metadata_file
    )

    # Score
    df = calculate_common_score(
        df
    )

    return df