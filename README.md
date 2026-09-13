# Dashboard sản lượng Cảng Nghệ Tĩnh

Ứng dụng React/Vite và FastAPI đọc SQL Server `SmartTOS` / `SmartTOS_BenThuy`. Mục tiêu là xem sản lượng toàn công ty hoặc từng xí nghiệp theo kỳ, so sánh kỳ trước, phân tích cơ cấu và xuất báo cáo CSV.

**Trạng thái bản rà soát 09/09/2026, cập nhật lần 2:** đã kiểm tra catalog hai database, toàn lịch sử TallyShift và nghiệp vụ từng tháng năm 2026; đối chiếu API báo cáo, sửa lọc nhóm qua cảng và lỗi cộng lẫn đơn vị. KPI tấn chỉ cộng phần khối lượng có cơ sở; đơn vị khác và dữ liệu thiếu hiển thị riêng. Chưa sửa dữ liệu gốc hoặc triển khai production. API chưa có xác thực người dùng; điều kiện vận hành và đối soát chứng từ còn lại được ghi trong kế hoạch.

## Tài liệu

- [Kế hoạch nâng cấp theo giai đoạn](UPGRADE_PLAN.vi.md)
- [Hướng dẫn frontend](frontend/README.md)

Hồ sơ đối soát chứa dữ liệu sản xuất được giữ cục bộ, không nằm trong repository công khai: `AUDIT_REPORT.vi.md`, `outputs/DATABASE_AUDIT.vi.md` và `outputs/data-accuracy-20260909/DATA_ACCURACY_REPORT.vi.md`. Các script `tests/verify_live_*.py` dùng hồ sơ hiện trường cũng được giữ cục bộ; bộ kiểm thử tái lập trong Git dùng fixture tổng hợp.

## Chạy trên Windows / PowerShell

Yêu cầu Python 3.12 trở lên, Node đáp ứng phiên bản Vite trong `frontend/package-lock.json`, Microsoft ODBC Driver 17 hoặc 18 đã cài và đường mạng truy cập SQL Server.

Từ thư mục gốc dự án:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

Nếu chưa có `.env`, sao chép `.env.example` thành `.env` và điền cấu hình ở máy cục bộ. Giữ nguyên `.env` đang có nếu đã được quản trị cấu hình. `DB_DRIVER` phải khớp driver đã cài. Ưu tiên chứng chỉ SQL được tin cậy; `DB_TRUST_SERVER_CERTIFICATE=true` chỉ là ngoại lệ cần chủ hệ thống cho phép, không phải giải pháp cho lỗi mạng.

Chạy API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Mở một terminal khác:

```powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Mở URL Vite in ra. Frontend gọi `/api` qua proxy phát triển. Nếu API ở cổng khác, cấu hình biến proxy được mô tả trong `frontend/README.md`. `VITE_API_URL` là đường dẫn kết thúc ở `/api` khi triển khai API khác origin; phải cấu hình CORS exact origin tương ứng. Không đưa bí mật SQL vào biến `VITE_*`.

### Railway API và Vercel frontend

Railway dùng biến môi trường của service, không tự đọc tệp `.env` trên máy phát triển. Trong **Variables** của service API, đặt `DB_SERVER`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD`, `DB_DRIVER` và các lựa chọn TLS theo cấu hình SQL đã được quản trị xác nhận. Lưu thông tin đăng nhập trong Railway Variables, không ghi vào Git hoặc biến frontend. Dockerfile cài ODBC Driver 17; `DB_DRIVER` phải khớp driver này.

Domain frontend chính được cấu hình bằng `FRONTEND_ORIGIN`, mặc định là `https://dashboard-sanluong.vercel.app`. `CORS_ORIGINS` là danh sách origin bổ sung ở dạng JSON, ví dụ `["http://localhost:5173","http://127.0.0.1:5173"]`. Mỗi origin gồm scheme và host/port, không có đường dẫn hoặc dấu `/` cuối; không dùng wildcard. Trên Vercel, đặt `VITE_API_URL=https://dashboardsanluong-production.up.railway.app/api` rồi build lại khi đổi biến này.

Sau khi áp dụng thay đổi Variables trên Railway, triển khai lại service. Kiểm tra OPTIONS với `Origin` là domain Vercel phải trả 200 cùng `Access-Control-Allow-Origin` đúng domain. GET báo cáo cũng phải có header này kể cả khi trả lỗi. `503 DATABASE_UNAVAILABLE` là lỗi cấu hình/kết nối SQL cần kiểm tra riêng, không phải lỗi CORS; không thay bằng dữ liệu mẫu hoặc tắt kiểm tra TLS để che lỗi.

#### Chuyển cấu hình TLS từ bản cũ

Bản cũ ghi cố định `Encrypt=yes;TrustServerCertificate=yes`. Bản hiện tại vẫn bật mã hóa nhưng mặc định xác minh chứng chỉ (`DB_ENCRYPT=true`, `DB_TRUST_SERVER_CERTIFICATE=false`). Đủ ba biến đăng nhập chưa bảo đảm kết nối được nếu máy chủ dùng chứng chỉ tự ký hoặc CA chưa được client tin cậy.

Cách triển khai lâu dài là dùng chứng chỉ hợp lệ trên SQL Server, cài CA cần thiết vào trust store của container và giữ `DB_TRUST_SERVER_CERTIFICATE=false`. Tên máy chủ kết nối phải phù hợp với chứng chỉ. Xem [hướng dẫn chứng chỉ của Microsoft ODBC trên Linux](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/connection-string-keywords-and-data-source-names-dsns?view=sql-server-ver17).

Nếu quản trị cho phép khôi phục tạm chính sách kết nối của bản cũ, cấu hình rõ trong Railway Variables rồi Deploy:

```dotenv
DB_ENCRYPT=true
DB_TRUST_SERVER_CERTIFICATE=true
```

Ngoại lệ này giữ mã hóa đường truyền nhưng bỏ xác minh danh tính máy chủ qua chứng chỉ. Không đặt `DB_ENCRYPT=false`; ứng dụng không tự hạ mức bảo vệ hoặc thử lại bằng chính sách TLS yếu hơn khi kết nối lỗi.

Deploy Logs chỉ ghi loại lỗi và SQLSTATE đã được lọc: `missing_configuration`, `tls_certificate`, `tls_handshake`, `authentication`, `timeout`, `network`, `connection`, `driver_configuration` hoặc `unknown`. `connection` biểu thị chưa đủ bằng chứng để phân loại chi tiết hơn. Kiểm tra dòng này để phân biệt lỗi chứng chỉ với lỗi mạng/tài khoản; không chia sẻ mật khẩu hoặc toàn bộ chuỗi kết nối trong quá trình xử lý.

#### Theo dõi lỗi tải dữ liệu gián đoạn

Dashboard và chi tiết chuyến tự thử lại GET **một lần** sau 500 ms khi `fetch` lỗi mạng hoặc HTTP 502/503/504. Cả hai lần dùng chung giới hạn 45 giây; đổi bộ lọc/trang hoặc đóng chi tiết sẽ hủy yêu cầu và lần thử lại. Lỗi cấu trúc/JSON và lỗi bộ lọc không được thử lại. Nếu vẫn thất bại, giao diện giữ thông báo lỗi và nút thử lại, không thay bằng dữ liệu cũ hay số 0.

Log kết nối có `elapsed_ms`. Lỗi đọc báo cáo và kiểm tra sức khỏe còn có `operation=query|health`, `phase=cursor|execute|fetch|row_limit`, `category`, `sqlstate` và `elapsed_ms`. Ngoài nhóm lỗi kết nối, các nhóm truy vấn gồm `permission`, `schema`, `deadlock`, `transaction_conflict`, `cancelled`, `query` và `row_limit`. Log không chứa câu SQL, tham số, dữ liệu trả về hay nội dung lỗi driver nguyên bản.

Khi lỗi tái diễn, đối chiếu thời điểm, mã HTTP và Deploy Logs: lỗi kết nối khác lỗi `DATABASE_QUERY_FAILED` sau khi đã kết nối. Kiểm tra `/api/health` và một báo cáo cùng kỳ riêng biệt. Không tự tăng timeout hoặc đổi quy tắc sản lượng khi chưa xác định bước lỗi. Nếu lỗi tập trung ở lần mở đầu tiên sau thời gian không dùng, kiểm tra service có bật **Serverless** hay không; Railway ghi nhận lần đánh thức có thể trả 502 trong [tài liệu Serverless](https://docs.railway.com/deployments/serverless). Đây là hướng kiểm tra có điều kiện, không phải khẳng định service đang bật chế độ này.

## API và hợp đồng báo cáo

```text
GET /api/dashboard?start_date=2026-08-01&end_date=2026-08-31&terminal=all
GET /api/voyages/cua_lo/{voyage_id}?start_date=2026-08-01&end_date=2026-08-31&page=1&page_size=25
GET /api/health
```

`terminal`: `all`, `cua_lo`, `ben_thuy`. Mặc định từ đầu tháng đến ngày hiện tại tại Việt Nam. Kỳ tối đa 366 ngày, gồm cả ngày kết thúc, không chọn ngày tương lai. Kỳ đối chiếu là khoảng liền trước có cùng số ngày.

Response gồm `overview`, `cargo`, `history` (tháng), `daily_history` (ngày), `terminals`, `directions`, `customers`, `native_units`, `voyages`, `efficiency`, `yard`, `meta`. Các panel sản lượng dùng cùng tập kết quả truy vấn. `meta.metric_coverage` chứa trạng thái và số dòng đủ/thiếu dữ liệu từng chỉ tiêu; `native_units` giữ riêng các đơn vị chưa cộng được vào tấn. Chỉ tiêu không có giá trị đã biết trả `null`, không có bản ghi trả 0. `meta` còn chứa bộ lọc, kỳ đối chiếu, thời điểm tổng hợp, ngày nghiệp vụ mới nhất theo nguồn và định nghĩa.

`voyages` là danh sách các chuyến được đếm trong KPI, lấy từ cùng tập tác nghiệp qua cảng và cùng bộ lọc. Khóa chuyến gồm xí nghiệp + ID chuyến; không gộp theo tên tàu. Mở một chuyến gọi `/api/voyages/{terminal}/{voyage_id}` để đọc thông tin tàu, ngày đến/rời thực tế, sản lượng theo hàng/ngày và các phiếu tác nghiệp phân trang. ID trong ví dụ chỉ minh họa; lấy ID thực từ danh sách. Không có chuyến hợp lệ trong kỳ trả 404. `page_size` tối đa 100; đây là chi tiết trong kỳ lọc, không tự mở rộng sang toàn vòng đời chuyến.

Mỗi dòng chi tiết tương ứng một `TallyShift`, định danh bằng `tallyShiftId`; đây là dòng tổng hợp tác nghiệp, không phải một lượt xe hay một phiếu cân. Ngày lấy từ `shiftDate`, ca lấy qua `shiftId` → `Shift.shiftCode`, số lượng từ `quantityTotalSum`, trọng lượng từ `weightNetSum`, giữ nguyên đơn vị nguồn. Phạm vi chỉ gồm dòng chưa xóa, chuyến tàu vật lý hợp lệ, hướng xếp/dỡ và phương án thuộc nhóm `SANLUONG-QUACANG`. Không cộng thêm bảng nguồn lực vào sản lượng vì một tác nghiệp có thể có nhiều nguồn lực.

Chi tiết nhận `operation_filter=all|with_values|missing_weight`, mặc định API là `all`. Giao diện chủ động chọn `with_values`: số lượng **hoặc** trọng lượng nguồn có giá trị khác 0, bao gồm điều chỉnh âm và đơn vị ngoài tấn. `missing_weight` lấy trọng lượng nguồn `NULL`; dòng có số lượng khác 0 nhưng thiếu trọng lượng có thể thuộc cả hai bộ lọc. Lọc bảng trước khi phân trang, sau khi tổng hợp toàn bộ dòng đủ điều kiện; bộ lọc không đổi `header`, `summary`, `cargo`, `daily` hoặc `native_units`. `operations.total` là số dòng sau lọc; `total_all` và `counts.all` là tổng dòng nguồn, bằng `summary.record_count`; `counts` chứa số dòng của từng bộ lọc. Khi không có dòng khớp bộ lọc, trang 1 vẫn trả 200 với `rows=[]`, `total_pages=0`; trang vượt phạm vi trả 422.

Không tự chuyển trọng lượng thiếu thành 0 hoặc ước tính từ số container. Cờ `isChecked` không được dùng làm điều kiện sản lượng: cần xác nhận quy trình duyệt tại nguồn trước khi thêm quy tắc này. API vẫn cung cấp trạng thái độ đầy đủ của số liệu trong metadata.

Theo yêu cầu giao diện hiện tại, các lưu ý rải dưới KPI và khung cảnh báo vàng được ẩn. Định nghĩa và metadata vẫn có trong mục nguồn dữ liệu thu gọn/API/CSV; cách tính và phân biệt NULL với 0 không thay đổi. Ngày rời chưa ghi nhận không được suy thành trạng thái tàu đang ở cảng.

Cơ cấu hàng hóa gộp các mã 20F/20E/20R/40F/40E/40R/45F/45E thành **Hàng container**, áp dụng chung cho dashboard, CSV và tổng hợp chi tiết chuyến; phiếu vẫn giữ mã gốc. Biểu đồ hàng xếp giảm dần theo tấn, cho mở rộng danh sách và giữ riêng giá trị 0/chưa có số tấn. Logo chính thức được import từ `frontend/src/assets` để Vite quản lý đường dẫn và đóng gói cùng bản build.

- HTTP 200 + `meta.status=empty`: truy vấn thành công, không có dòng phù hợp bộ lọc; không chứng minh ngày đã chốt đủ dữ liệu.
- HTTP 503: lỗi kết nối/truy vấn; không có dữ liệu mẫu thay thế.
- HTTP 422: bộ lọc không hợp lệ.
- `business_rules_verified=false`: công thức nghiệp vụ chưa được ký duyệt. Chỉ đổi trạng thái sau khi cập nhật code/quy tắc và có bằng chứng đối soát.
- Hiệu suất và tỷ lệ kho/bãi chưa đủ cơ sở tính nên trả trạng thái chưa khả dụng, không trả số giả.

Các endpoint cũ được giữ để đọc từng phần nhưng chúng cũng tổng hợp cùng bộ dữ liệu. Frontend nên gọi một `/api/dashboard` để tránh nhiều lần đọc nguồn và khác thời điểm giữa các lần gọi.

## Khảo sát nguồn

```powershell
.\.venv\Scripts\python.exe -m backend.inspect_database --mode schema
.\.venv\Scripts\python.exe -m backend.inspect_database --mode views
.\.venv\Scripts\python.exe -m backend.inspect_database --mode methods --database SmartTOS --start-date 2026-08-01 --end-date 2026-08-07
.\.venv\Scripts\python.exe -m backend.inspect_database --mode metrics --start-date 2026-08-01 --end-date 2026-08-07
```

Lệnh dùng truy vấn SELECT cố định, khoảng ngày có giới hạn, timeout và đóng kết nối; không cập nhật database. Tệp khảo sát có thể chứa dữ liệu doanh nghiệp: giữ trong phạm vi nội bộ. `ApplicationIntent=ReadOnly` hỗ trợ định tuyến kết nối; quyền tài khoản do SQL Server quyết định, không được xem cờ này là cơ chế chặn ghi. Xem [Microsoft về định tuyến chỉ đọc](https://learn.microsoft.com/en-us/sql/database-engine/availability-groups/windows/configure-read-only-routing-for-an-availability-group-sql-server?view=sql-server-ver17).

## Kiểm tra

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe -m compileall -q backend
cd frontend
npm test
npm run lint
npm run build
npm audit
```

Kiểm thử không ghi database nguồn. Browser smoke test dùng dữ liệu fixture tách biệt với chế độ chạy ứng dụng; xem hướng dẫn ở đầu `tests/browser_smoke.py` khi chạy kiểm thử giao diện.

## Container và triển khai

Dockerfile dùng build context `backend/`, chỉ copy module chạy API và chạy dưới user riêng. Cần cung cấp biến môi trường khi chạy; không đóng gói `.env` vào image. Cài ODBC theo [hướng dẫn chính thức Microsoft](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server?view=sql-server-ver17).

Docker engine không hoạt động trong phiên rà soát nên **chưa xác minh build image**. Chưa publish/deploy ứng dụng. Trước khi triển khai nội bộ chính thức cần xác thực/phân quyền, HTTPS, tài khoản SQL chỉ đọc, thay thông tin đăng nhập cũ từng xuất hiện trong source và hoàn tất đối soát nghiệp vụ.
