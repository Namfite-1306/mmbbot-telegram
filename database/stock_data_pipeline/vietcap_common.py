"""
vietcap_common.py
===================

Module dùng chung cho các script crawl dữ liệu Vietcap:
    - vietcap_market_data.py  (giá, khối lượng, OHLCV)
    - vietcap_fundamental_data.py  (báo cáo tài chính, chỉ số cơ bản)

Chứa:
    - BaseVietcapClient: HTTP client có rate-limit + retry, dùng chung
      cho mọi endpoint Vietcap (mỗi module con kế thừa và thêm các
      hàm gọi endpoint riêng của mình).
    - write_csv / merge_csv_dedup: tiện ích ghi CSV.
    - is_trading_hour: kiểm tra giờ giao dịch.
    - get_symbol_static_info / load_symbol_info_cache: lấy & cache
      thông tin tĩnh (sàn niêm yết, loại chứng khoán) theo mã, dùng
      chung để enrich cả market data lẫn fundamental data.
"""

from __future__ import annotations

import csv
import logging
import time

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests


# ============================================================================
# HTTP HEADERS DÙNG CHUNG
# ============================================================================

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://trading.vietcap.com.vn/",
    "Origin": "https://trading.vietcap.com.vn",
}

MAX_RETRIES = 2
RETRY_BACKOFF_SEC = 1.0
REQUEST_TIMEOUT_SEC = 8

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


log = get_logger("vietcap_common")


# ============================================================================
# BASE HTTP CLIENT (rate-limit + retry) - dùng chung mọi endpoint Vietcap
# ============================================================================

class BaseVietcapClient:
    """
    HTTP client nền tảng: rate-limit đơn giản + retry khi gặp lỗi
    mạng / 429 / 403. Các client cụ thể (market data, fundamental
    data) kế thừa class này và chỉ cần thêm hàm gọi endpoint riêng.
    """

    def __init__(self, min_interval_sec: float = 0.4, headers: Optional[dict] = None):
        self.session = requests.Session()
        self.session.headers.update(headers or DEFAULT_HEADERS)
        self.min_interval_sec = max(0.0, min_interval_sec)
        self._last_call = 0.0

    def _throttle(self):
        elapsed = time.time() - self._last_call
        if elapsed < self.min_interval_sec:
            time.sleep(self.min_interval_sec - elapsed)
        self._last_call = time.time()

    def _request(self, method: str, url: str, **kwargs):
        last_err = None

        for attempt in range(1, MAX_RETRIES + 1):
            self._throttle()
            try:
                resp = self.session.request(
                    method, url, timeout=REQUEST_TIMEOUT_SEC, **kwargs
                )

                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code in (429, 403):
                    log.warning(
                        "HTTP %s ở lần thử %s/%s. Chờ rồi thử lại...",
                        resp.status_code, attempt, MAX_RETRIES,
                    )
                    time.sleep(RETRY_BACKOFF_SEC * attempt)
                    continue

                resp.raise_for_status()

            except (requests.Timeout, requests.ConnectionError, requests.RequestException) as e:
                last_err = e
                log.warning("Lỗi request lần %s/%s: %s", attempt, MAX_RETRIES, e)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SEC * attempt)

        raise RuntimeError(
            f"Request thất bại sau {MAX_RETRIES} lần thử: {url}"
        ) from last_err


# ============================================================================
# CSV UTILS
# ============================================================================

def write_csv(rows: List[dict], path: Path, mode: str = "w"):
    if not rows:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists() and mode == "a"

    with open(path, mode, newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if not file_exists:
            writer.writeheader()
        writer.writerows(rows)


def merge_csv_dedup(new_rows: List[dict], path: Path, key_field: str = "trade_date"):
    """
    Gộp new_rows vào file CSV đã có, loại trùng theo key_field
    (mặc định 'trade_date' cho market data; fundamental data nên
    dùng key_field='report_period' hoặc tương đương).
    """
    if not new_rows:
        return 0

    existing_rows = []
    if path.exists():
        with open(path, "r", newline="", encoding="utf-8-sig") as f:
            existing_rows = list(csv.DictReader(f))

    merged = {row.get(key_field): row for row in existing_rows}

    added = 0
    for row in new_rows:
        k = row.get(key_field)
        if k not in merged:
            added += 1
        merged[k] = row

    all_rows = sorted(merged.values(), key=lambda r: r.get(key_field) or "")
    write_csv(all_rows, path, mode="w")
    return added


# ============================================================================
# TRADING HOURS
# ============================================================================

def is_trading_hour(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False

    t = now.time()
    morning = (
        datetime.strptime("09:00", "%H:%M").time()
        <= t <= datetime.strptime("11:30", "%H:%M").time()
    )
    afternoon = (
        datetime.strptime("13:00", "%H:%M").time()
        <= t <= datetime.strptime("15:00", "%H:%M").time()
    )
    return morning or afternoon


# ============================================================================
# SYMBOL STATIC INFO CACHE (exchange, security_type)
#
# Thông tin này gần như tĩnh (hiếm khi đổi sàn/loại CK), nên cache
# ra file và chỉ refresh định kỳ (vd: 1 lần/tuần) thay vì gọi lại
# mỗi lần cần enrich dữ liệu.
# ============================================================================

PRICEBOARD_ENDPOINT = (
    "https://trading.vietcap.com.vn/api/price/v1/w/priceboard/tickers/price/group"
)

DEFAULT_SYMBOL_INFO_CACHE = Path("data/symbol_info_cache.csv")


def fetch_symbol_static_info(
    groups: List[str] = ("HOSE", "HNX", "UPCOM"),
    min_interval_sec: float = 0.4,
) -> Dict[str, dict]:
    """
    Gọi priceboard cho từng sàn, trả về map:
        {"FPT": {"exchange": "HOSE", "security_type": "STOCK"}, ...}
    """
    client = BaseVietcapClient(min_interval_sec=min_interval_sec)
    info = {}

    for group in groups:
        try:
            data = client._request(
                "POST", PRICEBOARD_ENDPOINT, json={"group": group}
            )
            rows = data.get("data") if isinstance(data, dict) else data
            for item in rows or []:
                sym = item.get("s")
                if not sym:
                    continue
                info[sym] = {
                    "exchange": item.get("bo"),
                    "security_type": item.get("st"),
                }
        except Exception as e:
            log.error("Lỗi lấy symbol info cho sàn %s: %s", group, e)

    return info


def load_symbol_info_cache(
    cache_path: Path = DEFAULT_SYMBOL_INFO_CACHE,
    refresh: bool = False,
    groups: List[str] = ("HOSE", "HNX", "UPCOM"),
) -> Dict[str, dict]:
    """
    Đọc cache exchange/security_type theo mã từ file CSV; nếu chưa
    có cache hoặc refresh=True thì gọi lại priceboard và ghi cache mới.
    """
    if cache_path.exists() and not refresh:
        with open(cache_path, "r", newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        return {r["symbol"]: {"exchange": r["exchange"], "security_type": r["security_type"]} for r in rows}

    info = fetch_symbol_static_info(groups=groups)
    rows = [
        {"symbol": sym, "exchange": v["exchange"], "security_type": v["security_type"]}
        for sym, v in info.items()
    ]
    write_csv(rows, cache_path, mode="w")
    log.info("Đã cache thông tin sàn/loại CK cho %d mã vào %s", len(rows), cache_path)
    return info
