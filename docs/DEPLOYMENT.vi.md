**Triển khai dashboard nội bộ trên Railway và Vercel — cập nhật 17/09/2026**

**Responsive và xóa kế hoạch:** backend Railway deployment `277286c5-4212-4e0f-beaf-9881c8b9e0e4` đạt `SUCCESS`; hash module khớp gói phát hành. `DELETE /api/plans/{id}` nhận `{revision}` và đánh dấu xóa trong transaction, kiểm tra quyền/phạm vi, giữ lịch sử và bản chốt. `include_deleted=true` hỗ trợ tra cứu. Xóa bản đã duyệt mới nhất không kích hoạt lại phiên bản cũ; xóa mục tiêu công ty cũng không tự thay bằng tổng hai xí nghiệp. Migration chỉ thêm metadata xóa và điều chỉnh trigger để cho phép chuyển trạng thái này; nội dung kế hoạch đã duyệt/hủy vẫn bất biến.

Đã sao lưu và kiểm tra khôi phục `before-plan-delete` tại container/local. Sau triển khai, đối chiếu hash theo các cột gốc xác nhận tài khoản, phiên đăng nhập, toàn bộ kế hoạch, lịch sử và các bản chốt không đổi; không có kế hoạch thật nào bị xóa. Kiểm tra runtime chỉ thực hiện xóa trên một bản sao SQLite tạm, xác nhận lịch sử thêm đúng một sự kiện, phiên bản cũ không có hiệu lực và thao tác lặp trả 409. Health và OpenAPI production xác nhận API mới; không truy vấn hoặc sửa nguồn TOS trong bước xóa.

Frontend local đổi cùng một bảng kế hoạch thành thẻ khi vùng nội dung rộng tối đa 1.000 px, một cột ở tối đa 440 px; bổ sung hộp xác nhận xóa, nút giữ kế hoạch và lựa chọn xem kế hoạch đã xóa. **151 kiểm thử backend liên quan và 111 kiểm thử frontend đạt; lint/build đạt.** Browser fixture đã bổ sung kích thước 390/700/1024/1366 px và các thao tác xác nhận/hủy/lỗi/quyền, nhưng chưa chạy vì không có trình duyệt CUA kết nối. Tại thời điểm triển khai thủ công này, Vite đã trả module giao diện mới tại local; khi đó mã chưa push Git và frontend Vercel chưa triển khai lại. [Hướng dẫn xóa kế hoạch](KE_HOACH_SAN_LUONG.vi.md#xóa-kế-hoạch).

**Bản tối ưu truy vấn đã triển khai:** Railway deployment `1e75f49a-9a2c-42f0-8a41-2551d20ea92f` đạt `SUCCESS`; hash các module đang chạy khớp gói phát hành. Runtime UID `10001`, timeout SQL 20 giây, cache báo cáo 120 giây. Truy vấn tạo danh sách phương án `JobMethod` đủ điều kiện một lần cho mỗi nguồn, giữ nguyên KPI/phạm vi cầu đầu; không tạo index trên bảng nguồn hoặc sửa dữ liệu TOS.

Đo trực tiếp `ReportingService` production kỳ 01/01–17/09/2026, `all`/`nghe_tinh`: đọc mới **19,502 giây**, trong đó đọc nguồn **19,164 giây**; cache RAM **0,0057 giây** và cache ổ đĩa qua service mới **0,2262 giây**. Hai lần cache giữ nguyên `report_id`/`source_read_at`, tổng số lần đọc SQL nguồn là 1. Số dòng, KPI và kỳ đối chiếu khớp kết quả trước tối ưu; tấn vẫn `partial`. Mục tiêu kế hoạch năm đã duyệt được giữ nguyên và tỷ lệ đối chiếu vẫn là tạm tính. Các thời gian này đo trong tiến trình máy chủ, không bao gồm hành trình trình duyệt và không phải SLA. [Chi tiết hiệu năng và các lần đo trước](QUERY_PERFORMANCE.vi.md).

Bản sao `before-query-performance` trước nâng cấp đã kiểm tra khôi phục đạt trên container và local; hash nội dung tài khoản, kế hoạch, lịch sử không đổi sau triển khai. **464 kiểm thử backend đạt trong 199,94 giây; 110 kiểm thử frontend, lint và build đạt.** Payload báo cáo thật 459.057 byte được `validateDashboard` và `validateThroughputProgress` của frontend chấp nhận khi chạy bằng Node. Health Railway và qua proxy Vite trả HTTP 200. Tại thời điểm triển khai thủ công bản này, frontend local tại `http://127.0.0.1:5173` đã cập nhật; khi đó mã chưa push Git, Vercel chưa triển khai lại và chưa kiểm tra trực quan bằng browser. Kết quả đo lưu cục bộ, được Git ignore: `outputs/railway-annual-performance-deployed-20260917.json` và `outputs/railway-annual-performance-frontend-check-20260917.json`.

Bản trước cùng ngày `c147069e-1a83-42a3-a604-8e291cfa7a97` (SUCCESS) được triển khai trực tiếp từ mã cục bộ bằng CLI. Bản này sửa việc không tìm thấy kế hoạch đã duyệt khi dashboard đang xem một kỳ khác. API thêm `available_periods` chỉ chứa thông tin kế hoạch, không gắn sản lượng tháng vào mục tiêu năm. Nút **Xem tiến độ** và danh sách **Kế hoạch đối chiếu** mở đúng kỳ trước khi tính tỷ lệ. Đổi nhãn phạm vi thành **Cảng Nghệ Tĩnh**, **Cầu 5**; giữ nguyên quy tắc `initial-berth-v1`.

Đã xác nhận kế hoạch người dùng vừa nhập được duyệt và thay thế đúng phiên bản thử trước đó. Không sửa giá trị hoặc tạo thêm kế hoạch trong lượt này. Bản sao `/data/backups/control-20260917-before-plan-ux.sqlite3` và bản tải về `backend/.data/backups/production-20260917-before-plan-ux.sqlite3` đều kiểm tra khôi phục đạt. Sau triển khai, hash mã chạy khớp gói phát hành; tài khoản, toàn bộ kế hoạch và lịch sử khớp bản sao trước triển khai.

Kiểm thử bản sửa: **437 backend, 109 frontend đạt; lint/build đạt**. Kiểm tra live kỳ 01–17/09 mất 6,60 giây: danh sách lựa chọn có kế hoạch năm đã duyệt, nhưng số thực hiện tháng chỉ đối chiếu kế hoạch tháng/kỳ tùy chọn phù hợp. Đã đối chiếu nguồn phạm vi **Chưa xác định cầu** cùng kỳ; kết quả chi tiết được giữ trong hồ sơ nội bộ, không suy rộng cho mọi kỳ lịch sử. Frontend local thêm nhập số Việt Nam có xem trước, lỗi từng ô, nút tạo rõ ràng, sao chép thành phiên bản mới và xem tiến độ sau duyệt; duyệt kế hoạch không truy vấn lại TOS. Hai fixture trình duyệt đã cập nhật; phiên này không có trình duyệt được kết nối nên chưa chạy lại kiểm thử giao diện bằng browser. Tại thời điểm triển khai thủ công bản sửa này, những thay đổi trên chưa push Git và frontend Vercel chưa được triển khai lại. Xem [hướng dẫn kế hoạch](KE_HOACH_SAN_LUONG.vi.md).

Đối chiếu live báo cáo năm với mục tiêu đã duyệt xác nhận tỷ lệ đúng, hiển thị bằng cách cắt một chữ số thập phân; trạng thái nguồn `partial` nên tỷ lệ là tạm tính. Hàm kiểm tra frontend đã chấp nhận payload thật cho cả tháng và năm, đồng thời xác nhận không dùng sản lượng tháng cho mục tiêu năm. Lần đầu kết nối SQL thất bại, một lần kênh SSH kiểm tra bị gián đoạn; lần đọc thành công mất **56,68 giây**. Vì vượt hạn chờ giao diện cũ 45 giây, frontend nâng hạn chờ riêng kỳ 93–366 ngày lên 90 giây, giữ hủy truy vấn khi đổi bộ lọc; đây không phải sửa tốc độ truy vấn hay bảo đảm hết lỗi mạng.

Bản trước cùng ngày `885a994f-6acd-466d-8581-db72bcbadfc1` thêm mục tiêu sản lượng tháng/quý/năm/khoảng ngày, mục tiêu toàn công ty và API tiến độ theo phiên báo cáo. Giữ cơ chế nháp–duyệt–lịch sử; không thêm migration hay thay tài khoản. Frontend local có lọc Quý 1–4 theo năm và thanh tiến độ 5 màu.

Bản nền cùng ngày `95d19d19-2bf0-4ace-b29e-639719385c9a` có ba phạm vi sản lượng theo cầu cập đầu tiên và tối ưu đọc lịch sử cầu một lần trong cùng câu lệnh với dữ liệu tác nghiệp; phân loại trước khi tổng hợp/lưu phiên. Gói Docker đã bổ sung `berth_scope.py`.

Trước bản mục tiêu quý đã sao lưu `/data/backups/control-20260917-before-quarter-targets.sqlite3`, tải về `backend/.data/backups/production-20260917-before-quarter-targets.sqlite3` và kiểm tra khôi phục cả hai bản đạt. Kiểm thử: 432 backend, 100 frontend; lint/build đạt. Bộ browser mục tiêu mới kiểm nhập/duyệt bốn loại kỳ, ngưỡng màu, trạng thái tạm tính và phạm vi, với API giả lập hoàn toàn; không ghi kế hoạch thử vào dữ liệu công ty.

Sau triển khai, hash module khớp gói phát hành, API tiến độ hiện diện và tài khoản giữ nguyên. Đọc thực tế kỳ 01/07–17/09/2026 trên Railway mất 8,68 giây; KPI đã được đối chiếu, trạng thái số liệu `partial`. API tiến độ trả cùng report ID/phạm vi và xử lý đúng tình huống chưa có mục tiêu đã duyệt. Đây là kiểm tra trực tiếp code đang chạy; kiểm thử đăng nhập và nhập/duyệt kế hoạch dùng dữ liệu giả lập, không tạo mục tiêu trong kho production.

Sau đó, theo yêu cầu rõ ràng của người dùng, đã tạo và duyệt **kế hoạch thử trên Railway** qua dịch vụ `ControlStore` cho các loại kỳ được hỗ trợ; không ghi vào nguồn TOS. Các kế hoạch thử dùng mã tham chiếu bắt đầu bằng `TEST`, ghi rõ không phải chỉ tiêu chính thức và giữ lịch sử tạo–duyệt. Trước khi tạo đã kiểm tra để tránh thay thế mục tiêu thật; script có kiểm tra trùng và tái sử dụng đúng bản TEST khi chạy lại. Danh tính, giá trị và nội dung cụ thể được lưu trong hồ sơ nội bộ.

Đối chiếu snapshot nguồn thật và hàm tiến độ phía frontend sau khi tạo xác nhận các loại kỳ tính tỷ lệ đúng; kết quả vẫn tạm tính do trạng thái nguồn `partial`. Kết quả kiểm tra lưu trong `outputs/railway-demo-progress-frontend-check-20260917.json`; dữ liệu kế hoạch và lịch sử được lưu đầy đủ. Giao diện local bổ sung căn cứ và nhãn **Kế hoạch thử** cho các mã TEST. Phiên kiểm tra này không có trình duyệt được kết nối, nên chưa xác minh trực quan với phiên đăng nhập thật; đã kiểm tra dịch vụ đang triển khai, dữ liệu lưu và tính tương thích payload với giao diện. Không đổi tài khoản hoặc tạo kế hoạch chốt.

Frontend local đang dùng `/api` qua Vite proxy HTTPS tới Railway theo [hướng dẫn local qua Railway](LOCAL_RAILWAY.vi.md), vẫn ở `http://127.0.0.1:5173`. Backend Python local không cần chạy. Chế độ này dùng tài khoản và dữ liệu quản trị production, không sao chép tài khoản local hay mở thêm CORS production.

Trước nâng cấp đã sao lưu online `/data/backups/control-20260917-before-berth-scope.sqlite3`, tải về `backend/.data/backups/production-20260917-before-berth-scope.sqlite3` và kiểm tra khôi phục đạt ở cả hai nơi. Schema và cơ chế xác thực không đổi; việc nâng cấp không reset mật khẩu hoặc thay kho `/data/control.sqlite3`. Các ghi nhận ngày 13/09 bên dưới được giữ lại làm lịch sử triển khai.

Hướng dẫn này dành cho bản có đăng nhập nội bộ, kế hoạch và báo cáo chốt. Commit `beeb1f8` đã được push lên `main`; GitHub Actions run `34758456463` thành công. Railway đã chạy commit mới trong project `keen-reverence`, environment `production`; Vercel đã hiển thị trang đăng nhập mới tại [dashboard production](https://dashboard-sanluong.vercel.app).

Đã xác nhận API chạy dưới UID `10001`, volume 500 MB mount tại `/data`, và health có xác thực kết nối được cả hai nguồn SQL. Dashboard, chi tiết tác nghiệp, tìm chuyến và xuất Excel đã được kiểm tra bằng dữ liệu nguồn thật. Sao lưu thủ công từ volume production và kiểm tra khôi phục trên database tạm ở container/local đã đạt. Sau khởi động lại production, tài khoản và phiên đăng nhập vẫn còn, bản báo cáo cũ được phục hồi từ ổ đĩa với số truy vấn nguồn bằng 0. Tài khoản bàn giao đã được xác minh và yêu cầu đổi mật khẩu ở lần đăng nhập đầu; lịch backup tự động chưa được bật. Build Docker, khởi động lại với volume thử và kiểm tra sao lưu/khôi phục tại local cũng đã đạt.

**Railway: cấu hình service API**

| Thiết lập | Giá trị của dự án |
|---|---|
| Root Directory | `backend` |
| Cách build | Dockerfile trong thư mục `backend` |
| Start Command | Giữ mặc định của Docker; không bỏ qua `runtime_entrypoint.py` |
| Target Port của public domain | `8000`, theo Docker CMD hiện tại |
| `PORT` | `8000`, đặt rõ trong Variables để khớp cổng API và healthcheck |
| Healthcheck Path | `/api/health/live` |
| Số instance / worker API | Một instance, một worker với kho SQLite hiện tại |
| Volume Mount Path | `/data` |
| `DASHBOARD_STATE_PATH` | `/data/control.sqlite3` |
| `RAILWAY_RUN_UID` | `0`, chỉ để entrypoint chuẩn bị quyền rồi hạ quyền API |
| `FRONTEND_ORIGIN` | `https://dashboard-sanluong.vercel.app`, hoặc origin frontend chính thực tế |
| `CORS_ORIGINS` | JSON array các origin bổ sung được duyệt; ví dụ `[]` cho production chỉ dùng frontend chính |
| `REPORT_CACHE_TTL_SECONDS` | Mặc định và runtime đã xác nhận `120`, số nguyên từ `1` đến `300`; triển khai lại API sau khi đổi |

Cấu hình production đã áp dụng `PORT=8000`, `DASHBOARD_STATE_PATH=/data/control.sqlite3`, `RAILWAY_RUN_UID=0`, `FRONTEND_ORIGIN=https://dashboard-sanluong.vercel.app` và `CORS_ORIGINS=[]`. Lần triển khai đầu bị lỗi healthcheck vì runtime có `PORT=8080` trong khi Docker CMD chạy API ở `8000`; đặt `PORT=8000` đã khắc phục. Khi đổi cổng, phải cập nhật đồng bộ lệnh chạy API, biến `PORT` và Target Port của domain. [Railway: cổng healthcheck](https://docs.railway.com/deployments/healthchecks#configure-the-healthcheck-port)

SQLite lưu tài khoản, phiên đăng nhập, kế hoạch, báo cáo chốt và lịch sử đối soát. Đây là dữ liệu riêng của dashboard; không đặt file này trong SQL Server TOS, thư mục build hoặc Vercel. Railway mount volume lúc container bắt đầu chạy; volume không có ở bước build hay pre-deploy. Vì thế lệnh `chown` trong Docker build không thay thế việc chuẩn bị quyền trên volume thật. [Railway: vòng đời volume](https://docs.railway.com/volumes#volume-availability)

`report-cache.sqlite3` được tạo cùng thư mục với `control.sqlite3` để giữ bản tra cứu tối đa 15 phút qua lần khởi động lại/loại khỏi RAM. Kho này giới hạn 128 bản, 1.000.000 dòng và 128 MiB nội dung nén; không thay thế báo cáo đã chốt hoặc bản sao lưu. Đặt cả hai trên volume; entrypoint chuẩn bị quyền cho các file SQLite/WAL/SHM tương ứng. Không cần sao lưu cache để khôi phục tài khoản/kế hoạch. Vẫn triển khai một instance, một worker.

Trong bản tối ưu, `REPORT_CACHE_TTL_SECONDS=120` điều khiển thời gian dùng lại báo cáo cho một bộ lọc ở RAM và trường `fresh_until` của bản lưu mới trên ổ đĩa; giá trị ngoài 1–300 làm cấu hình không hợp lệ. Không đặt biến cũng nhận mặc định 120 giây. Thời gian giữ snapshot vẫn 900 giây và các giới hạn số dòng/dung lượng không đổi. Thay đổi chỉ nối vào instance API; `ReportingService()` dùng riêng trong mã/kiểm thử vẫn mặc định 30 giây.

Yêu cầu `refresh=true` bỏ qua cache: nút **Tải lại**, **Thử cập nhật lại** và cập nhật tự động đều dùng cơ chế này. Nút **Thử lại** sau lỗi tải đầu tiên cho phép dùng cache còn hạn, tránh lặp truy vấn nguồn đã hoàn tất. Cache hit giữ nguyên `source_read_at`/`report_id`. Khi cập nhật lỗi, giao diện chỉ giữ báo cáo cùng bộ lọc với cảnh báo đang dùng lần đọc trước, không ghi nhận là số liệu mới. Cấu hình cache không thay timeout SQL `DB_QUERY_TIMEOUT_SECONDS` mặc định 20 giây hay quyền đọc nguồn.

Railway mount volume bằng root. `RAILWAY_RUN_UID=0` cho phép entrypoint chạy bước khởi tạo; entrypoint của dự án chỉ xử lý thư mục state cố định và các file SQLite đã quy định, từ chối symlink, rồi chuyển API về UID/GID `10001`. Không thay bằng `chmod 777`, không chown đệ quy một đường dẫn tùy ý, không chạy uvicorn trực tiếp bằng root. Railway tự cung cấp `RAILWAY_VOLUME_MOUNT_PATH`; kiểm tra giá trị runtime trùng `/data`, không tự đặt biến này để giả lập volume. [Railway: quyền volume](https://docs.railway.com/volumes#permissions), [biến runtime](https://docs.railway.com/variables/reference)

Nếu log báo volume chưa mount hoặc không có quyền ghi, sửa mount/quyền trước; không đổi sang `/tmp` để vượt qua lỗi. Việc tạo lại container phải giữ nguyên SQLite và các tài khoản. Không tăng số replica khi còn dùng volume SQLite và bộ nhớ phiên báo cáo trong process; muốn mở rộng cần đổi thiết kế lưu trữ trước. [Railway: giới hạn volume](https://docs.railway.com/volumes/reference)

Nhập các biến SQL Server qua Railway Variables: `DB_SERVER`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD`, `DB_DRIVER`. Driver trong image là `ODBC Driver 17 for SQL Server`; `DB_DATABASE` nhận database đã được allowlist trong code. Không đưa mật khẩu, chuỗi kết nối hoặc thông tin API TOS vào Git hay biến `VITE_*`.

Giữ `DB_ENCRYPT=true`. Mặc định `DB_TRUST_SERVER_CERTIFICATE=false` kiểm tra chứng chỉ; nguồn dùng CA nội bộ cần CA được tin cậy trên container và hostname khớp chứng chỉ. Chỉ đặt `DB_TRUST_SERVER_CERTIFICATE=true` khi tiếp tục một ngoại lệ TLS đã được quản trị chấp nhận; ứng dụng không tự chuyển sang chế độ này khi lỗi. Log chỉ ghi loại lỗi và mã SQLSTATE đã lọc, không ghi mật khẩu hay endpoint nguồn. [Microsoft: kiểm tra chứng chỉ ODBC](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/connection-string-keywords-and-data-source-names-dsns?view=sql-server-ver17)

**Tạo quản trị viên đầu tiên**

Không có tài khoản hoặc mật khẩu mặc định. Bootstrap chỉ áp dụng cho kho dashboard mới chưa có tài khoản; không chạy lại để thay quản trị viên hoặc thay database đang sử dụng. Có thể chạy CLI trong container đã mount đúng volume, hoặc chuẩn bị một database mới riêng rồi đưa vào volume đã xác nhận trống trước lần triển khai đầu tiên. Không chạy bootstrap ở build/pre-deploy. `railway run` trên máy cá nhân vẫn ghi vào filesystem của máy cá nhân, không tự ghi vào volume production.

Cách chạy trực tiếp trong container:

Sau khi đăng nhập Railway CLI và chọn đúng project, environment, service, chạy từ máy quản trị:

```text
railway ssh -- python /app/runtime_entrypoint.py python /app/manage_users.py bootstrap-admin --username admin --output /data/.dashboard-access.txt
```

Lệnh SSH chạy trong container. Gọi qua entrypoint giúp CLI dùng cùng quyền UID `10001` như API, tránh tạo SQLite do root sở hữu. Mật khẩu tạm chỉ được ghi vào file được chỉ định; CLI không in mật khẩu và không ghi đè file/tài khoản sẵn có. [Railway: SSH vào service](https://docs.railway.com/cli/ssh)

Tải file riêng qua công cụ volume hoặc SFTP đã xác thực. Ví dụ sau chạy tại thư mục dự án, với `backend/.data` đã tồn tại và được Git ignore:

```text
railway volume files download /.dashboard-access.txt ./backend/.data/.dashboard-access-production.txt
```

Kiểm tra đúng volume trước khi tải; đường dẫn từ công cụ volume tính từ gốc volume. [Railway: quản lý file volume](https://docs.railway.com/cli/volume)

Nếu container chưa sẵn sàng cho SSH, có thể chuẩn bị lần đầu từ máy quản trị theo thứ tự sau:

1. Xác nhận đúng project/environment/service và volume `/data` hoàn toàn mới, chưa có `control.sqlite3` hoặc dữ liệu người dùng. Thực hiện trước khi API bắt đầu ghi vào volume; không dùng cách này để ghi đè kho đang hoạt động.
2. Tạo thư mục bootstrap riêng trong vùng được Git ignore. Dùng `manage_users.py bootstrap-admin --state-path <database-mới-riêng> --output <file-mật-khẩu-riêng>` để tạo một quản trị viên mới. Không dùng lại database local đang chứa tài khoản thử, kế hoạch hay báo cáo chốt.
3. Chạy `state_backup.py backup --source <database-mới-riêng> --output <bản-sao-mới>` rồi `state_backup.py verify --backup <bản-sao-mới>`. Lệnh backup dùng SQLite Online Backup API, giữ nội dung nhất quán kể cả khi SQLite có WAL.
4. Dùng công cụ volume đã xác thực để tải **bản sao đã kiểm tra** vào `/control.sqlite3` tính từ gốc volume, tương ứng `/data/control.sqlite3` trong container. Giữ file mật khẩu riêng trên máy quản trị; không cần tải file này lên volume. Sau đó triển khai API để entrypoint chuẩn bị quyền file và hạ quyền tiến trình.

Quản trị viên production đầu tiên của lần triển khai `beeb1f8` đã được tạo bằng cách chuẩn bị database mới riêng và tải bản sao online vào volume đã xác nhận trống trước khi deploy. Đây là bước khởi tạo có chủ đích, không phải sao chép database thử nghiệm local sang production.

Đọc file mật khẩu trên máy quản trị, đăng nhập frontend rồi đổi mật khẩu ngay theo yêu cầu của ứng dụng. Không dán mật khẩu vào log, ticket hoặc screenshot. Sau đó tạo tài khoản riêng cho người dùng trong màn hình quản trị; file mật khẩu tạm có thể được xóa sau khi xác nhận đăng nhập bằng mật khẩu mới.

| Vai trò | Quyền trong phạm vi cảng được cấp |
|---|---|
| `viewer` | Xem báo cáo, dữ liệu tác nghiệp và báo cáo đã chốt |
| `manager` | Quyền xem; tạo/duyệt kế hoạch, chốt báo cáo và ghi nhận xử lý đối soát |
| `admin` | Quyền quản lý; tạo, khóa, phân quyền và reset mật khẩu tài khoản |

Session bearer hết hạn sau 8 giờ. Đổi/reset mật khẩu hoặc thay đổi quyền/khóa tài khoản thu hồi phiên liên quan. Không có biến môi trường tắt xác thực. `/api/health/live` chỉ xác nhận tiến trình API đang chạy; kiểm tra nguồn TOS bằng `/api/health` sau khi đăng nhập với quyền quản trị.

**Vercel: cấu hình frontend**

| Thiết lập | Giá trị |
|---|---|
| Root Directory | `frontend` |
| Framework Preset | Vite |
| Install Command | `npm ci` |
| Build Command | `npm run build` |
| Output Directory | `dist` |
| `VITE_API_URL` | `https://<domain-api>.up.railway.app/api` |

Đặt biến đúng environment Production hoặc Preview và tạo bản build mới khi đổi giá trị. Origin frontend phải khớp allowlist của API, gồm scheme và hostname, không có path hoặc dấu `/` cuối. Preview URL khác cần được thêm exact origin nếu cho phép truy cập; không mở wildcard cho mọi `vercel.app`. `VITE_API_URL` là địa chỉ API công khai, không phải nơi lưu secret. [Vercel: Vite](https://vercel.com/docs/frameworks/frontend/vite), [biến môi trường](https://vercel.com/docs/environment-variables)

**Kiểm tra trước khi bàn giao và sao lưu**

Đã tạo bản sao online từ database production tại `/data/backups/control-20260913-handoff.sqlite3`, kiểm tra khôi phục vào database tạm trong container, rồi tải về `backend/.data/backups/production-20260913-handoff.sqlite3` trong vùng local được Git ignore. Kiểm tra khôi phục local đạt và dấu vết nội dung khớp bản sao trên container. Việc kiểm chứng không thay database đang chạy hoặc tạo kế hoạch thử trên production.

Trạng thái lịch backup production: yêu cầu bật lịch backup volume trả về `Not Authorized`, nên chưa xác nhận được lịch sao lưu tự động. Chưa có kết luận về nguyên nhân hoặc giới hạn gói dịch vụ. Cần xử lý và xác nhận lịch trong Railway. Kiểm tra dữ liệu qua lần khởi động lại production đã đạt; chưa tạo kế hoạch hoặc báo cáo chốt giả lập trên production.

1. Đăng nhập tài khoản mới, đổi mật khẩu tạm; kiểm tra logout và một tài khoản viewer bị chặn thao tác quản trị. Tài khoản một cảng không được truy cập báo cáo của cảng khác bằng cách sửa URL/API.
2. Xem dashboard và chi tiết tác nghiệp; kiểm tra phản hồi TOS thật, không chỉ `/api/health/live`. Lỗi kết nối phải hiện lỗi, không biến thành số liệu 0.
3. Nhập một kế hoạch từ mẫu Excel dưới dạng nháp, kiểm tra trước khi duyệt. Chốt một kỳ thử đã được phép, xuất Excel và so sánh lại trên cùng phạm vi dữ liệu.
4. Khởi động lại service trong cửa sổ bảo trì rồi xác nhận tài khoản, kế hoạch và báo cáo chốt vẫn còn. Nếu dữ liệu biến mất, dừng bàn giao và kiểm tra `DASHBOARD_STATE_PATH`/mount.
5. Bật backup volume định kỳ và thử khôi phục vào environment riêng. File SQLite và bản backup chứa dữ liệu nội bộ; không commit hoặc tải lên Vercel. Railway hỗ trợ backup dữ liệu volume, bao gồm SQLite. [Railway: backup volume](https://docs.railway.com/volumes/backups)

Khi cần bản sao SQLite thủ công trong lúc API hoạt động, dùng SQLite Online Backup API; không chỉ copy riêng `control.sqlite3` trong khi WAL còn thay đổi. Khi khôi phục, giữ bản đang dùng để có thể quay lại và kiểm tra quyền file trước khi chạy API. [Python: `Connection.backup`](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup)

CLI có sẵn, chạy từ thư mục gốc local (chọn tên mới cho mỗi bản sao, không ghi đè):

```powershell
.\.venv-audit\Scripts\python.exe -B -m backend.state_backup backup --output backend/.data/backups/control-YYYYMMDD-HHMM.sqlite3
.\.venv-audit\Scripts\python.exe -B -m backend.state_backup verify --backup backend/.data/backups/control-YYYYMMDD-HHMM.sqlite3
```

`verify` khôi phục vào database tạm riêng, kiểm tra toàn vẹn/khóa ngoại và so sánh dấu vết nội dung theo từng dòng; không thay database đang chạy. Trong container, dùng cùng CLI qua entrypoint:

```text
railway ssh -- python /app/runtime_entrypoint.py python /app/state_backup.py backup --output /data/backups/control-YYYYMMDD-HHMM.sqlite3
railway ssh -- python /app/runtime_entrypoint.py python /app/state_backup.py verify --backup /data/backups/control-YYYYMMDD-HHMM.sqlite3
```

Bản sao cùng volume chỉ hỗ trợ thao tác bảo trì. Bật thêm backup volume định kỳ và lưu bản sao ở nơi độc lập theo chính sách công ty để ứng phó mất volume. Trước khi nâng cấp, sao lưu `control.sqlite3`; migration thêm lịch sử/revision cho nháp, giữ nguyên kế hoạch đã duyệt và bản chốt cũ. Frontend mới gửi revision khi sửa/hủy/duyệt; tải lại trang sau khi triển khai đồng bộ API/giao diện.
