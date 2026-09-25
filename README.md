# Telegram Bot tín hiệu cổ phiếu Việt Nam

Telegram Bot viết bằng Python với hai provider: `mock` để kiểm thử và `strategy` dùng SQLite. Giá OHLCV mới ưu tiên DNSE OpenAPI, dự phòng Vietcap rồi CafeF; báo cáo tài chính vẫn lấy từ Vietcap vì hai nguồn giá còn lại không cung cấp đủ các trường F của chiến lược.

> Tín hiệu chỉ phục vụ mục đích học tập, không phải khuyến nghị đầu tư.

## Cấu trúc repo

- `app/`: ứng dụng Telegram đang chạy; `app/main.py` là entry point. Các nhóm `bot/`, `providers/`, `services/`, `storage/` và `models/` giữ giao diện, nguồn tín hiệu, nghiệp vụ, SQLite và hợp đồng dữ liệu tách biệt.
- `tests/`: kiểm thử cho ứng dụng; chạy bằng `python -m pytest -q`.
- `crawl data/Project_python3/`: mã crawler Vietcap được ứng dụng gọi khi cần cập nhật.
- `Code chiến lược/Fin_Bot/`: các hàm kỹ thuật/cơ bản mà `app/strategy_engine.py` đang sử dụng. Chưa di chuyển các file này để không phá đường dẫn import và bài kiểm thử hiện có.
- `database/stock_data_pipeline/`: pipeline dữ liệu phụ trợ; không phải entry point của Telegram Bot.

Repo Git này chỉ chứa code, cấu hình mẫu và tài liệu. `.env`, SQLite, dữ liệu raw, `cleaned data/`, kết quả backtest và báo cáo Excel được giữ ngoài Git. Vì vậy bản clone mới chạy được chế độ `mock` ngay sau khi cài dependencies; chế độ `strategy` cần dữ liệu thực được cung cấp riêng rồi import. Workbook Yahoo mẫu không đi kèm repo nên các test yêu cầu nó sẽ được skip.

Với máy mới chạy `strategy`: đặt dữ liệu đã làm sạch vào `cleaned data/cleaned_v2/` (không commit), cấu hình `.env`, rồi chạy `python -m app.main --import-market-data`. Chỉ sau khi kiểm tra `/status` có phiên giá/index hợp lệ mới dùng `/scan` hoặc tài khoản mô phỏng. Dữ liệu raw cho lần crawl tiếp theo nằm ở `crawl data/Project_python3/data/`, cũng không commit.

## Chuẩn bị push GitHub

`Bot tele/` là một repo Git riêng; luôn chạy lệnh Git từ thư mục này, không từ repo `Finance1` cha. Trước khi commit, kiểm tra file sẽ được thêm:

```powershell
git status --short
git add --dry-run .
git check-ignore -v .env data/fintech_bot.db 'cleaned data/cleaned_v2/MODEL READY/model_ready_all.csv'
```

Sau khi tự xem lại danh sách, mới `git add .`, `git diff --cached --stat` và commit. Repo chưa có remote; cần URL GitHub riêng cho bot trước khi push. Không đưa token, SQLite hoặc dữ liệu người dùng lên Git. Nếu secret từng được commit, `.gitignore` không xóa nó khỏi lịch sử; cần thu hồi secret và xử lý lịch sử riêng.

## Yêu cầu môi trường

- Python 3.11 trở lên. Dự án đã được kiểm thử bằng Python 3.14.
- Telegram bot token từ BotFather.
- SQLite đi kèm Python.
- Quyền truy cập DNSE Market Data API (API Key và API Secret) khi dùng `SIGNAL_PROVIDER=strategy`.

## Tạo bot bằng BotFather

1. Mở Telegram và nhắn `/newbot` cho `@BotFather`.
2. Chọn tên và username cho bot.
3. Sao chép token BotFather cung cấp.
4. Không gửi token vào chat, log, source code hoặc commit Git.

## Cài đặt

Chạy các lệnh trong thư mục `Bot tele`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Nếu cần chạy test, cài `python -m pip install -r requirements-dev.txt` thay cho file requirements chạy bot.

Mở `.env` và điền:

```env
TELEGRAM_BOT_TOKEN=token_thật_từ_BotFather
SIGNAL_PROVIDER=mock
DATABASE_PATH=data/fintech_bot.db
TIMEZONE=Asia/Ho_Chi_Minh
LOG_LEVEL=INFO
MAX_TICKERS_PER_MESSAGE=10
DIGEST_TIME=17:30
# Chỉ điền khi bạn muốn bot báo lỗi/khôi phục EOD vào chat quản trị đã mở bot.
ADMIN_CHAT_ID=
DNSE_API_KEY=your_key
DNSE_API_SECRET=your_secret
```

`.env` đã được ignore. Không commit token thật.
Nếu chưa có chat quản trị, để `ADMIN_CHAT_ID` trống: lỗi EOD vẫn được ghi log
và thấy qua `/status`, nhưng bot không tự nhắn cảnh báo ra ngoài.

## Chạy

Kiểm tra cấu hình và database:

```powershell
python -m app.main --check-config
```

Xác thực token trực tiếp với Telegram mà không in token:

```powershell
python -m app.main --check-token
```

Kết quả đúng có dạng `Telegram token OK: @ten_bot`. Nếu Telegram trả `Unauthorized`, token đã sai hoặc bị thu hồi. Token phải có dạng `123456789:AA...`, không thêm tiền tố `bot`.

Nếu đã thay token trong `.env` nhưng bot vẫn dùng token cũ, xóa biến môi trường của phiên PowerShell trước khi chạy:

```powershell
Remove-Item Env:TELEGRAM_BOT_TOKEN -ErrorAction SilentlyContinue
```

Khởi động polling:

```powershell
python -m app.main
```

Trên Windows, có thể dùng lệnh sau để tự khởi động lại khi Telegram mất kết nối
lúc startup (exit code 4); các lỗi cấu hình khác sẽ dừng để kiểm tra:

```powershell
.\scripts\run_bot.ps1
```

Chỉ tạo database, không cần token:

```powershell
python -m app.main --init-db
```

Nếu thiếu `TELEGRAM_BOT_TOKEN`, chương trình dừng an toàn với thông báo cấu hình; token không được in ra console.

Log HTTP chi tiết của `httpx` bị hạ xuống mức `WARNING` và formatter che chuỗi có dạng token Telegram. Nếu token từng xuất hiện trong ảnh chụp, log hoặc nơi công khai, hãy thu hồi token đó ngay trong BotFather rồi tạo token mới.

## Command

| Command | Chức năng |
| --- | --- |
| `/start` | Giới thiệu bot |
| `/help` | Hướng dẫn đầy đủ |
| `/analyze FPT` hoặc `/soi FPT` | Phân tích từ nến cuối ngày đã lưu, gọi giá DNSE mới của riêng FPT tại thời điểm yêu cầu, rồi hiện nút biểu đồ |
| `/chart FPT` | Gửi ảnh biểu đồ trực tiếp nếu không dùng được nút |
| `/scan` | Không gọi API; từ SQLite chỉ quét VN100, bỏ mã thiếu điểm tổng/đứng giá và xếp top điểm cao, thấp |
| `/add FPT HPG` | Thêm nhiều mã, tối đa 10 mã/người |
| `/remove FPT HPG` | Xóa mã khỏi danh mục |
| `/portfolio` | Xem tổng quan danh mục: giá, biến động %, trạng thái xanh/vàng/đỏ và ghi chú |
| `/modelportfolio` | Vẽ sơ đồ phân bổ vốn mô phỏng theo `backtest_config.json` từ BUY HOSE của phiên cuối ngày đã lưu; không phải danh mục đang nắm giữ. Nếu thiếu ngành hoặc adjusted OHLC, giữ vốn ở tiền mặt và nêu lý do. |
| `/paper` | Tạo/xem tài khoản giao dịch ảo, tiền mặt, vị thế, tài sản ước tính và biểu đồ tròn |
| `/papercapital 120000000` | Đặt vốn góp mô phỏng thành 120 triệu VNĐ; chênh lệch cộng/trừ tiền mặt |
| `/paperposition FPT 200 85000` | Nhập/chỉnh tuyệt đối 200 cổ phiếu FPT với giá vốn 85.000 VNĐ/cp; `0 0` để xóa |
| `/paperbuy FPT 100` | Khớp mua ảo ngay 100 cổ phiếu HOSE theo giá khớp gần nhất từ API DNSE |
| `/papersell FPT 100` | Khớp bán ảo ngay từ số cổ phiếu đang nắm giữ theo giá API DNSE |
| `/paperorders`, `/paperhistory` | Xem lệnh chờ cũ (có nút hủy) hoặc 10 lệnh ảo gần nhất |
| `/papercancel 123`, `/papercancel FPT` | Hủy một lệnh theo ID hoặc toàn bộ lệnh chờ của một mã |
| `/alert on` | Bật cảnh báo |
| `/alert off` | Tắt cảnh báo nhưng giữ danh mục |
| `/digest on`, `/digest off` | Tự chọn nhận/dừng tổng kết danh mục sau `DIGEST_TIME` (mặc định 17:30); mặc định tắt |
| `/status` | Kiểm tra provider, database, phiên EOD cần có và trạng thái cập nhật |

Bot cũng nhận text như `FPT` hoặc `HPG, FPT, VNM`. Một mã đơn lẻ có
cùng nút biểu đồ với `/soi`; nhiều mã vẫn trả tóm tắt. Các lệnh cũ
`/danhmuc` và `/del` được giữ làm bí danh để không mất thao tác trước đây,
nhưng menu Telegram dùng tên tiếng Anh. `/soi` được giữ vì có trong hướng
dẫn người dùng; `/analyze` là tên tiếng Anh tương ứng. Nếu không thấy nút
biểu đồ, dùng `/chart <MÃ>`; ảnh cần ít nhất 20 phiên giá đã chốt hợp lệ.
Khi nhận lệnh, bot gửi `Vui lòng chờ phản hồi` rồi thay tin đó bằng kết quả; với
biểu đồ ảnh, tin chờ được xóa sau khi gửi.

### Tài khoản giao dịch ảo (MVP)

`/paper` chỉ hoạt động trong chat riêng với bot và `SIGNAL_PROVIDER=strategy`.
Nút sau `/scan` chỉ mở hướng dẫn đặt lệnh ảo hoặc danh mục, không tự mua/bán.
Mỗi chat riêng có tài khoản riêng; vốn ban đầu, phí, thuế, slippage, lô và số vị thế
tối đa lấy từ `backtest_config.json`. Lệnh `/paperbuy` và `/papersell` là lệnh
thủ công, **không phải tín hiệu hoặc lệnh gửi DNSE/VCI**. Bản đầu chỉ nhận cổ
phiếu HOSE, không bán khống, không cho đặt vượt tiền khả dụng hoặc số cổ phiếu
đang giữ. Mỗi lệnh mới gọi API market-data DNSE để lấy giá khớp gần nhất; nếu
không lấy được giá hợp lệ thì bot từ chối lệnh và không dùng Close cũ thay thế.

Lệnh mới khớp ảo ngay tại giá API, sau đó cập nhật tiền, vị thế và lịch sử trong
một transaction SQLite. Giá, nguồn và thời điểm API được lưu cùng lệnh để kiểm
tra lại. Tác vụ nền chỉ tiếp tục xử lý các lệnh `PENDING` do phiên bản cũ tạo ra.
Lệnh ảo vẫn tính phí mua/bán và thuế bán theo `backtest_config.json`; không cộng
slippage vào giá API hiện hành.
Lãi/lỗ trên màn hình dùng giá đóng cửa thô gần nhất. Bản MVP chưa tính T+2,
cổ tức, chia tách, giới hạn giá theo sàn hay độ sâu sổ lệnh; do đó không dùng
kết quả này như hiệu suất giao dịch thật. Các giới hạn ngành và risk/ATR của
chiến lược tự động **chưa áp dụng cho lệnh ảo thủ công**; chỉ dùng giới hạn tiền,
lô và tối đa ba vị thế. Tự đặt lệnh ảo theo tín hiệu chưa được bật.
Sổ lệnh vẫn giữ tín hiệu, điểm tổng (nếu đủ T/F/M) và ngày tín hiệu tại lúc
đặt lệnh; các ghi chú cũ được giữ trong SQLite cục bộ, không bị xóa khi bỏ
hai lệnh nhập ghi chú theo ID. Database không được commit lên Git.
`/papercapital` thay đổi phần vốn góp mô phỏng và tiền mặt cùng một khoản chênh lệch;
vẫn bảo vệ tiền đã dành cho lệnh chờ cũ nếu còn. `/paperposition` nhập hoặc sửa
vị thế có sẵn theo số cổ phiếu và giá vốn tuyệt đối, không tạo lệnh khớp giả;
giá vốn nhập tay được cộng/trừ vào tổng vốn góp, tiền mặt không đổi. Cả hai
được lưu lịch sử điều chỉnh trong SQLite và chỉ tác động đến tài khoản chat
của bạn. Không được sửa vị thế đang có lệnh chờ cùng mã. Biểu đồ `/paper`
dùng Close cuối cùng đã lưu, không phải giá trị tài sản thời gian thực.

Tổng kết `/digest on` chỉ gửi cho chat riêng tự bật và có danh mục, tối đa một
lần cho mỗi phiên. Sau `DIGEST_TIME` (mặc định 17:30) ngày giao dịch, bot dùng **phiên trước đó** nếu
VNINDEX cuối ngày của phiên ấy hợp lệ; tin ghi ngày thật, độ bao phủ và mã chưa
đủ dữ liệu. Bot thử lại định kỳ nếu dữ liệu đến muộn. `/digest off` dừng nhận;
mặc định mọi tài khoản đều tắt.

Mock provider có các trường hợp kiểm thử đặc biệt:

- `FPT`: BUY.
- `HPG`: HOLD.
- `VNM`: SELL.
- `SSI`: không đủ dữ liệu.
- `ERR`: giả lập provider error.

## SQLite

Database tự tạo khi bot khởi động. Ngoài `users`, `watchlist`, `user_settings` và `notifications`, provider chiến lược lưu `market_symbols`, `market_prices`, `market_fundamentals`, `market_indices` và dấu vết import `market_imports` trong cùng SQLite. Khóa chính cho phép import lại mà không nhân đôi dòng. CSV raw trong `crawl data/Project_python3/data` được giữ nguyên khi import; crawler gộp dữ liệu mới vào các CSV này. Bot chuẩn hóa ngày/cột, kiểm tra OHLC và cờ cuối ngày trước khi lưu DB. Với fundamental, CFO và lợi nhuận âm có thể hợp lệ; giá trị âm ở các khoản quy mô tài sản/nợ được gắn cờ riêng. `MODEL READY` không được dùng để phát tín hiệu vì chứa nhãn tương lai.
Các bảng nhật ký lệnh và tổng kết được tạo khi khởi động; cột nhật ký mới
được bổ sung vào database cũ mà không xóa lệnh hoặc ghi chú hiện có.

Import lại `cleaned_v2` có sẵn, không cần token Telegram và không gọi mạng:

```powershell
python -m app.main --import-market-data
```

## Mock alert

Chạy một lần theo danh mục của những người đã bật alert:

```powershell
python -m app.main --run-alert-once
```

Chỉ signal BUY/SELL được gửi. Bảng `notifications` ngăn gửi lại cùng `signal_id` cho cùng người dùng.

Scheduler không bật mặc định. Để bật trong môi trường phát triển:

```env
ENABLE_MOCK_ALERT_SCHEDULER=true
MOCK_ALERT_INTERVAL_SECONDS=300
```

Không nên bật scheduler mock liên tục trên production.

## Chạy test

```powershell
python -m pytest -q
```

Unit test không gọi Telegram thật và dùng database tạm.

## Chạy chiến lược với dữ liệu DNSE, Vietcap và CafeF dự phòng

Đặt `SIGNAL_PROVIDER=strategy` trong `.env`, rồi chạy `python -m app.main`.
Lần khởi động đầu tiên import `cleaned_v2` vào SQLite. Sau khởi động, tác vụ nền
kiểm tra và bổ sung dữ liệu cuối ngày của phiên giao dịch liền trước; nếu nguồn
lỗi, bot giữ dữ liệu đã lưu và thử lại sau. Không chạy crawl toàn thị trường
ngay trong lệnh `/scan`.
`/soi FPT` tính T/F/M từ nến và BCTC đã lưu, đồng thời gọi giá DNSE của FPT
ngay lúc nhận lệnh. Giá mới và tin liên quan có thời hạn chờ riêng; nguồn nào
chậm/lỗi thì dùng thông tin đã lưu và ghi rõ, không giữ phản hồi vô hạn.
`/scan` ưu tiên VNINDEX đã chốt của phiên giao dịch liền trước (ví dụ 25/09
dùng 24/09), chỉ đọc SQLite và chấm toàn bộ mã đến đúng phiên đó. Nếu thiếu
phiên kỳ vọng, lệnh này thử thêm đúng một phiên giao dịch trước nữa (23/09
trong ví dụ) và hiển thị cảnh báo cùng ngày dữ liệu thực tế; không dùng dữ liệu
cũ hơn hoặc trình bày tín hiệu 23/09 như tín hiệu 24/09. Các tác vụ quét tự động
vẫn đòi đúng phiên kỳ vọng. Lịch nghỉ giao dịch 2026 lấy từ thông báo HNX và HSX,
bao gồm cập nhật nghỉ 02/01/2026; cần bổ sung lịch năm 2027 trước khi dùng
trong năm đó. Ngày làm bù thứ Bảy vẫn không phải phiên giao dịch.
Nguồn: [HNX — lịch nghỉ 2026](https://old.hnx.vn/vi-vn/chi-tiet-lich-nghi-gd-60021971.html?_page=1),
[HSX — cập nhật Tết Dương lịch 2026](https://staticfile.hsx.vn/Uploads/UploadDocuments/2426350/20251225_Thong%20bao%20%20ve%20%20viec%20cap%20nhat%20lich%20nghi%20giao%20dich%20Tet%20Duong%20lich%202026%20toan%20thi%20truong.pdf).
Tác vụ EOD tự chạy khi khởi động và kiểm tra lại mỗi giờ; cập nhật giá và chỉ
số, không crawl lại toàn bộ báo cáo tài chính mỗi giờ. Khi nguồn lỗi, tác vụ
thử lại sau 15, 30, 60 phút rồi tối đa bốn giờ; `/status` hiển thị trạng thái.
Mỗi lần thử lưu phiên đích, độ bao phủ, mã còn thiếu và mã tải lỗi vào SQLite;
chỉ đánh dấu hoàn tất khi VNINDEX hợp lệ và độ bao phủ đạt ngưỡng kiểm tra.
Lần thử sau bỏ qua mã đã có nến cuối ngày hợp lệ của phiên đích, tránh tải trùng.
Kết quả `/scan` cùng phiên được giữ trong bộ nhớ tối đa 5 phút để các lệnh lặp
lại trả nhanh; `/soi` không crawl lại toàn bộ lịch sử khi người dùng hỏi giá mới.

`/soi <MÃ>` gọi REST DNSE `/price/:symbol/trades/latest` đúng lúc nhận lệnh
để hiển thị dữ liệu giá mới nhất kèm thời điểm khớp và khối lượng tích lũy;
không mở WebSocket chạy nền. Nếu DNSE lỗi, bot giữ giá phiên đã lưu và ghi rõ
nguồn dữ liệu hiển thị. Điểm/tín hiệu vẫn dùng nến đã
chốt, không dùng giá intraday để phát BUY/SELL. Báo cáo còn có BCTC quý đã
công bố trước ngày tín hiệu, điểm T/F/M, lý do và cảnh báo. Nút biểu đồ giá
vẽ tối đa 252 phiên **đã chốt** trong SQLite (nến OHLC, khối lượng, RSI14).
Nút dòng tiền vẽ CMF(20) từ OHLCV cùng nguồn; đây là chỉ báo áp lực mua/bán,
không phải dòng tiền giao dịch thực tế. Biểu đồ giá ghi rõ khi dùng giá thô.
Tin liên quan hiển thị tối đa hai tiêu đề và link lấy tại thời điểm gọi;
Telegram không hiện ảnh xem trước link. Nếu trang hoặc mạng lỗi, bot không tự tạo tin. Điểm
tổng chỉ hiện khi có đủ T/F/M; khi thiếu F hoặc M, điểm T được ghi riêng,
không trình bày như điểm tổng. Cần cài `matplotlib` từ `requirements.txt`
để dùng nút biểu đồ.

Nếu DNSE lỗi mạng hoặc thiếu nến, bot thử Vietcap rồi CafeF; nếu cả ba lỗi, bot dùng
dữ liệu đã lưu và báo lỗi làm mới. Thiếu/sai key DNSE là lỗi cấu hình, không
âm thầm đổi nguồn. Khi chạy crawler toàn thị trường riêng, pipeline kiểm tra VNINDEX
ở DNSE. DNSE được gọi tuần tự, giãn cách 0,1 giây; sau 5 mã liên tiếp lỗi,
pipeline chuyển sang Vietcap toàn thị trường (pipeline này có circuit breaker).
Nếu Vietcap cũng lỗi, CafeF lấy lịch sử giá từng mã và chỉ số, giãn cách 0,8 giây;
dừng sớm khi 5 mã đầu đều lỗi. CafeF dùng feed công khai của trang lịch sử giá,
không cần API key, nhưng endpoint có thể thay đổi. CafeF không làm mới báo cáo
tài chính; kết quả chỉ được đánh dấu là dự phòng giá và không đánh dấu hoàn tất
cập nhật fundamental. CafeF hiển thị giá cổ phiếu theo nghìn VNĐ; bot chỉ đổi
sang VND khi giá đóng cửa phiên trùng khớp lịch sử đã lưu. Nếu 7 ngày mới không
có phiên trùng, bot truy vấn riêng một phiên cũ đã lưu để xác nhận đơn vị;
không xác nhận được thì không ghi. Mã không có nến mới ở cả ba nguồn được
ghi nhận là thiếu dữ liệu, không tính là sự cố mạng diện rộng. Dữ liệu CafeF không
ghi đè phiên DNSE/Vietcap đã chốt hợp lệ và không tự suy ra adjusted OHLC từ
riêng giá điều chỉnh đóng cửa.
Giá DNSE chỉ được ghi đè lịch sử khi có phiên trùng để xác nhận đơn vị VND;
nếu không xác nhận được, mã đó dùng Vietcap. Các mã đã lấy thành công vẫn
được import vào DB; đợt cập nhật lỗi không được
đánh dấu là hoàn tất và có thể thử lại sau.
Không coi dữ liệu cũ là dữ liệu mới. `market_imports` giúp bỏ qua file CSV
chưa đổi. Thư mục `database/stock_data_pipeline` là pipeline thử nghiệm cũ,
không phải nguồn database mà bot đang đọc.

Điểm tổng = `0.375*T + 0.3125*F + 0.3125*M`. E (breakout, volume, trend,
RSI, khoảng cách EMA) là điều kiện vào lệnh riêng, không cộng điểm. T dùng
EMA20/50/200 và RSI; M = 50% sức mạnh tương đối 5 phiên + 50% sức mạnh
tương đối 20 phiên so với index của sàn.
F dùng tăng trưởng cùng quý năm trước, ROE, CFO, nợ vay/vốn chủ (không áp dụng
cho ngân hàng). P/E và P/B chỉ so với trung vị của các mã cùng ngành, cùng kỳ
báo cáo, đã công bố trước ngày tín hiệu. Nếu thiếu phân ngành, F được coi là
thiếu hợp lệ và mã đi nhánh Technical-only; không tự gán F=0. Nếu đã có ngành
nhưng thiếu peer hoặc chỉ số định giá không hợp lệ, phần valuation tương ứng
được loại khỏi mẫu số F. Workbook mẫu có `sector_group` cho 15 mã, còn live
Vietcap không dùng bản đồ ngành từ mẫu để suy rộng toàn thị trường.
`industry_map.csv` tại gốc bot có đúng hai cột `symbol,sector_group`;
`sector_group` là mã ngành ICB cấp 2 (`ICB2:<code>`) từ
[Vietcap IQ](https://iq.vietcap.com.vn/api/iq-insight-service/v2/company/search-bar?language=1),
đối chiếu với danh sách STOCK HSX/HNX trong SQLite. Lần truy xuất
22/09/2026: 704/704 mã có ngành, không có ngành trống hoặc mã ngoài danh sách.
Sau `pip install -r requirements.txt`, tạo lại bằng
`python scripts/build_score_inputs.py industry`. Đây là ảnh chụp
phân ngành ngày lấy, **không phải** lịch sử phân ngành point-in-time; khi
backtest ngày trước 22/09/2026 phải có snapshot ngành lịch sử riêng để loại
khả năng look-ahead. Có ngành không thay thế được báo cáo quý thiếu. F lọc
`announcement_date <= signal_date`, nhưng SQLite hiện không giữ từng bản sửa
đổi của cùng báo cáo; nếu nguồn từng điều chỉnh số liệu sau công bố, backtest
lịch sử vẫn cần snapshot báo cáo gốc để xác minh point-in-time.

Benchmark HNX lấy từ [báo cáo chỉ số từng phiên của Sở HNX](https://owa.hnx.vn/ftp/THONGKEGIAODICH/20260921/INDEX/20260921_ID_Thong_ke_thong_tin_chi_so.pdf),
trích dòng `HNX Index` và xác minh ngày, OHLC trước khi ghi
`cleaned data/cleaned_v2/index_data/cleaned_HNXINDEX.csv`. Lần lấy
22/09/2026 có 260 phiên thật trong khoảng 03/09/2025–21/09/2026 (các ngày
không tải được được liệt kê khi chạy script); đối chiếu lịch VNINDEX còn thiếu
23/06/2026 và không tự điền giá. Ngày 21/09 đóng cửa 274,38. CSV này ở thư mục dữ liệu
đã bị `.gitignore` loại khỏi Git. Tạo lại bằng
`python scripts/build_score_inputs.py hnx-index --start 2025-09-01 --end 2026-09-21`;
chỉ thêm `--allow-insecure-hnx` nếu máy báo lỗi chuỗi chứng chỉ TLS của host
HNX. Tuỳ chọn này bỏ xác thực chứng chỉ cho đúng host tải PDF, không bỏ kiểm
tra ngày/giá; cân nhắc cấu hình CA tin cậy trên máy triển khai. Khởi động bot
hoặc gọi `MarketStore.import_directory(CLEAN_DIR, symbols=[])` để nạp CSV vào
SQLite. M chỉ được tính khi có ít nhất 21 phiên trùng ngày của mã và chỉ số,
bao gồm đúng phiên phân tích. Chuỗi 260 phiên cũng đủ tính EMA200 của HNXINDEX;
với mã giao dịch thưa vẫn có thể thiếu 21 phiên giá trùng và M phải để trống.
Luồng DNSE/Vietcap/CafeF hiện không tự lấy PDF HNX này; khi cần phiên HNX
mới hơn, chạy lệnh tạo lại với `--end` là ngày quét rồi import CSV trước
`/scan`. Không được dùng chuỗi cũ làm M cho một phiên mới.
Đối soát cùng phiên bằng
`python scripts/audit_score_inputs.py --as-of 2026-09-21`; cột "before" trong
chương trình này là mô phỏng trạng thái trước khi có bảng ngành/HNXINDEX,
không phải bản ghi tín hiệu đã lưu. Chỉ tín hiệu có giá đúng ngày quét mới
được xem là hiện hành trong so sánh BUY/SELL.
Các ngưỡng con là quyết định thiết kế V1, cần kiểm định bằng backtest.
P/E, P/B trong CSV báo cáo chưa được kiểm chứng là snapshot đúng tại từng
ngày lịch sử; mặc định valuation tắt (`valuation_verified=False`) cho cả bot
và backtest. Hàm so trung vị ngành đã có nhưng chỉ bật sau khi xác minh nguồn
point-in-time, không tự bật chỉ vì đã có bảng ngành.

Breadth chỉ tính cổ phiếu HOSE có giao dịch hợp lệ ở hai phiên index liên tiếp;
thiếu breadth hoặc index cùng ngày là `UNKNOWN`, không mở BUY. BULL yêu cầu
Index > EMA50 > EMA200 và breadth ≥50%; BEAR khi EMA50 < EMA200; còn lại
NEUTRAL. Chỉ HOSE phát BUY V1 khi Score ≥75 hoặc nhánh thiếu F có T ≥75,
E PASS và regime BULL/NEUTRAL. HNX và UPCOM chỉ theo dõi. SELL hiện là cảnh
báo Trend Exit tại Close nếu đang nắm giữ, dự kiến thực hiện Open phiên sau;
bot chưa lưu vị thế hay entry price để xét stop riêng theo người dùng.
Dữ liệu giá Vietcap chưa có adjusted OHLC đầy đủ; bot gắn cảnh báo giá thô.
Backtest dùng adjusted OHLC cho chỉ báo, nhưng dùng Open/Close gốc cho khớp
lệnh, ADV20 và PnL; chưa hạch toán đầy đủ corporate actions nên kết quả vẫn
chỉ là mô phỏng nghiên cứu. Dữ liệu quá 5 ngày được đánh dấu cũ.
`mock` vẫn dùng để thử giao diện và cảnh báo.

## Giới hạn MVP

- Backtest V1 dùng giả định phí mua/bán 0,15% mỗi chiều, thuế bán 0,10% và
  trượt giá 0,05% mỗi chiều trong `backtest_config.json`; đây không phải phí
  bắt buộc của mọi công ty chứng khoán. Vốn 100 triệu, risk 1% BULL/0,5%
  NEUTRAL, tối đa 3 vị thế, 35% ngành, 5% ADV20, lô 100.
- Bot phát tín hiệu phân tích, không đặt lệnh thật và chưa có sổ vị thế; SELL
  phải được hiểu là điều kiện thoát cho người đang nắm giữ mã.
- Chưa có admin command hoặc phân quyền.
- Polling phù hợp cho MVP; production có thể chuyển sang webhook.
- Scheduler nội bộ phù hợp phát triển một process, không thay thế distributed scheduler khi scale nhiều instance.
