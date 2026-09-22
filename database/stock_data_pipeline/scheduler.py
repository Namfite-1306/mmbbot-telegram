# scheduler.py
# ------------------------------------------------------------
# NHIỆM VỤ: tự động gọi 2 crawler (vietcap_market_data.py,
# vietcap_fundamental_data.py) rồi tới run_daily_update.py (làm sạch +
# lưu trữ) vào 1 giờ cố định MỖI NGÀY, để không cần ngồi bấm chạy tay.
#
# Đây chỉ là 1 CÁCH trong nhiều cách để chạy tự động:
#
#   CÁCH 1 (dùng file này): chạy "python scheduler.py" và để nó chạy
#            liên tục. Đơn giản, không cần cấu hình hệ điều hành,
#            nhưng nếu tắt máy/mất điện thì lịch cũng dừng theo.
#
#   CÁCH 2 (khuyên dùng khi deploy thật): dùng cron (Linux/macOS) hoặc
#            Task Scheduler (Windows) để gọi 3 lệnh sau, THEO ĐÚNG
#            THỨ TỰ, mỗi ngày sau giờ đóng cửa (vd 18:00):
#              1. python vietcap_market_data.py history --update --symbols FPT,VCB,... --out data/market_data
#              2. python vietcap_fundamental_data.py --symbols FPT,VCB,... --out data/fundamental
#              3. python run_daily_update.py
#            Ví dụ dòng crontab (Linux/macOS):
#              0 18 * * * cd /duong/dan/toi/project && python vietcap_market_data.py history --update --symbols FPT,VCB,VNM --out data/market_data && python vietcap_fundamental_data.py --symbols FPT,VCB,VNM --out data/fundamental && python run_daily_update.py
#
#   Lưu ý: vietcap_market_data.py cũng có sẵn lệnh "auto" tự lên lịch
#   riêng cho phần crawl (--enable-history). Nếu nhóm dùng lệnh đó để
#   crawl, thì scheduler.py này chỉ cần chạy phần fundamental + gọi
#   run_daily_update.py theo giờ trễ hơn 1 chút, để chắc chắn dữ liệu
#   giá đã được crawl xong.
# ------------------------------------------------------------

import subprocess
import sys
import time
import logging

import schedule

from config import STOCK_LIST, RAW_MARKET_DATA_DIR, RAW_FUNDAMENTAL_DIR
from run_daily_update import run, setup_logging

# Giờ chạy mỗi ngày (giờ máy chủ). Nên chọn SAU giờ đóng cửa thị trường
# (HOSE đóng cửa 15:00) để đảm bảo có đủ dữ liệu của phiên hôm đó.
RUN_TIME = "18:00"


def _run_subprocess(cmd, label):
    logging.getLogger(__name__).info(f"Đang chạy {label}: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logging.getLogger(__name__).error(f"{label} chạy lỗi:\n{result.stderr}")
    else:
        logging.getLogger(__name__).info(f"{label} chạy xong.")


def run_crawlers():
    """Gọi 2 crawler để cập nhật dữ liệu thô mới nhất."""
    symbols_arg = ",".join(STOCK_LIST)

    _run_subprocess(
        [
            sys.executable, "vietcap_market_data.py", "history",
            "--update", "--symbols", symbols_arg, "--out", RAW_MARKET_DATA_DIR,
        ],
        "crawler giá (vietcap_market_data.py)",
    )
    _run_subprocess(
        [
            sys.executable, "vietcap_fundamental_data.py",
            "--symbols", symbols_arg, "--out", RAW_FUNDAMENTAL_DIR,
        ],
        "crawler BCTC (vietcap_fundamental_data.py)",
    )


def job():
    logging.getLogger(__name__).info(f"Đến giờ hẹn ({RUN_TIME}) - bắt đầu cập nhật dữ liệu")
    run_crawlers()          # Bước 1: crawl dữ liệu thô mới nhất
    run()                   # Bước 2: làm sạch + lưu trữ (phần việc của mình)


def main():
    setup_logging()
    schedule.every().day.at(RUN_TIME).do(job)

    logging.getLogger(__name__).info(
        f"Scheduler đã khởi động. Sẽ tự động chạy cập nhật dữ liệu lúc {RUN_TIME} mỗi ngày. "
        f"Nhấn Ctrl+C để dừng."
    )

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
