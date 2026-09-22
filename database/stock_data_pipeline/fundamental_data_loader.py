# fundamental_data_loader.py
# ------------------------------------------------------------
# NHIỆM VỤ DUY NHẤT của file này: ĐỌC dữ liệu BÁO CÁO TÀI CHÍNH thô mà
# bạn cùng nhóm đã crawl sẵn bằng `vietcap_fundamental_data.py`, lưu
# dưới dạng file CSV, mỗi mã 1 file:
# data/fundamental/FPT_fundamental.csv, data/fundamental/VCB_fundamental.csv...
#
# LƯU Ý QUAN TRỌNG (đọc kỹ, vì ảnh hưởng tới cả nhóm):
# Tại thời điểm này, vietcap_fundamental_data.py mới xác thực được
# phần BẢNG CÂN ĐỐI KẾ TOÁN (current_assets, total_assets,
# total_liabilities, total_equity...). Các cột revenue, net_profit,
# total_debt, operating_cash_flow, eps, pe, pb HIỆN LUÔN RỖNG (None)
# vì endpoint tương ứng (INCOME_STATEMENT/CASH_FLOW/RATIO) chưa được
# xác thực bên phía crawler (xem comment đầu file vietcap_fundamental_data.py).
#
# File này KHÔNG cần biết/quan tâm điều đó - nó chỉ đọc đúng những gì
# có trong CSV. Việc CỘT NÀO ĐANG RỖNG sẽ tự động được giữ nguyên rỗng
# xuyên suốt qua data_cleaner.py -> database.py, và khi nhóm bổ sung
# xong endpoint còn thiếu, dữ liệu sẽ tự động đầy đủ mà KHÔNG CẦN sửa
# lại file này hay database.py.
# ------------------------------------------------------------

import logging
import os

import pandas as pd

from config import RAW_FUNDAMENTAL_DIR

logger = logging.getLogger(__name__)


def load_raw_fundamental_csv(symbol: str) -> pd.DataFrame:
    """
    Đọc file CSV báo cáo tài chính thô của 1 mã, do
    vietcap_fundamental_data.py tạo ra.

    File được tìm tại: {RAW_FUNDAMENTAL_DIR}/{symbol}_fundamental.csv

    Trả về:
        DataFrame thô với các cột: symbol, report_period, report_type,
        year_report, length_report, publish_date, update_date,
        current_assets, non_current_assets, total_assets,
        total_liabilities, total_equity, equity_attributable_to_parent,
        total_capital_source, revenue, net_profit, total_debt,
        operating_cash_flow, eps, pe, pb, currency, source, fetched_at
        (nếu file chưa tồn tại -> trả về DataFrame rỗng, KHÔNG báo lỗi)
    """
    file_path = os.path.join(RAW_FUNDAMENTAL_DIR, f"{symbol}_fundamental.csv")

    if not os.path.exists(file_path):
        logger.warning(
            f"[{symbol}] Chưa có file BCTC thô tại {file_path} - "
            f"có thể vietcap_fundamental_data.py chưa chạy cho mã này."
        )
        return pd.DataFrame()

    try:
        df = pd.read_csv(file_path, encoding="utf-8-sig")
        if df.empty:
            logger.warning(f"[{symbol}] File {file_path} tồn tại nhưng rỗng")
        return df
    except Exception as e:
        logger.error(f"[{symbol}] Lỗi khi đọc file {file_path}: {e}")
        return pd.DataFrame()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_symbol = "FPT"
    df = load_raw_fundamental_csv(test_symbol)
    print(f"--- Dữ liệu BCTC thô đọc được cho {test_symbol} ---")
    print(df.head())
    print(f"Số dòng: {len(df)}")
