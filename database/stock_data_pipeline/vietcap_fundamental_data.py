"""
vietcap_fundamental_data.py
==============================

Crawl Fundamental Data (báo cáo tài chính, chỉ số cơ bản) từ Vietcap.

TRẠNG THÁI:
    - BALANCE_SHEET: đã xác thực và hoạt động đầy đủ.
    - INCOME_STATEMENT / CASH_FLOW / RATIO (EPS, P/E, P/B): CHƯA XÁC
      THỰC — cần capture thêm request thật qua DevTools trước khi tin
      dùng (xem "VIỆC CẦN LÀM TIẾP" ở cuối file).

Endpoint đã xác nhận hoạt động:
    GET https://iq.vietcap.com.vn/api/iq-insight-service/v1/company/{symbol}/financial-statement?section=BALANCE_SHEET

Response: {"data": {"years": [...], "quarters": [...]}}, mỗi phần tử là
1 kỳ báo cáo với field mã hoá (bsa1, bsa2, ... KHÔNG có tên đi kèm).

Đã xác thực bằng quan hệ số học — kiểm tra cộng dồn khớp nhau ở MỌI kỳ,
MỌI năm trong dữ liệu mẫu (không phải đoán 1 lần rồi tin) — các field:

    bsa1   = Tài sản ngắn hạn
    bsa23  = Tài sản dài hạn
    bsa53  = TỔNG TÀI SẢN                       (= bsa1 + bsa23)
    bsa54  = Nợ phải trả (tổng)
    bsa78  = Vốn chủ sở hữu (tổng, gồm lợi ích cổ đông thiểu số)
    bsa79  = Vốn chủ sở hữu của cổ đông công ty mẹ
    bsa96  = TỔNG NGUỒN VỐN                     (= bsa54 + bsa78 = bsa53)

CHƯA xác thực — KHÔNG dùng cho tới khi có xác nhận:
    - "Nợ vay" cụ thể (vay + nợ thuê tài chính). Đây KHÁC bsa54: bsa54
      là toàn bộ nợ phải trả (gồm cả phải trả người bán, thuế, người
      lao động...), không chỉ riêng khoản vay có lãi.
    - Doanh thu, Lợi nhuận sau thuế       -> nằm ở section INCOME_STATEMENT
    - Dòng tiền kinh doanh                -> nằm ở section CASH_FLOW
    - EPS, P/E, P/B                       -> khả năng 1 endpoint "ratio"
      /"valuation" riêng, chưa xác nhận URL/section

VIỆC CẦN LÀM TIẾP (để hoàn thiện 4 mục còn thiếu):
    1. Trên trang chi tiết mã ở Vietcap, mở tab "Kết quả kinh doanh"
       (Income Statement) -> F12 -> Network -> XHR -> gửi Request URL
       + Response mẫu. Khả năng cao cùng pattern, chỉ đổi
       ?section=INCOME_STATEMENT (hoặc tên khác).
    2. Làm tương tự với tab "Lưu chuyển tiền tệ" (Cash Flow).
    3. Làm tương tự với tab "Chỉ số tài chính" / "Định giá" nếu có
       (cho EPS, P/E, P/B) — endpoint này nhiều khả năng khác hẳn
       pattern financial-statement.
    4. Đối chiếu 1 kỳ trên giao diện web (có nhãn tiếng Việt) với JSON
       để xác nhận thêm field "Nợ vay" cụ thể trong nhóm bsa2-bsa77.

    Sau khi có, cập nhật SECTION_* và field map tương ứng bên dưới rồi
    bổ sung logic build record giống get_balance_sheet().
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List

from vietcap_common import (
    BaseVietcapClient,
    merge_csv_dedup,
    get_logger,
)

log = get_logger("vietcap_fundamental_data")

VN_TZ = timezone(timedelta(hours=7))


# ============================================================================
# ENDPOINT
# ============================================================================

FINANCIAL_STATEMENT_ENDPOINT = (
    "https://iq.vietcap.com.vn/api/iq-insight-service/v1/"
    "company/{symbol}/financial-statement"
)

# Đã xác nhận hoạt động qua DevTools thật.
SECTION_BALANCE_SHEET = "BALANCE_SHEET"

# CHƯA XÁC NHẬN — chỉ là phỏng đoán theo pattern đặt tên của Vietcap,
# KHÔNG dùng cho tới khi test thật (xem "VIỆC CẦN LÀM TIẾP" ở trên).
SECTION_INCOME_STATEMENT_UNCONFIRMED = "INCOME_STATEMENT"
SECTION_CASH_FLOW_UNCONFIRMED = "CASH_FLOW"
SECTION_RATIO_UNCONFIRMED = "RATIO"

# Field map ĐÃ XÁC THỰC cho BALANCE_SHEET (kiểm tra cộng dồn khớp mọi kỳ,
# mọi năm trong dữ liệu mẫu FPT 2018-2026).
BALANCE_SHEET_MAP = {
    "bsa1": "current_assets",
    "bsa23": "non_current_assets",
    "bsa53": "total_assets",
    "bsa54": "total_liabilities",
    "bsa78": "total_equity",
    "bsa79": "equity_attributable_to_parent",
    "bsa96": "total_capital_source",
}

# Các field fundamental người dùng cuối cần nhưng CHƯA có dữ liệu xác
# thực (cần INCOME_STATEMENT / CASH_FLOW / RATIO). Để None tường minh
# thay vì bỏ qua, để pipeline downstream (bot Telegram) biết rõ field
# nào đang thiếu chứ không nhầm là "bằng 0".
PENDING_FIELDS = {
    "revenue": None,
    "net_profit": None,
    "total_debt": None,  # KHÁC total_liabilities - vay có lãi, chưa xác định field
    "operating_cash_flow": None,
    "eps": None,
    "pe": None,
    "pb": None,
}


class VietcapFundamentalClient(BaseVietcapClient):

    def _fetch_section(self, symbol: str, section: str) -> dict:
        url = FINANCIAL_STATEMENT_ENDPOINT.format(symbol=symbol.upper())
        return self._request("GET", url, params={"section": section})

    # =========================================================================
    # BALANCE SHEET (đã xác thực)
    # =========================================================================

    def get_balance_sheet(self, symbol: str) -> List[dict]:
        """
        Lấy bảng cân đối kế toán, trả về list record đã chuẩn hoá tên
        field, gồm cả kỳ năm (yearly) và quý (quarterly).
        """
        raw = self._fetch_section(symbol, SECTION_BALANCE_SHEET)
        data = raw.get("data") if isinstance(raw, dict) else raw
        if not data:
            return []

        records = []
        for report_type, periods in (
            ("yearly", data.get("years") or []),
            ("quarterly", data.get("quarters") or []),
        ):
            for item in periods:
                records.append(
                    self._build_balance_sheet_record(symbol, item, report_type)
                )
        return records

    @staticmethod
    def _build_balance_sheet_record(symbol: str, item: dict, report_type: str) -> dict:
        year = item.get("yearReport")
        length = item.get("lengthReport")  # 1-4 = quý; quan sát thấy 5 = cả năm

        report_period = f"{year}" if report_type == "yearly" else f"{year}Q{length}"

        record = {
            "symbol": symbol.upper(),
            "report_period": report_period,
            "report_type": report_type,
            "year_report": year,
            "length_report": length,
            "publish_date": (item.get("publicDate") or "")[:10] or None,
            "update_date": item.get("updateDate"),
        }

        for raw_key, field_name in BALANCE_SHEET_MAP.items():
            record[field_name] = item.get(raw_key)

        record.update(PENDING_FIELDS)

        record["currency"] = "VND"
        record["source"] = "vietcap"
        record["fetched_at"] = datetime.now(VN_TZ).isoformat(timespec="seconds")

        return record

    # =========================================================================
    # PLACEHOLDER - chờ endpoint/section được xác nhận
    # =========================================================================

    def get_income_statement(self, symbol: str) -> List[dict]:
        raise NotImplementedError(
            "Chưa xác nhận section cho Income Statement (Doanh thu, "
            "Lợi nhuận sau thuế). Xem 'VIỆC CẦN LÀM TIẾP' ở đầu file."
        )

    def get_cash_flow(self, symbol: str) -> List[dict]:
        raise NotImplementedError(
            "Chưa xác nhận section cho Cash Flow (Dòng tiền kinh doanh). "
            "Xem 'VIỆC CẦN LÀM TIẾP' ở đầu file."
        )

    def get_ratios(self, symbol: str) -> List[dict]:
        raise NotImplementedError(
            "Chưa xác nhận endpoint cho EPS/P-E/P-B. "
            "Xem 'VIỆC CẦN LÀM TIẾP' ở đầu file."
        )


# ============================================================================
# CRAWL + LƯU CSV
# ============================================================================

def crawl_fundamental_data(
    symbols: List[str],
    out_dir: Path,
    min_interval: float = 0.5,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    client = VietcapFundamentalClient(min_interval_sec=min_interval)

    for sym in symbols:
        try:
            records = client.get_balance_sheet(sym)
            if records:
                merge_csv_dedup(
                    records,
                    out_dir / f"{sym}_fundamental.csv",
                    key_field="report_period",
                )
                log.info(
                    "%s: %d kỳ báo cáo (balance sheet) đã lưu.",
                    sym, len(records),
                )
            else:
                log.warning("%s: không có dữ liệu balance sheet.", sym)
        except Exception as e:
            log.error("%s: lỗi khi lấy fundamental data - %s", sym, e)


# ============================================================================
# CLI
# ============================================================================

def main():
    p = argparse.ArgumentParser(description="Crawl Fundamental Data từ Vietcap")
    p.add_argument("--symbols", required=True, help="VD: FPT,VCB,VNM")
    p.add_argument("--out", default="data/fundamental")
    p.add_argument("--min-interval", type=float, default=0.5)
    args = p.parse_args()

    syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    crawl_fundamental_data(syms, Path(args.out), args.min_interval)


if __name__ == "__main__":
    main()
