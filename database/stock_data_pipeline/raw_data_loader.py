# raw_data_loader.py
# ------------------------------------------------------------
# NHIỆM VỤ DUY NHẤT của file này: ĐỌC dữ liệu GIÁ thô mà bạn cùng nhóm
# đã crawl sẵn bằng `vietcap_market_data.py` (lệnh "history"), lưu dưới
# dạng file CSV, mỗi mã 1 file: data/market_data/FPT.csv,
# data/market_data/VCB.csv... và trả về DataFrame để đưa qua bước làm
# sạch (data_cleaner.py).
#
# Quy trình đầy đủ (2 bước, 2 người phụ trách khác nhau):
#   Bước 1 (bạn cùng nhóm): chạy vietcap_market_data.py để tạo/cập nhật
#           file CSV thô trong thư mục data/market_data/
#   Bước 2 (mình):          raw_data_loader (đọc) -> data_cleaner
#           (làm sạch) -> database (lưu vào SQLite/PostgreSQL)
# ------------------------------------------------------------

import logging
import os

import pandas as pd

from config import RAW_MARKET_DATA_DIR

logger = logging.getLogger(__name__)


def load_raw_price_csv(symbol: str) -> pd.DataFrame:
    """
    Đọc file CSV giá thô của 1 mã cổ phiếu do vietcap_market_data.py
    tạo ra (lệnh "history").

    File được tìm tại: {RAW_MARKET_DATA_DIR}/{symbol}.csv

    Trả về:
        DataFrame thô với các cột đúng theo schema Market Data:
        symbol, trade_date, open, high, low, close, volume, total_value,
        ref_price, ceiling, floor, foreign_buy_volume, foreign_sell_volume,
        put_through_volume, exchange, security_type, timeframe, currency,
        source, is_final, fetched_at
        (nếu file chưa tồn tại -> trả về DataFrame rỗng, KHÔNG báo lỗi,
        vì có thể crawler chưa chạy cho mã này)
    """
    file_path = os.path.join(RAW_MARKET_DATA_DIR, f"{symbol}.csv")

    if not os.path.exists(file_path):
        logger.warning(
            f"[{symbol}] Chưa có file dữ liệu giá thô tại {file_path} - "
            f"có thể vietcap_market_data.py chưa chạy cho mã này."
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


def load_all_raw_price(symbols: list) -> dict:
    """Đọc file CSV giá thô cho NHIỀU mã cùng lúc -> dict {mã: DataFrame}."""
    return {symbol: load_raw_price_csv(symbol) for symbol in symbols}


if __name__ == "__main__":
    # Chạy "python raw_data_loader.py" để tự test nhanh module này.
    logging.basicConfig(level=logging.INFO)
    test_symbol = "FPT"
    df = load_raw_price_csv(test_symbol)
    print(f"--- Dữ liệu giá thô đọc được cho {test_symbol} ---")
    print(df.head())
    print(f"Số dòng: {len(df)}")
