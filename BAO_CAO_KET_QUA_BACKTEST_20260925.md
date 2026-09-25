# Báo cáo kết quả backtest chiến lược V1

Ngày chạy: 25/09/2026  
Giai đoạn dữ liệu: 06/02/2020–11/09/2026  
Vốn ban đầu cho mỗi nhánh: 100.000.000 VNĐ  
Phạm vi giá: 15 mã HOSE trong workbook mẫu

## 1. Kết quả tổng hợp

| Chỉ tiêu | TFM | Technical-only |
|---|---:|---:|
| Giá trị cuối kỳ | 113.484.170 VNĐ | 98.388.665 VNĐ |
| Lợi nhuận toàn kỳ | **+13,48%** | **-1,61%** |
| CAGR ước tính | +1,94%/năm | -0,25%/năm |
| Drawdown lớn nhất | -8,45% | -2,39% |
| Số giao dịch đã đóng | 68 | 3 |
| Tỷ lệ thắng | 35,29% | 33,33% |
| Profit factor | 1,56 | 0,20 |
| Thời gian giữ trung bình | 34,25 ngày | 35,33 ngày |
| Tỷ lệ ngày có vị thế | 48,08% | 3,91% |
| Vị thế còn mở cuối kỳ | 1 | 0 |

## 2. Chi tiết nhánh TFM

- Tổng lãi của các lệnh thắng: 38.184.577 VNĐ.
- Tổng lỗ của các lệnh thua: -24.553.561 VNĐ.
- Lãi trung bình mỗi lệnh thắng: 1.591.024 VNĐ.
- Lỗ trung bình mỗi lệnh thua: -558.035 VNĐ.
- Lợi nhuận trung vị mỗi giao dịch: -281.527 VNĐ.
- Lệnh tốt nhất: FPT, từ 04/02/2021 đến 20/07/2021, lãi 5.124.263 VNĐ.
- Lệnh kém nhất: FPT, từ 24/01/2025 đến 04/02/2025, lỗ -1.538.458 VNĐ.
- Lý do thoát: 50 lệnh Trend Exit và 18 lệnh Stop Close.
- Drawdown sâu nhất xuất hiện ngày 29/01/2021, sau đỉnh gần nhất ngày 15/01/2021.

Nhóm đóng góp PnL tốt nhất là PLX, FPT, MBB, SSI và CTG. Nhóm đóng góp PnL kém nhất là HPG, MWG, VHM, VNM và VRE.

Tỷ lệ thắng thấp nhưng lãi trung bình của lệnh thắng lớn hơn khoảng 2,85 lần mức lỗ trung bình. Vì vậy nhánh TFM vẫn có lãi. Tuy nhiên CAGR 1,94%/năm còn thấp và chưa được so sánh đầy đủ với chiến lược mua-và-giữ hoặc lãi suất phi rủi ro.

## 3. Chi tiết nhánh Technical-only

- Chỉ phát sinh 3 giao dịch, nên mẫu quá nhỏ để kết luận.
- Một lệnh thắng và hai lệnh thua.
- Lệnh tốt nhất: HPG, lãi 412.077 VNĐ.
- Lệnh kém nhất: MSN, lỗ -1.066.554 VNĐ.
- Profit factor 0,20 cho thấy tổng lãi không bù được tổng lỗ trong mẫu hiện tại.

## 4. Cấu hình mô phỏng

- T/F/M: 37,5% / 31,25% / 31,25%.
- Ngưỡng điểm tổng TFM: 75.
- Ngưỡng Technical-only: 75.
- Rủi ro dự kiến mỗi lệnh: 1% equity.
- Tối đa 3 vị thế cùng lúc.
- Tối đa 35% equity cho một ngành.
- Khối lượng lệnh không quá 5% ADV20; lô 100 cổ phiếu.
- Phí mua: 0,15%; phí bán: 0,15%; thuế bán: 0,10%; trượt giá: 0,05% mỗi chiều.
- Tín hiệu xác nhận tại Close, giao dịch mô phỏng tại Open phiên tiếp theo.

## 5. Hạn chế quan trọng

1. Chỉ dùng 15 mã được chọn sẵn nên có selection bias và survivorship bias.
2. Đây không phải backtest toàn bộ VN100 hay toàn thị trường.
3. Breadth sử dụng giá Vietcap chưa điều chỉnh; số mã tham gia thay đổi theo độ phủ dữ liệu.
4. Giá dùng cho chỉ báo đã điều chỉnh nhưng mô phỏng khớp lệnh/PnL dùng giá thô; chưa có sổ tiền mặt riêng cho cổ tức và chia tách.
5. Chưa xác minh hoàn toàn tính point-in-time của mọi phiên bản báo cáo tài chính lịch sử.
6. Chi phí giao dịch là giả định nghiên cứu, không đảm bảo giống mức thực tế.
7. Một vị thế TFM còn mở ở cuối kỳ, nên equity cuối kỳ có phần lãi/lỗ chưa thực hiện.

## 6. Kết luận

Trong mẫu hiện tại, nhánh TFM tốt hơn rõ rệt so với nhánh Technical-only: lợi nhuận dương, profit factor lớn hơn 1 và drawdown dưới 10%. Tuy nhiên mức tăng trưởng năm còn thấp, tỷ lệ thắng thấp và phạm vi chỉ gồm 15 mã. Kết quả chỉ phù hợp để kiểm tra logic chiến lược; chưa đủ cơ sở kết luận chiến lược có hiệu quả trên VN100 hoặc dùng cho giao dịch thật.

Các file số liệu chi tiết nằm trong `backtest_results_20260925/` gồm `manifest.json`, `trades_tfm.csv`, `trades_technical_only.csv`, `equity_tfm.csv` và `equity_technical_only.csv`.
