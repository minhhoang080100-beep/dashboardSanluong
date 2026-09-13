**Triển khai dashboard nội bộ trên Railway và Vercel — cập nhật 13/09/2026**

Hướng dẫn này dành cho bản có đăng nhập nội bộ, kế hoạch và báo cáo chốt. Đã kiểm tra build Docker, khởi động lại với volume thử, sao lưu và khôi phục tại local. Railway CLI chưa đăng nhập nên các bước cấu hình/xác minh production dưới đây chưa được thực hiện. Chuẩn bị volume, tài khoản quản trị và kiểm tra lưu bền vững trước khi mở bản nâng cấp cho người dùng.

**Railway: cấu hình service API**

| Thiết lập | Giá trị của dự án |
|---|---|
| Root Directory | `backend` |
| Cách build | Dockerfile trong thư mục `backend` |
| Start Command | Giữ mặc định của Docker; không bỏ qua `runtime_entrypoint.py` |
| Target Port của public domain | `8000`, theo Docker CMD hiện tại |
| Healthcheck Path | `/api/health/live` |
| Số instance / worker API | Một instance, một worker với kho SQLite hiện tại |
| Volume Mount Path | `/data` |
| `DASHBOARD_STATE_PATH` | `/data/control.sqlite3` |
| `RAILWAY_RUN_UID` | `0`, chỉ để entrypoint chuẩn bị quyền rồi hạ quyền API |
| `FRONTEND_ORIGIN` | `https://dashboard-sanluong.vercel.app`, hoặc origin frontend chính thực tế |
| `CORS_ORIGINS` | JSON array các origin bổ sung được duyệt; ví dụ `[]` cho production chỉ dùng frontend chính |

SQLite lưu tài khoản, phiên đăng nhập, kế hoạch, báo cáo chốt và lịch sử đối soát. Đây là dữ liệu riêng của dashboard; không đặt file này trong SQL Server TOS, thư mục build hoặc Vercel. Railway mount volume lúc container bắt đầu chạy; volume không có ở bước build hay pre-deploy. Vì thế lệnh `chown` trong Docker build không thay thế việc chuẩn bị quyền trên volume thật. [Railway: vòng đời volume](https://docs.railway.com/volumes#volume-availability)

`report-cache.sqlite3` được tạo cùng thư mục với `control.sqlite3` để giữ bản tra cứu tối đa 15 phút qua lần khởi động lại/loại khỏi RAM. Kho này giới hạn 128 bản, 1.000.000 dòng và 128 MiB nội dung nén; không thay thế báo cáo đã chốt hoặc bản sao lưu. Đặt cả hai trên volume; entrypoint chuẩn bị quyền cho các file SQLite/WAL/SHM tương ứng. Không cần sao lưu cache để khôi phục tài khoản/kế hoạch. Vẫn triển khai một instance, một worker.

Railway mount volume bằng root. `RAILWAY_RUN_UID=0` cho phép entrypoint chạy bước khởi tạo; entrypoint của dự án chỉ xử lý thư mục state cố định và các file SQLite đã quy định, từ chối symlink, rồi chuyển API về UID/GID `10001`. Không thay bằng `chmod 777`, không chown đệ quy một đường dẫn tùy ý, không chạy uvicorn trực tiếp bằng root. Railway tự cung cấp `RAILWAY_VOLUME_MOUNT_PATH`; kiểm tra giá trị runtime trùng `/data`, không tự đặt biến này để giả lập volume. [Railway: quyền volume](https://docs.railway.com/volumes#permissions), [biến runtime](https://docs.railway.com/variables/reference)

Nếu log báo volume chưa mount hoặc không có quyền ghi, sửa mount/quyền trước; không đổi sang `/tmp` để vượt qua lỗi. Việc tạo lại container phải giữ nguyên SQLite và các tài khoản. Không tăng số replica khi còn dùng volume SQLite và bộ nhớ phiên báo cáo trong process; muốn mở rộng cần đổi thiết kế lưu trữ trước. [Railway: giới hạn volume](https://docs.railway.com/volumes/reference)

Nhập các biến SQL Server qua Railway Variables: `DB_SERVER`, `DB_DATABASE`, `DB_USERNAME`, `DB_PASSWORD`, `DB_DRIVER`. Driver trong image là `ODBC Driver 17 for SQL Server`; `DB_DATABASE` nhận database đã được allowlist trong code. Không đưa mật khẩu, chuỗi kết nối hoặc thông tin API TOS vào Git hay biến `VITE_*`.

Giữ `DB_ENCRYPT=true`. Mặc định `DB_TRUST_SERVER_CERTIFICATE=false` kiểm tra chứng chỉ; nguồn dùng CA nội bộ cần CA được tin cậy trên container và hostname khớp chứng chỉ. Chỉ đặt `DB_TRUST_SERVER_CERTIFICATE=true` khi tiếp tục một ngoại lệ TLS đã được quản trị chấp nhận; ứng dụng không tự chuyển sang chế độ này khi lỗi. Log chỉ ghi loại lỗi và mã SQLSTATE đã lọc, không ghi mật khẩu hay endpoint nguồn. [Microsoft: kiểm tra chứng chỉ ODBC](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/connection-string-keywords-and-data-source-names-dsns?view=sql-server-ver17)

**Tạo quản trị viên đầu tiên**

Không có tài khoản hoặc mật khẩu mặc định. Cần một container maintenance/staging có mã mới và đúng volume để chạy CLI trước khi bàn giao truy cập. Không chạy bootstrap ở build/pre-deploy, không dùng `railway run` trên máy cá nhân rồi hiểu nhầm rằng đã ghi vào volume production.

Sau khi đăng nhập Railway CLI và chọn đúng project, environment, service, chạy từ máy quản trị:

```text
railway ssh -- python /app/runtime_entrypoint.py python /app/manage_users.py bootstrap-admin --username admin --output /data/.dashboard-access.txt
```

Lệnh SSH chạy trong container. Gọi qua entrypoint giúp CLI dùng cùng quyền UID `10001` như API, tránh tạo SQLite do root sở hữu. Mật khẩu tạm chỉ được ghi vào file được chỉ định; CLI không in mật khẩu và không ghi đè file/tài khoản sẵn có. [Railway: SSH vào service](https://docs.railway.com/cli/ssh)

Tải file riêng qua công cụ volume hoặc SFTP đã xác thực. Ví dụ sau chạy tại thư mục dự án, với `backend/.data` đã tồn tại và được Git ignore:

```text
railway volume files download /.dashboard-access.txt ./backend/.data/.dashboard-access-production.txt
```

Kiểm tra đúng volume trước khi tải; đường dẫn từ công cụ volume tính từ gốc volume. Đọc file trên máy quản trị, đăng nhập frontend rồi đổi mật khẩu ngay theo yêu cầu của ứng dụng. Không dán mật khẩu vào log, ticket hoặc screenshot. Sau đó tạo tài khoản riêng cho người dùng trong màn hình quản trị; file mật khẩu tạm có thể được xóa sau khi xác nhận đăng nhập bằng mật khẩu mới. [Railway: quản lý file volume](https://docs.railway.com/cli/volume)

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
