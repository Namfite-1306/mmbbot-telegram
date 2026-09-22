# Backtest mẫu HOSE V1

Chạy từ thư mục `Bot tele`:

```powershell
python -m app.backtest_v1 --config backtest_config.json --output backtest_results
python -m pytest tests/test_backtest_v1.py -q
```

Chương trình tạo hai danh mục **độc lập** (`TFM` và `TECHNICAL_ONLY`), mỗi danh mục có vốn ban đầu 100 triệu VND. Kết quả nằm trong `trades_*.csv`, `equity_*.csv` và `manifest.json`. Không dùng các cột target hoặc split của workbook mẫu.

## Nguồn và quy tắc

- Giá của 15 mã HOSE lấy từ sheet `Dữ liệu mô hình` trong `yahoo_vn_model_sample_2020_2026.xlsx`. Adjusted OHLC được tính bằng tỷ lệ `adj_close / close` của chính phiên; thiếu `adj_close` thì bỏ dòng, không thay bằng giá thô.
- Breadth đọc toàn bộ file cổ phiếu hợp lệ trong `crawl data/Project_python3/data/market_data`, không chỉ 15 mã mẫu. Vì lịch sử Vietcap chưa có adjusted OHLC, breadth hiện tính bằng close thô; số mã tham gia từng ngày phụ thuộc độ bao phủ dữ liệu.
- Benchmark được ánh xạ theo sàn. Bản mẫu chỉ có HOSE/VNINDEX; nếu thêm HNX hoặc UPCoM mà không có index tương ứng, chương trình sẽ báo thiếu dữ liệu. UPCoM không vào universe mua.
- T/F/M dùng trọng số 37,5% / 31,25% / 31,25%. E chỉ là điều kiện vào. `TECHNICAL_ONLY` chỉ nhận mã không có F hợp lệ và dùng ngưỡng T trong cấu hình; không so sánh hoặc cộng gộp hai danh mục như một kết quả chiến lược.
- Signal xác nhận tại Close T, mua Open phiên tiếp theo. Stop được kiểm tra bằng Close ≤ stop; Trend Exit cần hai Close liên tiếp < EMA20 và RSI14 phiên sau < 50. Bán Open phiên tiếp theo, nên gap có thể làm lỗ vượt mức dự kiến.
- 1% equity cho rủi ro dự kiến mỗi lệnh, tối đa 3 vị thế, tối đa 35% equity mỗi ngành, lô 100, lệnh không quá 5% ADV20. Ở NEUTRAL, quy mô rủi ro giảm 50% theo tài liệu chiến lược hiện tại.
- Nếu nhiều mã cùng phát tín hiệu hơn số chỗ trống, backtest xếp theo điểm giảm dần, rồi theo mã để phá hòa. Đây là quy tắc kỹ thuật tạm thời cần nhóm xác nhận.

## Chưa được chốt hoặc xác minh

`buy_fee_fraction`, `sell_fee_fraction`, `sell_tax_fraction`, `slippage_fraction` hiện là **0 để kiểm thử**, không phải giả định giao dịch thực tế. Cần điền số nhóm thống nhất trước khi đánh giá hiệu quả. M trong code hiện dùng 5/20 phiên; báo cáo chỉ số đề xuất 60 phiên. Ngưỡng Technical-only T ≥ 75, quy mô NEUTRAL 50% và thứ tự ưu tiên khi quá nhiều tín hiệu là giả định mẫu cần nhóm chiến lược xác nhận.

Workbook 15 mã được chọn trước có thể gây selection/survivorship bias. Danh sách Vietcap là hiện tại, chưa tái tạo đầy đủ universe lịch sử. Giá adjusted dùng cho fill/PnL nghiên cứu không thay thế việc hạch toán cổ tức/chia tách và giá khớp thật. Các tỷ số tài chính lịch sử có `announcement_date` nhưng chưa xác minh được phiên bản báo cáo/tỷ số có bị sửa về sau hay không. `manifest.json` ghi rõ các giới hạn này; không coi lợi nhuận mẫu là bằng chứng hiệu quả đầu tư.
