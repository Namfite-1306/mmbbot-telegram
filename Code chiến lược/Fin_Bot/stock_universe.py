import pandas as pd


METADATA_FILE = "data/stock_metadata.csv"


def load_stock_metadata(file_path=METADATA_FILE):
    """
    Đọc thông tin cơ bản của các mã cổ phiếu.
    """

    df = pd.read_csv(file_path)

    df["symbol"] = df["symbol"].str.strip().str.upper()
    df["exchange"] = df["exchange"].str.strip().str.upper()
    df["industry"] = df["industry"].str.strip().str.upper()

    return df


def get_stock_universe(file_path=METADATA_FILE):
    """
    Trả về danh sách mã cổ phiếu trong universe.
    """

    df = load_stock_metadata(file_path)

    return df["symbol"].drop_duplicates().tolist()


def get_stock_info(symbol, file_path=METADATA_FILE):
    """
    Lấy thông tin của một mã cổ phiếu.
    """

    df = load_stock_metadata(file_path)

    symbol = symbol.strip().upper()

    result = df[df["symbol"] == symbol]

    if result.empty:
        return None

    return result.iloc[0].to_dict()