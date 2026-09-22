from vnstock import Listing
import pandas as pd


def get_raw_listing():
    """
    Lấy toàn bộ dữ liệu listing từ API.
    Đây là dữ liệu RAW, chưa lọc.
    """

    listing = Listing(source="VCI")

    df = listing.symbols_by_exchange()

    return df


def normalize_listing(df):
    """
    Chuẩn hóa dữ liệu listing.
    """

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

    # Chuẩn hóa exchange
    df["exchange"] = (
        df["exchange"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # API trả HSX, hệ thống nội bộ dùng HOSE
    df["exchange"] = df["exchange"].replace({
        "HSX": "HOSE"
    })

    # Chuẩn hóa type
    df["type"] = (
        df["type"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    return df


def get_stock_universe():
    """
    Lấy Stock Universe chính thức cho Technical Engine.

    Chỉ lấy:
    - HOSE
    - HNX
    - UPCOM

    Và chỉ lấy:
    - type = STOCK
    """

    df = get_raw_listing()

    df = normalize_listing(df)

    # Chỉ lấy cổ phiếu
    df = df[df["type"] == "STOCK"]

    # Chỉ lấy 3 sàn
    df = df[
        df["exchange"].isin([
            "HOSE",
            "HNX",
            "UPCOM"
        ])
    ]

    # Không duplicate symbol + exchange
    df = df.drop_duplicates(
        subset=["symbol", "exchange"]
    )

    # Sắp xếp
    df = df.sort_values(
        ["exchange", "symbol"]
    )

    # Reset index
    df = df.reset_index(drop=True)

    return df


def get_symbols_by_exchange(exchange):
    """
    Lấy danh sách symbol theo sàn.
    """

    exchange = exchange.strip().upper()

    df = get_stock_universe()

    return df[
        df["exchange"] == exchange
    ].copy()
def get_ohlcv(symbol, start_date="2025-01-01", end_date="2026-09-20"):
    """
    Lấy dữ liệu OHLCV lịch sử của một mã.
    """

    from vnstock import Quote

    symbol = symbol.strip().upper()

    quote = Quote(
        symbol=symbol,
        source="VCI"
    )

    df = quote.history(
        start=start_date,
        end=end_date,
        interval="1D"
    )

    if df is None or df.empty:
        raise ValueError(f"Không lấy được dữ liệu OHLCV cho {symbol}")

    df = df.copy()

    # Chuẩn hóa tên cột
    df.columns = (
        df.columns
        .str.strip()
        .str.lower()
    )

    # Chuẩn hóa ngày
    df["time"] = pd.to_datetime(
        df["time"],
        errors="coerce"
    )

    # Đổi tên time → date
    df = df.rename(
        columns={"time": "date"}
    )

    # Chuẩn hóa symbol
    df["symbol"] = symbol

    # Sắp xếp
    df = df.sort_values("date")

    # Loại duplicate
    df = df.drop_duplicates(
        subset=["symbol", "date"]
    )

    df = df.reset_index(drop=True)

    return df

if __name__ == "__main__":

    print("===== STOCK UNIVERSE =====")

    df = get_stock_universe()

    print("Tổng số mã:", len(df))

    print("\n===== SỐ LƯỢNG THEO SÀN =====")

    print(
        df["exchange"]
        .value_counts()
        .sort_index()
    )

    print("\n===== 10 MÃ ĐẦU =====")

    print(
        df[
            [
                "symbol",
                "exchange",
                "organ_name"
            ]
        ].head(10)
    )
from vnstock import Listing
import pandas as pd
import os
import time


# =========================================================
# 1. STOCK UNIVERSE
# =========================================================

def get_raw_listing():
    listing = Listing(source="VCI")
    df = listing.symbols_by_exchange()
    return df


def normalize_listing(df):
    df = df.copy()

    df.columns = df.columns.str.strip().str.lower()

    df["symbol"] = (
        df["symbol"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["exchange"] = (
        df["exchange"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    df["exchange"] = df["exchange"].replace({
        "HSX": "HOSE"
    })

    df["type"] = (
        df["type"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    return df


def get_stock_universe():
    df = get_raw_listing()
    df = normalize_listing(df)

    df = df[df["type"] == "STOCK"]

    df = df[
        df["exchange"].isin([
            "HOSE",
            "HNX",
            "UPCOM"
        ])
    ]

    df = df.drop_duplicates(
        subset=["symbol", "exchange"]
    )

    df = df.sort_values(
        ["exchange", "symbol"]
    )

    df = df.reset_index(drop=True)

    return df


def get_symbols_by_exchange(exchange):

    exchange = exchange.strip().upper()

    df = get_stock_universe()

    return df[
        df["exchange"] == exchange
    ].copy()


# =========================================================
# 2. OHLCV
# =========================================================

def get_ohlcv(
    symbol,
    start_date="2025-01-01",
    end_date="2026-09-20"
):

    from vnstock import Quote

    symbol = symbol.strip().upper()

    quote = Quote(
        symbol=symbol,
        source="VCI"
    )

    df = quote.history(
        start=start_date,
        end=end_date,
        interval="1D"
    )

    if df is None or df.empty:
        raise ValueError(
            f"Không lấy được dữ liệu OHLCV cho {symbol}"
        )

    df = df.copy()

    df.columns = (
        df.columns
        .str.strip()
        .str.lower()
    )

    df["time"] = pd.to_datetime(
        df["time"],
        errors="coerce"
    )

    df = df.rename(
        columns={
            "time": "date"
        }
    )

    df["symbol"] = symbol

    df = df.sort_values("date")

    df = df.drop_duplicates(
        subset=["symbol", "date"]
    )

    df = df.reset_index(drop=True)

    return df


# =========================================================
# 3. VALIDATE OHLCV
# =========================================================

def validate_ohlcv(df):

    required_columns = [
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "symbol"
    ]

    missing_columns = [
        col for col in required_columns
        if col not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Thiếu cột: {missing_columns}"
        )

    # Kiểm tra null
    null_counts = df[
        required_columns
    ].isnull().sum()

    # Kiểm tra duplicate
    duplicate_count = df.duplicated(
        subset=["symbol", "date"]
    ).sum()

    # Kiểm tra giá trị âm
    negative_price = (
        df[
            ["open", "high", "low", "close"]
        ] < 0
    ).any().any()

    negative_volume = (
        df["volume"] < 0
    ).any()

    if duplicate_count > 0:
        return False, "Có duplicate"

    if negative_price:
        return False, "Có giá âm"

    if negative_volume:
        return False, "Có volume âm"

    if null_counts.sum() > 0:
        return False, "Có dữ liệu null"

    return True, "OK"


# =========================================================
# 4. STORAGE
# =========================================================

RAW_DIR = "data/raw/ohlcv"


def save_raw_ohlcv(df, symbol):

    os.makedirs(
        RAW_DIR,
        exist_ok=True
    )

    symbol = symbol.upper()

    file_path = os.path.join(
        RAW_DIR,
        f"{symbol}.csv"
    )

    df.to_csv(
        file_path,
        index=False
    )

    return file_path


def load_raw_ohlcv(symbol):

    symbol = symbol.upper()

    file_path = os.path.join(
        RAW_DIR,
        f"{symbol}.csv"
    )

    if not os.path.exists(file_path):
        return None

    df = pd.read_csv(
        file_path,
        parse_dates=["date"]
    )

    return df


# =========================================================
# 5. FETCH + VALIDATE + SAVE
# =========================================================

def collect_one_stock(
    symbol,
    start_date="2025-01-01",
    end_date="2026-09-20"
):

    print("=" * 50)
    print(f"Đang lấy dữ liệu: {symbol}")

    df = get_ohlcv(
        symbol,
        start_date,
        end_date
    )

    is_valid, message = validate_ohlcv(df)

    if not is_valid:
        raise ValueError(
            f"{symbol}: {message}"
        )

    file_path = save_raw_ohlcv(
        df,
        symbol
    )

    print(
        f"✓ {symbol}: "
        f"{len(df)} dòng"
    )

    print(
        f"✓ Đã lưu: {file_path}"
    )

    return df


# =========================================================
# 6. RETRY
# =========================================================

def collect_with_retry(
    symbol,
    start_date="2025-01-01",
    end_date="2026-09-20",
    max_retries=3,
    delay=5
):

    for attempt in range(1, max_retries + 1):

        try:

            print(
                f"\n{symbol} - lần thử "
                f"{attempt}/{max_retries}"
            )

            df = collect_one_stock(
                symbol,
                start_date,
                end_date
            )

            return {
                "symbol": symbol,
                "status": "SUCCESS",
                "rows": len(df),
                "error": None
            }

        except Exception as e:

            error_message = str(e)

            print(
                f"✗ Lỗi {symbol}: {error_message}"
            )

            # ==========================================
            # RATE LIMIT
            # ==========================================

            if (
                "rate limit" in error_message.lower()
                or "requests per minute" in error_message.lower()
                or "20/20" in error_message
                or "60/60" in error_message
            ):

                wait_time = 15

                print(
                    "\n⚠️ RATE LIMIT"
                )

                print(
                    f"→ Chờ {wait_time} giây..."
                )

                time.sleep(wait_time)

            # ==========================================
            # LỖI KHÁC
            # ==========================================

            elif attempt < max_retries:

                print(
                    f"→ Chờ {delay} giây rồi thử lại..."
                )

                time.sleep(delay)

            else:

                return {
                    "symbol": symbol,
                    "status": "FAILED",
                    "rows": 0,
                    "error": error_message
                }

    return {
        "symbol": symbol,
        "status": "FAILED",
        "rows": 0,
        "error": "Unknown error"
    }

# =========================================================
# 7. BULK COLLECTION
# =========================================================

LOG_FILE = "data/collection_log.csv"


def save_collection_log(result):
    os.makedirs("data", exist_ok=True)

    log_df = pd.DataFrame([result])

    if os.path.exists(LOG_FILE):
        log_df.to_csv(
            LOG_FILE,
            mode="a",
            header=False,
            index=False
        )
    else:
        log_df.to_csv(
            LOG_FILE,
            index=False
        )


def collect_all_stocks(
    symbols,
    start_date="2025-01-01",
    end_date="2026-09-20",
    delay=1
):
    """
    Thu thập OHLCV cho nhiều mã.

    - Nếu đã có file CSV hợp lệ -> SKIP
    - Nếu chưa có -> DOWNLOAD
    - Nếu lỗi -> FAILED
    - Không dừng toàn bộ chương trình khi một mã lỗi
    """

    results = []

    total = len(symbols)

    print("\n" + "=" * 60)
    print("BẮT ĐẦU BULK DATA COLLECTION")
    print(f"Tổng số mã: {total}")
    print("=" * 60)

    for index, symbol in enumerate(symbols, start=1):

        symbol = symbol.upper()

        print(
            f"\n[{index}/{total}] {symbol}"
        )

        # =================================================
        # KIỂM TRA FILE ĐÃ CÓ
        # =================================================

        existing_data = load_raw_ohlcv(symbol)

        if existing_data is not None:

            is_valid, message = validate_ohlcv(
                existing_data
            )

            if is_valid:

                result = {
                    "symbol": symbol,
                    "status": "SKIPPED",
                    "rows": len(existing_data),
                    "error": None,
                    "elapsed_seconds": 0
                }

                print(
                    f"→ SKIP: đã có {len(existing_data)} dòng"
                )

                save_collection_log(result)

                results.append(result)

                continue

        # =================================================
        # DOWNLOAD
        # =================================================

        start_time = time.time()

        result = collect_with_retry(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            max_retries=3,
            delay=2
        )

        elapsed = round(
            time.time() - start_time,
            2
        )

        result["elapsed_seconds"] = elapsed

        # =================================================
        # SAVE LOG
        # =================================================

        save_collection_log(result)

        results.append(result)

        # =================================================
        # HIỂN THỊ KẾT QUẢ
        # =================================================

        print(
            f"Status: {result['status']}"
        )

        if result["status"] == "FAILED":

            print(
                f"Error: {result['error']}"
            )

        # =================================================
        # DELAY
        # =================================================

        if index < total:

            time.sleep(delay)

    return pd.DataFrame(results)