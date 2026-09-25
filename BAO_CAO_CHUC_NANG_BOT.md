# Báo cáo ngắn: Mr. Mission Bossible Bot

**Cập nhật:** 25/09/2026  
**Mục đích:** giúp người mới hiểu nhanh bot làm gì, dữ liệu đến từ đâu và nên đọc file nào.

> Bot phục vụ học tập và mô phỏng, không phải khuyến nghị đầu tư và không gửi lệnh thật đến công ty chứng khoán.

## 1. Bot này làm gì?

Bot chạy trên Telegram và hỗ trợ bốn nhóm chức năng:

1. **Phân tích cổ phiếu:** `/soi FPT` hoặc `/analyze FPT` hiển thị giá, điểm kỹ thuật/cơ bản/sức mạnh tương đối, lý do và biểu đồ.
2. **Quét thị trường:** `/scan` đọc dữ liệu đã lưu trong SQLite để tìm các mã BUY, SELL hoặc cần theo dõi. Lệnh này không crawl toàn thị trường trong lúc người dùng chờ.
3. **Quản lý danh mục:** `/add`, `/remove`, `/portfolio` và `/modelportfolio` quản lý danh sách quan tâm hoặc xem cơ cấu vốn mô phỏng.
4. **Giao dịch ảo:** `/paper`, `/paperbuy`, `/papersell` và các nút mua/bán dùng tiền ảo; không kết nối hệ thống đặt lệnh thật.

Bot có các nút thao tác nhanh dưới phần trả lời. Nếu không lấy được dữ liệu của một mã, bot phải nêu rõ mã đó thay vì âm thầm bỏ qua.

## 2. Dữ liệu đi qua hệ thống như thế nào?

```text
DNSE / Vietcap / CafeF
          ↓
   Kiểm tra và chuẩn hóa
          ↓
 SQLite: data/fintech_bot.db
          ↓
 Tính tín hiệu và điểm T/F/M
          ↓
  Trả kết quả trên Telegram
```

- Tác vụ nền kiểm tra phiên giao dịch mới và tải dữ liệu toàn thị trường vào database.
- Dữ liệu giá ưu tiên **DNSE**, dự phòng **Vietcap**, sau đó **CafeF**.
- Báo cáo tài chính phục vụ điểm F lấy từ **Vietcap**.
- Tin doanh nghiệp lấy từ **CafeF**.
- `/scan` chỉ đọc database. `/soi` cũng dùng dữ liệu cuối ngày đã lưu để tính điểm, nhưng có thể gọi DNSE để hiển thị thêm giá khớp mới nhất.

## 3. Các lệnh chính

| Lệnh | Công dụng |
| --- | --- |
| `/start`, `/help` | Giới thiệu và hướng dẫn sử dụng. |
| `/soi MÃ`, `/analyze MÃ` | Phân tích một mã và hiện nút biểu đồ, mua/bán ảo. |
| `/scan` | Quét toàn thị trường từ dữ liệu đã lưu. |
| `/add MÃ`, `/remove MÃ` | Thêm hoặc xóa mã khỏi danh mục quan tâm. |
| `/portfolio` hoặc `/danhmuc` | Xem danh mục quan tâm. |
| `/modelportfolio` hoặc `/phanbo` | Xem cơ cấu vốn mô phỏng. |
| `/paper` | Xem tài khoản và vị thế ảo. |
| `/paperbuy MÃ SỐ_CP` hoặc `/buy`, `/mua` | Khớp mua ảo ngay theo giá API hiện hành. |
| `/papersell MÃ SỐ_CP` hoặc `/sell`, `/ban` | Khớp bán ảo ngay theo giá API hiện hành. |
| `/paperorders`, `/paperhistory` | Xem lệnh chờ cũ và lịch sử giao dịch ảo. |
| `/status` | Kiểm tra database và trạng thái cập nhật dữ liệu. |

## 4. Những file code quan trọng

| File | Vai trò dễ hiểu |
| --- | --- |
| `app/main.py` | Điểm khởi động bot, đăng ký menu và chạy các tác vụ nền cập nhật dữ liệu. |
| `app/bot/handlers.py` | Nhận lệnh/nút Telegram và quyết định phải gọi chức năng nào. Đây là file đầu tiên nên xem khi một lệnh không hoạt động. |
| `app/bot/formatter.py` | Tạo nội dung tin nhắn mà người dùng nhìn thấy. |
| `app/market_store.py` | Điều phối tải dữ liệu, kiểm tra chất lượng và lưu/đọc dữ liệu thị trường trong SQLite. |
| `app/strategy_engine.py` | Tính chỉ báo kỹ thuật, điểm T/F/M và điều kiện chiến lược. |
| `app/providers/strategy_signal_provider.py` | Biến dữ liệu từ strategy engine thành tín hiệu BUY/SELL/theo dõi cho bot. |
| `app/services/signal_service.py` | Lớp trung gian giữa lệnh Telegram và nguồn tín hiệu. |
| `app/paper_broker.py` | Quản lý tiền, vị thế và lệnh giao dịch ảo; không gọi API đặt lệnh thật. |
| `app/storage/database.py` | Khai báo cấu trúc các bảng SQLite. |
| `app/stock_chart.py` | Vẽ biểu đồ giá và dòng tiền CMF. |
| `app/config.py` | Đọc cấu hình từ `.env`. |

## 5. File nào gọi API để lấy dữ liệu?

### DNSE

- **`app/dnse_market_data.py`**: chứa client gọi DNSE OpenAPI để lấy OHLCV, chỉ số và giá khớp mới nhất.
- API sử dụng khóa `DNSE_API_KEY` và `DNSE_API_SECRET` trong `.env`.
- File này chỉ đọc dữ liệu thị trường, không chứa endpoint đặt lệnh.

### Vietcap

- **`crawl data/Project_python3/vietcap_common.py`**: HTTP client dùng chung, header, giới hạn tốc độ và retry.
- **`crawl data/Project_python3/vietcap_market_data.py`**: endpoint lấy danh sách mã, OHLC, intraday và priceboard.
- **`crawl data/Project_python3/vietcap_fundamental_data.py`**: endpoint lấy báo cáo tài chính và dữ liệu cơ bản.
- **`crawl data/Project_python3/daily_data_pipeline.py`**: chạy quy trình crawl Vietcap theo ngày.

### CafeF

- **`app/cafef_market_data.py`**: nguồn giá dự phòng khi DNSE và Vietcap không dùng được.
- **`app/cafef_news.py`**: lấy tin doanh nghiệp để hiển thị trong `/soi`.

`app/market_store.py` là nơi gọi và phối hợp các client trên, sau đó upsert dữ liệu vào `data/fintech_bot.db`.

## 6. Dữ liệu nằm ở file nào?

- **Database bot đang sử dụng:** `data/fintech_bot.db`. `/scan`, `/soi`, danh mục và giao dịch ảo chủ yếu đọc từ đây.
- **Giá từng cổ phiếu dạng CSV:** `crawl data/Project_python3/data/market_data/<MÃ>.csv`, ví dụ `FPT.csv`.
- **Báo cáo tài chính từng mã:** `crawl data/Project_python3/data/fundamental/<MÃ>_fundamental.csv`.
- **Dữ liệu chỉ số:** `crawl data/Project_python3/data/index_data/VNINDEX.csv`.
- **Danh sách mã và sàn:** `crawl data/Project_python3/data/symbol_info_cache.csv` và `symbols.csv`.
- **Dữ liệu bảng giá:** thư mục `priceboard` và `priceboard_daily`.
- **Báo cáo chất lượng dữ liệu:** thư mục `quality`.

CSV là dữ liệu thu thập/lưu vết; SQLite là nguồn bot truy cập nhanh khi phục vụ người dùng.

## 7. Cách tính điểm ngắn gọn

- **T - Kỹ thuật:** tối đa 100 điểm. Close trên EMA20 được 30 điểm; EMA20 trên EMA50 được 30; EMA50 trên EMA200 được 25; RSI14 trong khoảng 50-70 được 15.
- **F - Cơ bản:** chấm tăng trưởng doanh thu, lợi nhuận, ROE, dòng tiền kinh doanh và nợ/vốn chủ. Điểm được chuẩn hóa về thang 100; thiếu dữ liệu quan trọng thì F để trống.
- **M - Sức mạnh tương đối:** so hiệu suất cổ phiếu với chỉ số trong 5 và 20 phiên. Vượt chỉ số ở mỗi khoảng được 50 điểm, nên M thường là 0, 50 hoặc 100.
- **Điểm tổng:** `0,375 x T + 0,3125 x F + 0,3125 x M`. Chỉ tính khi đủ cả ba điểm.
- **Lưu ý:** điểm cao chưa tự động tạo lệnh. Bot còn kiểm tra xu hướng thị trường, breakout, khối lượng và các điều kiện an toàn khác.

## 8. Điều cần nhớ

- Tín hiệu phụ thuộc vào độ đầy đủ và ngày của dữ liệu; nên kiểm tra `/status` trước khi tin vào `/scan`.
- Lệnh giao dịch ảo chỉ mô phỏng, không mua bán thật.
- Token và API key phải để trong `.env`, không ghi trực tiếp vào code hoặc commit lên Git.
- Sau khi sửa code, cần khởi động lại bot bằng `python -m app.main` để phiên đang chạy nhận thay đổi.
