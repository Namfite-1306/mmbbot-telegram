# database.py
# ------------------------------------------------------------
# NHIỆM VỤ DUY NHẤT của file này: LƯU TRỮ dữ liệu đã làm sạch,
# và cho phép ĐỌC lại dữ liệu khi cần.
#
# Hỗ trợ HAI nơi lưu trữ, chọn qua config.DB_ENGINE:
#   - "sqlite"   : 1 file .db trên máy (mặc định, không cần cài server)
#   - "postgres" : database PostgreSQL trên server/cloud (Supabase, Neon,
#                  hoặc tự host) - phù hợp khi nhiều thành viên/bot cùng
#                  cần đọc chung 1 database.
#
# CÁC MODULE KHÁC (raw_data_loader, fundamental_data_loader, data_cleaner,
# run_daily_update, và sau này là bot Telegram) KHÔNG CẦN QUAN TÂM đang
# dùng engine nào - chúng chỉ gọi save_price_data(), get_latest_price()...
# như bình thường.
# ------------------------------------------------------------

import logging
import sqlite3

import pandas as pd

from config import DB_ENGINE, DB_PATH, POSTGRES_DSN

logger = logging.getLogger(__name__)

# psycopg2 (driver PostgreSQL) chỉ thực sự cần khi dùng engine "postgres".
if DB_ENGINE == "postgres":
    import psycopg2

# Dấu placeholder cho câu lệnh SQL có tham số: SQLite dùng "?",
# PostgreSQL (qua psycopg2) dùng "%s".
_PLACEHOLDER = "%s" if DB_ENGINE == "postgres" else "?"


def get_connection():
    """Mở kết nối tới database, tự động đúng engine theo config.DB_ENGINE."""
    if DB_ENGINE == "postgres":
        return psycopg2.connect(POSTGRES_DSN)
    return sqlite3.connect(DB_PATH)


def init_db():
    """
    Khởi tạo database: tạo 2 bảng nếu chưa tồn tại (stock_price,
    fundamental_data). An toàn để gọi nhiều lần (IF NOT EXISTS).
    """
    conn = get_connection()
    cursor = conn.cursor()

    if DB_ENGINE == "postgres":
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_price (
                symbol VARCHAR(20) NOT NULL,
                date   DATE NOT NULL,
                open   DOUBLE PRECISION,
                high   DOUBLE PRECISION,
                low    DOUBLE PRECISION,
                close  DOUBLE PRECISION,
                volume DOUBLE PRECISION,
                total_value DOUBLE PRECISION,
                foreign_buy_volume DOUBLE PRECISION,
                foreign_sell_volume DOUBLE PRECISION,
                put_through_volume DOUBLE PRECISION,
                is_final BOOLEAN,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, date)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS fundamental_data (
                symbol VARCHAR(20) NOT NULL,
                report_period VARCHAR(20) NOT NULL,
                report_type VARCHAR(20),
                year_report INTEGER,
                length_report INTEGER,
                publish_date DATE,
                current_assets DOUBLE PRECISION,
                non_current_assets DOUBLE PRECISION,
                total_assets DOUBLE PRECISION,
                total_liabilities DOUBLE PRECISION,
                total_equity DOUBLE PRECISION,
                equity_attributable_to_parent DOUBLE PRECISION,
                total_capital_source DOUBLE PRECISION,
                revenue DOUBLE PRECISION,
                net_profit DOUBLE PRECISION,
                total_debt DOUBLE PRECISION,
                operating_cash_flow DOUBLE PRECISION,
                eps DOUBLE PRECISION,
                pe DOUBLE PRECISION,
                pb DOUBLE PRECISION,
                debt_to_equity DOUBLE PRECISION,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, report_period)
            )
            """
        )
    else:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_price (
                symbol TEXT NOT NULL,
                date   TEXT NOT NULL,
                open   REAL,
                high   REAL,
                low    REAL,
                close  REAL,
                volume REAL,
                total_value REAL,
                foreign_buy_volume REAL,
                foreign_sell_volume REAL,
                put_through_volume REAL,
                is_final INTEGER,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, date)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS fundamental_data (
                symbol TEXT NOT NULL,
                report_period TEXT NOT NULL,
                report_type TEXT,
                year_report INTEGER,
                length_report INTEGER,
                publish_date TEXT,
                current_assets REAL,
                non_current_assets REAL,
                total_assets REAL,
                total_liabilities REAL,
                total_equity REAL,
                equity_attributable_to_parent REAL,
                total_capital_source REAL,
                revenue REAL,
                net_profit REAL,
                total_debt REAL,
                operating_cash_flow REAL,
                eps REAL,
                pe REAL,
                pb REAL,
                debt_to_equity REAL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, report_period)
            )
            """
        )

    conn.commit()
    conn.close()
    logger.info(f"Đã khởi tạo/kiểm tra database ({DB_ENGINE}) thành công")


# ------------------------------------------------------------
# LƯU DỮ LIỆU GIÁ
# ------------------------------------------------------------

_PRICE_COLS = [
    "symbol", "date", "open", "high", "low", "close", "volume",
    "total_value", "foreign_buy_volume", "foreign_sell_volume",
    "put_through_volume", "is_final",
]


def save_price_data(df: pd.DataFrame):
    """
    Lưu DataFrame giá cổ phiếu (đã làm sạch) vào bảng stock_price.

    Cơ chế "upsert": nếu (symbol, date) đã tồn tại -> GHI ĐÈ bằng dữ
    liệu mới nhất; nếu chưa có -> thêm mới. Đây là cơ chế giúp việc
    "chạy lại mỗi ngày" không bị trùng dữ liệu.
    """
    if df.empty:
        return

    # Đảm bảo đủ cột theo đúng thứ tự _PRICE_COLS, cột nào thiếu (vd
    # is_final nếu nguồn không có) thì điền None thay vì lỗi KeyError.
    for col in _PRICE_COLS:
        if col not in df.columns:
            df[col] = None

    conn = get_connection()
    cursor = conn.cursor()
    rows = df[_PRICE_COLS].where(pd.notnull(df[_PRICE_COLS]), None).values.tolist()

    if DB_ENGINE == "postgres":
        cursor.executemany(
            """
            INSERT INTO stock_price
                (symbol, date, open, high, low, close, volume, total_value,
                 foreign_buy_volume, foreign_sell_volume, put_through_volume,
                 is_final, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (symbol, date) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                total_value = EXCLUDED.total_value,
                foreign_buy_volume = EXCLUDED.foreign_buy_volume,
                foreign_sell_volume = EXCLUDED.foreign_sell_volume,
                put_through_volume = EXCLUDED.put_through_volume,
                is_final = EXCLUDED.is_final,
                updated_at = CURRENT_TIMESTAMP
            """,
            rows,
        )
    else:
        cursor.executemany(
            """
            INSERT OR REPLACE INTO stock_price
                (symbol, date, open, high, low, close, volume, total_value,
                 foreign_buy_volume, foreign_sell_volume, put_through_volume,
                 is_final, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            rows,
        )

    conn.commit()
    conn.close()
    logger.info(f"Đã lưu {len(rows)} dòng giá vào database ({DB_ENGINE})")


# ------------------------------------------------------------
# LƯU DỮ LIỆU BÁO CÁO TÀI CHÍNH
# ------------------------------------------------------------

_FUNDAMENTAL_COLS = [
    "symbol", "report_period", "report_type", "year_report", "length_report",
    "publish_date", "current_assets", "non_current_assets", "total_assets",
    "total_liabilities", "total_equity", "equity_attributable_to_parent",
    "total_capital_source", "revenue", "net_profit", "total_debt",
    "operating_cash_flow", "eps", "pe", "pb", "debt_to_equity",
]


def save_fundamental_data(df: pd.DataFrame):
    """Lưu DataFrame báo cáo tài chính (đã làm sạch) vào bảng fundamental_data."""
    if df.empty:
        return

    for col in _FUNDAMENTAL_COLS:
        if col not in df.columns:
            df[col] = None

    conn = get_connection()
    cursor = conn.cursor()
    rows = df[_FUNDAMENTAL_COLS].where(pd.notnull(df[_FUNDAMENTAL_COLS]), None).values.tolist()

    if DB_ENGINE == "postgres":
        cursor.executemany(
            """
            INSERT INTO fundamental_data
                (symbol, report_period, report_type, year_report, length_report,
                 publish_date, current_assets, non_current_assets, total_assets,
                 total_liabilities, total_equity, equity_attributable_to_parent,
                 total_capital_source, revenue, net_profit, total_debt,
                 operating_cash_flow, eps, pe, pb, debt_to_equity, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (symbol, report_period) DO UPDATE SET
                report_type = EXCLUDED.report_type,
                year_report = EXCLUDED.year_report,
                length_report = EXCLUDED.length_report,
                publish_date = EXCLUDED.publish_date,
                current_assets = EXCLUDED.current_assets,
                non_current_assets = EXCLUDED.non_current_assets,
                total_assets = EXCLUDED.total_assets,
                total_liabilities = EXCLUDED.total_liabilities,
                total_equity = EXCLUDED.total_equity,
                equity_attributable_to_parent = EXCLUDED.equity_attributable_to_parent,
                total_capital_source = EXCLUDED.total_capital_source,
                revenue = EXCLUDED.revenue,
                net_profit = EXCLUDED.net_profit,
                total_debt = EXCLUDED.total_debt,
                operating_cash_flow = EXCLUDED.operating_cash_flow,
                eps = EXCLUDED.eps,
                pe = EXCLUDED.pe,
                pb = EXCLUDED.pb,
                debt_to_equity = EXCLUDED.debt_to_equity,
                updated_at = CURRENT_TIMESTAMP
            """,
            rows,
        )
    else:
        cursor.executemany(
            """
            INSERT OR REPLACE INTO fundamental_data
                (symbol, report_period, report_type, year_report, length_report,
                 publish_date, current_assets, non_current_assets, total_assets,
                 total_liabilities, total_equity, equity_attributable_to_parent,
                 total_capital_source, revenue, net_profit, total_debt,
                 operating_cash_flow, eps, pe, pb, debt_to_equity, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    CURRENT_TIMESTAMP)
            """,
            rows,
        )

    conn.commit()
    conn.close()
    logger.info(f"Đã lưu {len(rows)} dòng báo cáo tài chính vào database ({DB_ENGINE})")


# ------------------------------------------------------------
# CÁC HÀM ĐỌC DỮ LIỆU - sau này bot Telegram sẽ gọi các hàm này.
# ------------------------------------------------------------

def get_latest_price(symbol: str, n_days: int = 60) -> pd.DataFrame:
    """Lấy N phiên giao dịch gần nhất của 1 mã, sắp xếp theo ngày tăng dần."""
    conn = get_connection()
    query = f"""
        SELECT * FROM stock_price
        WHERE symbol = {_PLACEHOLDER}
        ORDER BY date DESC
        LIMIT {_PLACEHOLDER}
    """
    df = pd.read_sql_query(query, conn, params=(symbol, n_days))
    conn.close()
    return df.sort_values("date").reset_index(drop=True)


def get_latest_fundamental(symbol: str, report_type: str = None) -> pd.DataFrame:
    """
    Lấy toàn bộ lịch sử báo cáo tài chính của 1 mã.
    report_type: "yearly", "quarterly", hoặc None (lấy cả 2 loại).
    """
    conn = get_connection()
    if report_type:
        query = (
            f"SELECT * FROM fundamental_data "
            f"WHERE symbol = {_PLACEHOLDER} AND report_type = {_PLACEHOLDER} "
            f"ORDER BY report_period DESC"
        )
        df = pd.read_sql_query(query, conn, params=(symbol, report_type))
    else:
        query = (
            f"SELECT * FROM fundamental_data WHERE symbol = {_PLACEHOLDER} "
            f"ORDER BY report_period DESC"
        )
        df = pd.read_sql_query(query, conn, params=(symbol,))
    conn.close()
    return df


# ------------------------------------------------------------
# XUẤT RA CSV/EXCEL - dùng khi bạn muốn "xem thử" dữ liệu bằng mắt,
# hoặc nộp kèm báo cáo. KHÔNG dùng file này làm nơi lưu trữ chính.
# ------------------------------------------------------------

def export_to_csv(table_name: str, output_path: str):
    """Xuất toàn bộ 1 bảng trong database ra file CSV."""
    conn = get_connection()
    df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
    conn.close()
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    logger.info(f"Đã xuất bảng '{table_name}' ra {output_path}")
