**Triển khai dashboard nội bộ trên Railway và Vercel — cập nhật 13/09/2026**

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

Cấu hình production đã áp dụng `PORT=8000`, `DASHBOARD_STATE_PATH=/data/control.sqlite3`, `RAILWAY_RUN_UID=0`, `FRONTEND_ORIGIN=https://dashboard-sanluong.vercel.app` và `CORS_ORIGINS=[]`. Lần triển khai đầu bị lỗi healthcheck vì runtime có `PORT=8080` trong khi Docker CMD chạy API ở `8000`; đặt `PORT=8000` đã khắc phục. Khi đổi cổng, phải cập nhật đồng bộ lệnh chạy API, biến `PORT` và Target Port của domain. [Railway: cổng healthcheck](https://docs.railway.com/deployments/healthchecks#configure-the-healthcheck-port)

SQLite lưu tài khoản, phiên đăng nhập, kế hoạch, báo cáo chốt và lịch sử đối soát. Đây là dữ liệu riêng của dashboard; không đặt file này trong SQL Server TOS, thư mục build hoặc Vercel. Railway mount volume lúc container bắt đầu chạy; volume không có ở bước build hay pre-deploy. Vì thế lệnh `chown` trong Docker build không thay thế việc chuẩn bị quyền trên volume thật. [Railway: vòng đời volume](https://docs.railway.com/volumes#volume-availability)

`report-cache.sqlite3` được tạo cùng thư mục với `control.sqlite3` để giữ bản tra cứu tối đa 15 phút qua lần khởi động lại/loại khỏi RAM. Kho này giới hạn 128 bản, 1.000.000 dòng và 128 MiB nội dung nén; không thay thế báo cáo đã chốt hoặc bản sao lưu. Đặt cả hai trên volume; entrypoint chuẩn bị quyền cho các file SQLite/WAL/SHM tương ứng. Không cần sao lưu cache để khôi phục tài khoản/kế hoạch. Vẫn triển khai một instance, một worker.

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
