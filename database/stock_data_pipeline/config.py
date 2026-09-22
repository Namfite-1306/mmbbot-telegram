# config.py
# ------------------------------------------------------------
# File CẤU HÌNH CHUNG cho toàn bộ hệ thống.
# Mục đích: gom hết các thông số hay thay đổi (danh sách mã CP,
# đường dẫn file...) vào MỘT chỗ duy nhất, để sau này bạn chỉnh
# sửa mà không cần lục tìm trong nhiều file code khác nhau.
# ------------------------------------------------------------

import os

# Tự động nạp file .env (nếu có) để lấy biến môi trường như DATABASE_URL,
# tránh phải gõ lại export mỗi lần mở terminal mới. File .env đã được
# liệt vào .gitignore nên sẽ KHÔNG bị đẩy lên Git/nộp bài kèm mật khẩu.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # nếu chưa cài python-dotenv thì bỏ qua, vẫn dùng biến môi trường hệ thống bình thường

# Thư mục gốc của project (thư mục chứa file config.py này)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------
# 1. DANH SÁCH MÃ CỔ PHIẾU CẦN THEO DÕI
# ------------------------------------------------------------
# Muốn theo dõi thêm/bớt mã nào, chỉ cần sửa list này. Dùng list này
# khi gọi crawler (--symbols) VÀ khi chạy pipeline làm sạch/lưu trữ,
# để 2 bên luôn khớp nhau.
STOCK_LIST = [
    "FPT",
    "VCB",
    "VNM",
    "HPG",
    "MWG",
    "ACB",
    "MBB",
    "TCB",
    "VIC",
    "SSI",
]

# ------------------------------------------------------------
# 2. ĐƯỜNG DẪN FILE / THƯ MỤC
# ------------------------------------------------------------
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# Thư mục chứa dữ liệu GIÁ thô do vietcap_market_data.py tạo ra
# (mỗi mã 1 file: data/market_data/FPT.csv, data/market_data/VCB.csv...)
# PHẢI khớp tham số --out khi chạy lệnh "history" của vietcap_market_data.py.
RAW_MARKET_DATA_DIR = os.path.join(DATA_DIR, "market_data")

# Thư mục chứa dữ liệu BÁO CÁO TÀI CHÍNH thô do vietcap_fundamental_data.py
# tạo ra (mỗi mã 1 file: data/fundamental/FPT_fundamental.csv...)
# PHẢI khớp tham số --out khi chạy vietcap_fundamental_data.py.
RAW_FUNDAMENTAL_DIR = os.path.join(DATA_DIR, "fundamental")

# File database SQLite - nơi lưu trữ TOÀN BỘ dữ liệu đã làm sạch
# (chỉ dùng khi DB_ENGINE = "sqlite", xem mục 3 bên dưới)
DB_PATH = os.path.join(DATA_DIR, "stock_data.db")

# File log - ghi lại lịch sử chạy cập nhật dữ liệu mỗi ngày
LOG_FILE = os.path.join(LOG_DIR, "pipeline.log")

# ------------------------------------------------------------
# 3. CHỌN NƠI LƯU TRỮ: SQLITE (mặc định) HAY POSTGRESQL
# ------------------------------------------------------------
# "sqlite"   -> lưu vào 1 file .db tại DB_PATH. Đơn giản, không cần cài
#               đặt gì thêm, phù hợp khi code/test 1 mình trên máy cá nhân.
# "postgres" -> lưu vào database PostgreSQL (tự host hoặc dùng dịch vụ
#               miễn phí như Supabase/Neon). Phù hợp khi deploy thật:
#               nhiều thành viên và bot Telegram cùng đọc/ghi chung 1
#               database, không cần copy file .db qua lại.
#
# Đổi giá trị bên dưới để chuyển engine (hoặc set biến môi trường
# DB_ENGINE trước khi chạy, biến môi trường sẽ được ưu tiên hơn).
DB_ENGINE = os.environ.get("DB_ENGINE", "sqlite").strip().lower()

# Chuỗi kết nối PostgreSQL - CHỈ đọc từ biến môi trường, KHÔNG bao giờ
# ghi thẳng username/password vào file này, vì file này sẽ được đẩy lên
# Git/nộp bài. Cách set xem hướng dẫn trong README.md mục "Dùng PostgreSQL".
# Dạng chuỗi: postgresql://<user>:<password>@<host>:<port>/<dbname>
POSTGRES_DSN = os.environ.get("DATABASE_URL", "")

if DB_ENGINE == "postgres" and not POSTGRES_DSN:
    raise RuntimeError(
        "DB_ENGINE=postgres nhưng chưa có biến môi trường DATABASE_URL. "
        "Xem hướng dẫn set biến môi trường trong README.md mục 'Dùng PostgreSQL'."
    )
