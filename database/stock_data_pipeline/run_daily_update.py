# run_daily_update.py
# ------------------------------------------------------------
# ĐÂY LÀ FILE "NHẠC TRƯỞNG" (main entry point) của phần LÀM SẠCH + LƯU TRỮ.
# Nó gọi lần lượt các module theo đúng thứ tự cho TỪNG mã cổ phiếu:
#
#   PHẦN GIÁ (đã được vietcap_market_data.py crawl sẵn thành file CSV):
#       raw_data_loader (đọc CSV) -> data_cleaner -> database (lưu)
#
#   PHẦN BÁO CÁO TÀI CHÍNH (đã được vietcap_fundamental_data.py crawl
#   sẵn thành file CSV):
#       fundamental_data_loader (đọc CSV) -> data_cleaner -> database (lưu)
#
# LƯU Ý QUAN TRỌNG: file này KHÔNG tự chạy 2 crawler trên. Trước khi
# chạy file này, phải chạy crawler để tạo/cập nhật dữ liệu thô trước:
#     python vietcap_market_data.py history --symbols FPT,VCB,... --start 2023-09-01 --out data/market_data
#     python vietcap_fundamental_data.py --symbols FPT,VCB,... --out data/fundamental
# (xem chi tiết ở README.md). scheduler.py đã tự động hoá việc gọi
# đúng thứ tự này mỗi ngày.
#
# Cách chạy thủ công (sau khi crawler đã chạy):
#     python run_daily_update.py
# ------------------------------------------------------------

import logging
import os

from config import STOCK_LIST, LOG_FILE, LOG_DIR
import raw_data_loader
import fundamental_data_loader
import data_cleaner
import database


def setup_logging():
    """
    Ghi log ra CẢ console LẪN file logs/pipeline.log - quan trọng để
    debug khi chạy tự động lúc không có người theo dõi trực tiếp.
    """
    os.makedirs(LOG_DIR, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def update_price_for_symbol(symbol: str) -> int:
    """Cập nhật dữ liệu GIÁ cho 1 mã: đọc CSV thô -> làm sạch -> lưu."""
    raw_df = raw_data_loader.load_raw_price_csv(symbol)
    clean_df = data_cleaner.clean_price_data(raw_df)
    database.save_price_data(clean_df)
    return len(clean_df)


def update_fundamental_for_symbol(symbol: str) -> int:
    """Cập nhật dữ liệu BÁO CÁO TÀI CHÍNH cho 1 mã: đọc CSV thô -> làm sạch -> lưu."""
    raw_df = fundamental_data_loader.load_raw_fundamental_csv(symbol)
    clean_df = data_cleaner.clean_fundamental_data(raw_df)
    database.save_fundamental_data(clean_df)
    return len(clean_df)


def run():
    logger = logging.getLogger(__name__)
    logger.info("========== BẮT ĐẦU LÀM SẠCH + LƯU TRỮ DỮ LIỆU ==========")

    database.init_db()

    price_ok, price_fail = 0, 0
    fund_ok, fund_fail = 0, 0

    for symbol in STOCK_LIST:
        logger.info(f"--- Đang xử lý mã: {symbol} ---")

        # Phần giá: lỗi ở 1 mã không được làm dừng cả chương trình
        try:
            n_rows = update_price_for_symbol(symbol)
            if n_rows > 0:
                price_ok += 1
            else:
                logger.warning(f"[{symbol}] Không có dữ liệu giá nào được lưu")
        except Exception as e:
            logger.error(f"[{symbol}] Lỗi khi xử lý dữ liệu giá: {e}")
            price_fail += 1

        # Phần BCTC: tách try/except riêng, vì là nguồn CSV độc lập -
        # lỗi ở đây không nên ảnh hưởng tới kết quả xử lý giá ở trên.
        try:
            n_rows = update_fundamental_for_symbol(symbol)
            if n_rows > 0:
                fund_ok += 1
            else:
                logger.warning(f"[{symbol}] Không có dữ liệu BCTC nào được lưu")
        except Exception as e:
            logger.error(f"[{symbol}] Lỗi khi xử lý BCTC: {e}")
            fund_fail += 1

    logger.info(
        f"========== HOÀN TẤT ==========\n"
        f"  Giá: {price_ok} mã thành công, {price_fail} mã lỗi\n"
        f"  BCTC: {fund_ok} mã thành công, {fund_fail} mã lỗi\n"
        f"(xem chi tiết từng mã ở log phía trên)"
    )


if __name__ == "__main__":
    setup_logging()
    run()
