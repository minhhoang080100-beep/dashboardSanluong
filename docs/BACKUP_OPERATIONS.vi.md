# Sao lưu dữ liệu quản trị

Phạm vi sao lưu là SQLite quản trị: tài khoản, kế hoạch, phiên bản, nhật ký và báo cáo đã chốt. Không sao lưu SQL Server SmartTOS; việc sao lưu hệ thống nguồn thuộc đơn vị vận hành SQL Server. Cache báo cáo có thể tái tạo nên không nằm trong bản sao này.

## Ghi nhận thiết lập ngày 18/09/2026

Đã kiểm tra API Railway bằng truy vấn chỉ đọc: danh sách lịch backup volume production rỗng; service không có lịch cron. Đây là trạng thái được xác nhận tại thời điểm kiểm tra, không phải kết luận về giới hạn gói Railway.

Đã cài tác vụ Windows trên máy người dùng tại thư mục dự án hiện tại và chạy thử bằng chính Task Scheduler. Kết quả lần chạy lúc 21:50 ngày 18/09: `LastTaskResult = 0`; bản sao 118.784 byte đã tải về và kiểm tra khôi phục đạt. Máy chủ nhận metadata thành công lúc 21:51; file metadata có quyền `0600` và cùng chủ sở hữu với database. Lịch tiếp theo tại thời điểm kiểm tra là 08:00 ngày 19/09/2026. Việc này không triển khai mã API mới hoặc thay dữ liệu kế hoạch trên Railway.

## Quy trình tự động

Máy tính chạy Windows Task Scheduler tải bản sao từ Railway qua SSH đã xác thực. Máy chủ tạo bản sao bằng SQLite Online Backup API trong thư mục tạm rồi tự xóa thư mục tạm; không chép riêng file SQLite đang có WAL. Máy tính kiểm tra SHA-256 của file truyền về, thử khôi phục vào database tạm, kiểm tra toàn vẹn và so sánh dấu vết nội dung. Chỉ sau khi thành công mới cập nhật mốc sao lưu thành công.

Các bản sao nằm tại `backend/.data/backups/automatic`, được Git ignore. Thư mục chỉ cấp quyền cho tài khoản Windows hiện tại, SYSTEM và quản trị viên máy. Đây là bản sao độc lập với Railway; vẫn cần bảo vệ máy tính và ổ đĩa vì file chứa dữ liệu nội bộ. Không tải bản sao lên Git hoặc frontend.

Tác vụ `NgheTinhDashboard-DailyBackup` chạy mỗi ngày lúc **08:00 theo giờ máy tính**, chỉ khi tài khoản đã đăng nhập. Máy tắt, mất mạng hoặc đăng xuất sẽ không tạo được bản sao; tác vụ được cấu hình chạy bù khi có thể và thử lại tối đa 3 lần, cách nhau 15 phút. Python chạy nền bằng `pythonw.exe`, không mở cửa sổ.

Job có khóa chống chạy đồng thời. Mặc định giữ 30 bản sao mới nhất do chính job tạo. Bản sao thủ công không bị xóa; chỉ file có trong manifest, đúng tên và còn nguyên checksum mới được dọn. File bị thay đổi hoặc đường dẫn không hợp lệ được giữ lại. Không tự khôi phục hoặc thay database production.

## Cài lại và kiểm tra

Sau khi đăng nhập Railway CLI trên tài khoản Windows vận hành và tạo `.venv-audit`, chạy tại thư mục dự án:

```powershell
.\scripts\install-backup-task.ps1
.\.venv-audit\Scripts\python.exe -B backend\backup_job.py --config backend\.data\backups\automatic\job-config.json
Get-ScheduledTask -TaskName 'NgheTinhDashboard-DailyBackup'
Get-ScheduledTaskInfo -TaskName 'NgheTinhDashboard-DailyBackup'
```

Script cài đặt không chứa token hoặc mật khẩu. Job dùng phiên đăng nhập/SSH của Railway CLI trên máy. Cần đăng nhập lại khi phiên Railway hết hạn hoặc mất quyền. Đổi thư mục dự án hoặc môi trường Python thì chạy lại script cài đặt. Không cài task dưới tài khoản khác nếu chưa chuẩn bị phiên Railway cho tài khoản đó.

`backup-health.json` tại thư mục bản sao lưu lần chạy gần nhất, lần thành công gần nhất và kết quả kiểm tra khôi phục. Bản metadata không chứa đường dẫn, tài khoản hoặc nội dung dữ liệu cũng được gửi về cạnh database trên Railway để mục quản trị đọc. Nếu không gửi metadata được, job báo thất bại; bản sao đã xác minh trên máy vẫn được giữ. Nếu máy tắt hoàn toàn, máy chủ tự chuyển trạng thái thành quá hạn sau **36 giờ**, không tiếp tục hiển thị trạng thái tốt từ lần cũ.

## Khôi phục

Chạy kiểm tra trước trên bản sao đã chọn:

```powershell
.\.venv-audit\Scripts\python.exe -B -m backend.state_backup verify --backup '<đường dẫn bản sao>'
```

Khi cần khôi phục thật: dừng ghi quản trị, lưu bản dự phòng của dữ liệu đang dùng, khôi phục vào môi trường riêng để kiểm tra tài khoản/kế hoạch/báo cáo chốt, rồi mới chuyển hệ thống sang bản đã xác nhận. Công cụ tự động này chỉ sao lưu và kiểm chứng, không cung cấp lệnh ghi đè database đang chạy.

## Giới hạn giám sát

Trạng thái hiển thị xác nhận kết quả lần chạy đã báo về, không đảm bảo máy tính đang bật hay lịch Windows chưa bị người vận hành vô hiệu hóa. Quá 36 giờ không nhận được bản sao xác minh thì cảnh báo quá hạn. Việc gửi email/tin nhắn cảnh báo chưa được cấu hình; kiểm tra cảnh báo trong trang quản trị và Task Scheduler.
