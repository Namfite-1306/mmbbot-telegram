# Vietcap data pipeline

Pipeline lấy dữ liệu trực tiếp từ Vietcap, không dùng `vnstock`.

## Phạm vi

- OHLCV cuối ngày: cổ phiếu HSX, HNX và UPCOM.
- Benchmark: VNINDEX.
- Priceboard cuối ngày: snapshot chuẩn hóa của cả ba sàn.
- Fundamental: cổ phiếu HSX và HNX; gồm doanh thu, NPAT, vốn chủ sở hữu,
  nợ, CFO, EPS, P/E, P/B, ROE và ROA.
- Mỗi bản ghi giữ `source`, `fetched_at` và `announcement_date`.

P/E và P/B từ `statistics-financial` của Vietcap. Fundamental được join
theo đúng kỳ báo cáo. Khi tạo tập huấn luyện theo ngày, chỉ dùng báo cáo có
`announcement_date <= trade_date` để tránh look-ahead bias.

## Khởi tạo toàn bộ dữ liệu

Chạy trong thư mục này:

```powershell
python daily_data_pipeline.py bootstrap --start 2020-01-01
```

## Cập nhật một lần cuối ngày

```powershell
python daily_data_pipeline.py update
```

## Chạy lịch cập nhật hằng ngày

```powershell
python daily_data_pipeline.py schedule --time 16:00
```

Scheduler chỉ chạy từ thứ Hai đến thứ Sáu. Có thể dùng Windows Task
Scheduler gọi lệnh `update` thay cho process chạy liên tục.

## Chạy từng phần

```powershell
python vietcap_market_data.py history --all --start 2020-01-01
python vietcap_market_data.py index --start 2020-01-01
python vietcap_fundamental_data.py --all --exchanges HSX,HNX
```

## Thư mục output

- `data/market_data`: một CSV lịch sử cho mỗi cổ phiếu.
- `data/index_data`: VNINDEX.
- `data/priceboard`: snapshot thô mới nhất theo sàn.
- `data/priceboard_daily`: snapshot chuẩn hóa theo ngày.
- `data/fundamental`: một CSV báo cáo tài chính cho mỗi mã HSX/HNX.
- `data/quality`: danh sách lỗi và báo cáo mỗi lần chạy.

## Kiểm soát chất lượng

Mỗi lần `bootstrap` hoặc `update`, pipeline tạo báo cáo JSON và hai bảng lỗi
trong `data/quality`. Dòng OHLC có giá đóng cửa nằm ngoài biên cao/thấp được
giữ nguyên theo dữ liệu nguồn và gắn `data_quality_flags`; không tự sửa giá.
Khi tạo đặc trưng nến, nên loại các dòng có cờ
`ohlc_bounds_source_anomaly`. Các phiên khối lượng bằng 0 được phân loại riêng
để tránh nhầm với lỗi giao dịch thực.

Fundamental thiếu ở kỳ mới nhất được ghi vào `fundamental_issues.csv`. Không
forward-fill P/E, P/B, EPS hay số liệu báo cáo tài chính qua kỳ khác. Khi ghép
vào dữ liệu theo ngày, chỉ sử dụng quan sát có
`announcement_date <= trade_date`.

Hiện endpoint lịch sử không trả dữ liệu cho mã UTT dù mã vẫn xuất hiện trên
priceboard. Pipeline báo `missing_file` cho mã này, không tạo lịch sử giả.

`adjusted_*` được để trống vì endpoint OHLC Vietcap đang dùng không trả giá
điều chỉnh lịch sử. Pipeline không sao chép giá chưa điều chỉnh vào các cột
này để tránh tạo dữ liệu giả.
