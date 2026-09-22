# Data Pipeline - Làm sạch & Lưu trữ dữ liệu chứng khoán

Phần việc: **làm sạch → lưu trữ dữ liệu**, nhận dữ liệu thô từ 2 crawler do bạn cùng nhóm viết:
- `vietcap_market_data.py` — giá/khối lượng (OHLCV) + dữ liệu dòng tiền (khối ngoại, giá trị giao dịch)
- `vietcap_fundamental_data.py` — báo cáo tài chính (bảng cân đối kế toán)

## 1. Cấu trúc thư mục

```
stock_data_pipeline/
├── vietcap_common.py            # (nhóm) HTTP client dùng chung cho 2 crawler
├── vietcap_market_data.py       # (nhóm) crawl giá -> data/market_data/*.csv
├── vietcap_fundamental_data.py  # (nhóm) crawl BCTC -> data/fundamental/*.csv
├── config.py                    # Cấu hình chung: danh sách mã CP, đường dẫn, DB engine
├── raw_data_loader.py           # B1: Đọc CSV giá thô
├── fundamental_data_loader.py   # B1b: Đọc CSV BCTC thô
├── data_cleaner.py              # B2: Làm sạch dữ liệu (cả giá + BCTC)
├── database.py                  # B3: Lưu vào SQLite/PostgreSQL + hàm đọc lại
├── run_daily_update.py          # File CHẠY CHÍNH - nối B1+B2+B3 cho từng mã CP
├── scheduler.py                  # Tự động hoá: gọi crawler rồi chạy pipeline mỗi ngày
├── requirements.txt
├── .gitignore
├── data/
│   ├── market_data/             # CSV giá thô (1 file/mã) - do vietcap_market_data.py tạo
│   ├── fundamental/             # CSV BCTC thô (1 file/mã) - do vietcap_fundamental_data.py tạo
│   └── stock_data.db            # Database SQLite (tự sinh khi chạy lần đầu, nếu dùng SQLite)
└── logs/
    └── pipeline.log              # Log lịch sử chạy (tự sinh khi chạy)
```

## 2. Luồng dữ liệu

```
[Bạn cùng nhóm]                              [Phần việc của mình]

vietcap_market_data.py history
        --> data/market_data/<MÃ>.csv (thô)
                    |
                    v
        raw_data_loader.py (đọc CSV)
                    |
                    v
        data_cleaner.clean_price_data() (làm sạch)
                    |
                    v
        database.save_price_data() (lưu vào bảng stock_price)


vietcap_fundamental_data.py
        --> data/fundamental/<MÃ>_fundamental.csv (thô)
                    |
                    v
        fundamental_data_loader.py (đọc CSV)
                    |
                    v
        data_cleaner.clean_fundamental_data() (làm sạch + tự tính debt_to_equity)
                    |
                    v
        database.save_fundamental_data() (lưu vào bảng fundamental_data)
```

`run_daily_update.py` nối toàn bộ luồng trên. `run_daily_update.py` **KHÔNG tự
chạy 2 crawler** — crawler phải chạy TRƯỚC để tạo/cập nhật CSV, rồi mới chạy
`run_daily_update.py` để đọc, làm sạch, lưu. `scheduler.py` tự động hoá đúng
thứ tự này mỗi ngày.

## 3. Cài đặt

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 4. Dùng PostgreSQL thay vì SQLite (tuỳ chọn)

Mặc định hệ thống lưu vào file SQLite (`data/stock_data.db`), không cần cấu
hình gì thêm — phù hợp khi code/test 1 mình.

Nếu muốn cả nhóm và bot Telegram cùng đọc/ghi chung 1 database (khi deploy
thật), chuyển sang PostgreSQL:

1. Tạo 1 database PostgreSQL miễn phí ở **Supabase** (supabase.com) hoặc
   **Neon** (neon.tech) — cả 2 đều có gói free vĩnh viễn, không cần thẻ tín
   dụng. Sau khi tạo, lấy "Connection string" (dạng
   `postgresql://user:password@host:5432/dbname`).
2. Tạo file `.env` trong thư mục project (đã có trong `.gitignore`, sẽ KHÔNG
   bị đẩy lên Git/nộp bài):
   ```
   DB_ENGINE=postgres
   DATABASE_URL=postgresql://user:password@host:5432/dbname
   ```
3. Chạy `pip install -r requirements.txt` (đã gồm `psycopg2-binary` và
   `python-dotenv`) rồi chạy `python run_daily_update.py` như bình thường —
   không cần sửa code gì thêm.

Xem lại phần đầu conversation/báo cáo về lý do chọn PostgreSQL (chia sẻ dữ
liệu giữa nhiều thành viên/máy, không phải vì tốc độ).

## 5. Chạy thử (từng bước, để hiểu rõ)

```bash
# Bước 1 (bạn cùng nhóm): crawl dữ liệu giá + BCTC cho vài mã
python vietcap_market_data.py history \
    --symbols FPT,VCB,VNM,HPG,MWG,ACB,MBB,TCB,VIC,SSI \
    --start 2023-09-01 --out data/market_data

python vietcap_fundamental_data.py \
    --symbols FPT,VCB,VNM,HPG,MWG,ACB,MBB,TCB,VIC,SSI \
    --out data/fundamental

# Bước 2 (phần việc của mình): làm sạch + lưu vào database
python run_daily_update.py
```

Sau đó bạn sẽ thấy `data/stock_data.db` được tạo ra (nếu dùng SQLite), và
`logs/pipeline.log` ghi lại mã nào thành công/lỗi.

## 6. Cập nhật hàng ngày (tự động)

Mỗi ngày sau giờ đóng cửa, cần chạy NỐI TIẾP:
```bash
python vietcap_market_data.py history --update --symbols FPT,VCB,... --out data/market_data
python vietcap_fundamental_data.py --symbols FPT,VCB,... --out data/fundamental
python run_daily_update.py
```

**Cách A - đơn giản, để test:** chạy `python scheduler.py` (đã tự gọi đúng 3
lệnh trên theo giờ hẹn `RUN_TIME`, mặc định 18:00). Terminal phải giữ chạy liên tục.

**Cách B - đáng tin cậy hơn khi deploy thật:** dùng cron/Task Scheduler - xem
ví dụ chi tiết trong comment đầu file `scheduler.py`.

## 7. Cấu trúc dữ liệu đã lưu trong database

**Bảng `stock_price`** (khoá chính: mã + ngày):
| Cột | Mô tả |
|---|---|
| symbol, date | Mã CP, ngày giao dịch |
| open, high, low, close, volume | Giá & khối lượng |
| total_value | Giá trị giao dịch (VND) - **chỉ có cho ngày hôm nay**, xem mục 8 |
| foreign_buy_volume, foreign_sell_volume | Khối lượng mua/bán khối ngoại - **chỉ có cho ngày hôm nay** |
| put_through_volume | Khối lượng giao dịch thoả thuận - **chỉ có cho ngày hôm nay** |
| is_final | `False` nếu là dữ liệu ngày hôm nay (có thể còn thay đổi), `True` nếu đã chốt phiên |

**Bảng `fundamental_data`** (khoá chính: mã + kỳ báo cáo):
| Cột | Mô tả |
|---|---|
| symbol, report_period, report_type, year_report, length_report | Mã CP, kỳ báo cáo (vd "2024" hoặc "2024Q3"), loại (yearly/quarterly) |
| current_assets, non_current_assets, total_assets | Tài sản (đã xác thực) |
| total_liabilities, total_equity, equity_attributable_to_parent, total_capital_source | Nợ & vốn chủ sở hữu (đã xác thực) |
| debt_to_equity | **Tự tính** = total_liabilities / total_equity |
| revenue, net_profit, total_debt, operating_cash_flow, eps, pe, pb | **Hiện luôn rỗng (None)** - xem mục 8 |

## 8. Giới hạn hiện tại (quan trọng, đọc kỹ)

`vietcap_fundamental_data.py` (do nhóm bạn viết) hiện **mới xác thực được
Bảng cân đối kế toán**. Các trường sau vẫn đang chờ nhóm xác nhận endpoint
(xem comment đầu file `vietcap_fundamental_data.py`, mục "VIỆC CẦN LÀM TIẾP"):
- `revenue`, `net_profit` (cần section INCOME_STATEMENT)
- `operating_cash_flow` (cần section CASH_FLOW)
- `eps`, `pe`, `pb` (cần endpoint/section riêng, có thể tên "RATIO"/"valuation")

**Điều này ảnh hưởng trực tiếp tới Chiến lược 2 (Phân tích cơ bản) của đề
bài** — bộ lọc "tăng trưởng doanh thu/lợi nhuận > 15%, ROE > 15%" cần
`revenue`/`net_profit`/`eps` mà hiện chưa có dữ liệu thật.

Pipeline làm sạch/lưu trữ (phần việc trong tài liệu này) đã **thiết kế sẵn
đầy đủ cột cho các trường này trong database** — khi nhóm bổ sung xong
endpoint còn thiếu trong `vietcap_fundamental_data.py`, chỉ cần chạy lại
`run_daily_update.py`, dữ liệu sẽ tự động đầy đủ, **không cần sửa gì ở
`data_cleaner.py` hay `database.py`**.

Trong lúc chờ: `debt_to_equity` đã tính được ngay (từ 2 trường đã xác thực),
có thể dùng tạm cho bộ lọc "nợ/vốn chủ sở hữu" trong đề bài.

## 9. Bàn giao cho phần Bot Telegram

Phần bot chỉ cần import các hàm có sẵn trong `database.py`:

```python
from database import get_latest_price, get_latest_fundamental

df_price = get_latest_price("FPT", n_days=60)
df_fund_yearly = get_latest_fundamental("FPT", report_type="yearly")
df_fund_quarterly = get_latest_fundamental("FPT", report_type="quarterly")
```

## 10. Thêm/bớt mã cổ phiếu theo dõi

Sửa `STOCK_LIST` trong `config.py`. Dùng ĐÚNG danh sách này khi chạy lệnh
crawl (`--symbols`) ở mục 5/6.

## 11. Xử lý sự cố thường gặp

| Lỗi | Nguyên nhân thường gặp | Cách xử lý |
|---|---|---|
| `[MÃ] Chưa có file dữ liệu giá/BCTC thô` | Crawler chưa chạy cho mã đó | Chạy `vietcap_market_data.py`/`vietcap_fundamental_data.py` cho mã này trước |
| `ModuleNotFoundError: requests`/`schedule` | Chưa cài thư viện | `pip install -r requirements.txt` |
| `DB_ENGINE=postgres nhưng chưa có DATABASE_URL` | Chưa set biến môi trường | Xem mục 4 - tạo file `.env` |
| Giá lỗi nhưng BCTC vẫn lưu OK (hoặc ngược lại) | 2 nguồn CSV độc lập, `run_daily_update.py` tách try/except riêng cho từng phần | Xem chi tiết lỗi trong `logs/pipeline.log` |
| Crawler bị chặn IP (403/429) | Vietcap giới hạn tần suất request | Tăng `--min-interval`, giảm `--workers` (xem crawler) |
