# data_cleaner.py
# ------------------------------------------------------------
# NHIỆM VỤ DUY NHẤT của file này: nhận dữ liệu THÔ (đọc từ CSV do 2 file
# vietcap_market_data.py / vietcap_fundamental_data.py tạo ra, qua
# raw_data_loader.py / fundamental_data_loader.py) và LÀM SẠCH nó
# trước khi lưu vào database.
#
# "Làm sạch" ở đây gồm:
#   - Chỉ giữ các cột thật sự cần dùng, đổi tên cho thống nhất
#   - Ép đúng kiểu dữ liệu (ngày tháng, số...)
#   - Xử lý dữ liệu thiếu (missing values)
#   - Loại bỏ dòng trùng lặp
#   - Tự tính thêm 1 vài chỉ số đơn giản mà nguồn thô CHƯA có sẵn
#     (vd debt_to_equity từ total_liabilities / total_equity)
#
# File này KHÔNG gọi mạng, KHÔNG đọc/ghi file. Nó chỉ nhận DataFrame
# vào và trả DataFrame sạch ra - giúp dễ viết test và dễ debug.
# ------------------------------------------------------------

import logging
import pandas as pd

logger = logging.getLogger(__name__)


# ------------------------------------------------------------
# LÀM SẠCH DỮ LIỆU GIÁ (Market Data)
# ------------------------------------------------------------

def clean_price_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Làm sạch dữ liệu giá cổ phiếu thô (từ vietcap_market_data.py) ->
    trả về DataFrame với các cột chuẩn:
        symbol, date, open, high, low, close, volume, total_value,
        foreign_buy_volume, foreign_sell_volume, put_through_volume, is_final

    Ghi chú: total_value/foreign_*/put_through_volume chỉ có giá trị cho
    NGÀY HÔM NAY (do giới hạn của Vietcap - xem comment đầu file
    vietcap_market_data.py), các ngày quá khứ sẽ là NaN/rỗng - đây là
    hành vi ĐÚNG như thiết kế, không phải lỗi làm sạch dữ liệu.
    """
    if df.empty:
        return df

    df = df.copy()

    # Chuẩn hoá tên cột: trade_date (tên trong CSV thô) -> date
    df = df.rename(columns={"trade_date": "date"})

    keep_cols = [
        "symbol", "date", "open", "high", "low", "close", "volume",
        "total_value", "foreign_buy_volume", "foreign_sell_volume",
        "put_through_volume", "is_final",
    ]
    df = df[[c for c in keep_cols if c in df.columns]]

    # Ép kiểu ngày tháng chuẩn (trade_date đã ở dạng YYYY-MM-DD sẵn,
    # nhưng vẫn ép qua to_datetime để bắt các dòng ngày bị lỗi/rác)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["date"])

    # Ép kiểu số cho các cột giá/khối lượng/giá trị giao dịch
    numeric_cols = [
        "open", "high", "low", "close", "volume", "total_value",
        "foreign_buy_volume", "foreign_sell_volume", "put_through_volume",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Loại các dòng thiếu giá đóng cửa - dòng này vô nghĩa để lưu
    before = len(df)
    df = df.dropna(subset=["close"])
    dropped = before - len(df)
    if dropped > 0:
        logger.info(f"Đã loại {dropped} dòng thiếu giá đóng cửa")

    # Loại trùng lặp (cùng mã + cùng ngày), giữ dòng CUỐI CÙNG (dữ liệu
    # mới nhất - vd dòng của hôm nay được enrich thêm priceboard sau
    # khi đã có 1 dòng ban đầu chưa enrich, trong merge_csv_dedup phía
    # crawler cũng theo nguyên tắc này)
    df = df.drop_duplicates(subset=["symbol", "date"], keep="last")

    return df.reset_index(drop=True)


# ------------------------------------------------------------
# LÀM SẠCH DỮ LIỆU BÁO CÁO TÀI CHÍNH (Fundamental Data)
# ------------------------------------------------------------

def clean_fundamental_data(df: pd.DataFrame) -> pd.DataFrame:
    """
    Làm sạch dữ liệu báo cáo tài chính thô (từ vietcap_fundamental_data.py)
    -> trả về DataFrame với các cột chuẩn:
        symbol, report_period, report_type, year_report, length_report,
        publish_date, current_assets, non_current_assets, total_assets,
        total_liabilities, total_equity, equity_attributable_to_parent,
        total_capital_source, revenue, net_profit, total_debt,
        operating_cash_flow, eps, pe, pb, debt_to_equity

    Ghi chú: revenue, net_profit, total_debt, operating_cash_flow, eps,
    pe, pb hiện LUÔN RỖNG ở nguồn thô (crawler chưa xác thực endpoint
    tương ứng) - hàm này KHÔNG tự bịa ra giá trị, chỉ giữ nguyên rỗng
    (None), để tránh dữ liệu sai lệch. Khi crawler được bổ sung, các
    cột này sẽ tự động có giá trị mà không cần sửa hàm này.
    """
    if df.empty:
        return df

    df = df.copy()

    keep_cols = [
        "symbol", "report_period", "report_type", "year_report", "length_report",
        "publish_date", "current_assets", "non_current_assets", "total_assets",
        "total_liabilities", "total_equity", "equity_attributable_to_parent",
        "total_capital_source", "revenue", "net_profit", "total_debt",
        "operating_cash_flow", "eps", "pe", "pb",
    ]
    df = df[[c for c in keep_cols if c in df.columns]]

    # Ép kiểu số cho các cột tiền tệ/chỉ số. errors="coerce": ô rỗng
    # hoặc lỗi định dạng -> NaN, KHÔNG làm crash chương trình.
    numeric_cols = [
        "current_assets", "non_current_assets", "total_assets",
        "total_liabilities", "total_equity", "equity_attributable_to_parent",
        "total_capital_source", "revenue", "net_profit", "total_debt",
        "operating_cash_flow", "eps", "pe", "pb",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Tự tính debt_to_equity = Nợ phải trả / Vốn chủ sở hữu.
    # Đây là 2 field ĐÃ XÁC THỰC (total_liabilities, total_equity) nên
    # tính được ngay, không cần chờ crawler bổ sung endpoint ratio.
    if "total_liabilities" in df.columns and "total_equity" in df.columns:
        df["debt_to_equity"] = df.apply(
            lambda r: (r["total_liabilities"] / r["total_equity"])
            if pd.notna(r["total_liabilities"]) and pd.notna(r["total_equity"]) and r["total_equity"] != 0
            else None,
            axis=1,
        )
    else:
        df["debt_to_equity"] = None

    # Loại các dòng thiếu report_period (không xác định được kỳ báo cáo)
    df = df.dropna(subset=["report_period"])

    # Loại trùng lặp (cùng mã + cùng kỳ báo cáo)
    df = df.drop_duplicates(subset=["symbol", "report_period"], keep="last")

    return df.reset_index(drop=True)
