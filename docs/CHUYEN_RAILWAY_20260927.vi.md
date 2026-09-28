# Chuyển Railway ngày 27/09/2026

Dashboard đã chuyển sang tài khoản Railway mới. Frontend tiếp tục dùng `https://dashboard-sanluong.vercel.app`; API mới là `https://dashboardsanluong-production-1761.up.railway.app/api`.

## Cấu hình đang dùng

| Thành phần | Giá trị |
| --- | --- |
| Railway project | `celebrated-magic` — `e07b525b-cfc7-4466-851a-da1f43b5dde3` |
| Environment | `production` — `baa03491-69fd-4ef6-88a3-a0f0f172e976` |
| Service | `dashboardSanluong` — `81481805-48ae-4613-b878-e8c0626e2ad6` |
| Volume | `dashboardsanluong-volume`, 500 MB, mount `/data` |
| Kho tài khoản và kế hoạch | `DASHBOARD_STATE_PATH=/data/control.sqlite3` |
| Vercel project | `dashboard-sanluong` |
| Vercel production deployment | `dpl_APRGQU6V2Bv1k4AZKNYt55sWeu3B` — READY |
| Vercel `VITE_API_URL` | `https://dashboardsanluong-production-1761.up.railway.app/api` |

Vercel được build lại từ nguồn của deployment production trước đó, không tải mã local chưa commit lên. Biến môi trường chỉ được đổi ở Production. Backend cloud vẫn là bản dashboard đã commit `b56c744`; bộ API Tổng công ty và mục Kiểm tra API đang phát triển ở local chưa được phát hành trong lần chuyển này.

## Dữ liệu khôi phục

Nguồn là bản sao lưu Railway cũ lúc **08:01:46 ngày 27/09/2026 (UTC+7)**, file `control-auto-20260927T010146036864Z-a04cb4c0.sqlite3`, dung lượng 118.784 byte. Đây là mốc khôi phục; chưa xác minh các thay đổi phát sinh trên Railway cũ sau thời điểm này.

Đã xác nhận kho đích trống, kiểm tra bản nguồn và thử khôi phục trên database tạm. Trước thao tác có bản sao kho đích `/data/control-before-migration-20260927.sqlite3`. Việc chép dữ liệu và kiểm tra rỗng nằm trong cùng transaction; nếu phát hiện dữ liệu ở đích hoặc kiểm tra thất bại thì rollback. Thử nghiệm với đích đã có dữ liệu xác nhận thao tác bị từ chối và dữ liệu được giữ nguyên.

Kết quả giữ nguyên toàn bộ dòng và cột từ nguồn, bao gồm hash mật khẩu và lịch sử, đồng thời giữ schema của bản đang chạy:

| Nội dung | Số bản ghi |
| --- | ---: |
| Tài khoản | 1 |
| Kế hoạch, bao gồm các phiên bản và trạng thái lưu trong nguồn | 12 |
| Lịch sử kế hoạch | 35 |
| Phiên đăng nhập lưu trong nguồn | 15 |
| Báo cáo đã chốt | 0 |

Không bootstrap tài khoản, không đặt lại mật khẩu, không ghi dữ liệu SQL Server TOS. Các phiên đã hết hạn vẫn cần đăng nhập lại bằng tài khoản cũ.

## Kiểm tra sau chuyển

- API `/api/health/live` trả 200; `/api/auth/me` không có token trả 401. CORS cho phép đúng origin `https://dashboard-sanluong.vercel.app`.
- Kết nối đọc được cả `SmartTOS` và `SmartTOS_BenThuy`.
- Gọi trực tiếp `ReportingService.get_report` trong container, lấy mới ngày **16/09/2026**, Cửa Lò, Cảng Nghệ Tĩnh: **10.790,86 tấn, 5 chuyến**, 7,318 giây. `source_status=ok`, `tonnage_status=partial`, `teu_status=ready`, `incomplete_period=false`. Trạng thái tấn chưa đầy đủ vẫn được giữ; đây là kiểm tra một ngày, không chứng minh toàn bộ kỳ báo cáo hoặc tải đồng thời.
- Domain Vercel và JavaScript trả 200; bundle `/assets/index-BuwSMViy.js` chứa API mới và không còn URL API cũ.
- Chưa kiểm tra đăng nhập tương tác bằng trình duyệt; không tạo phiên thử hoặc thay mật khẩu để thực hiện kiểm tra này.

## Sao lưu sau chuyển

Đã đổi project/environment/service trong `backend/.data/backups/automatic/job-config.json`. Giữ lịch Windows Task Scheduler `NgheTinhDashboard-DailyBackup` lúc **08:00 mỗi ngày**, giữ tối đa 30 bản theo cấu hình có sẵn. Task ở trạng thái Ready; lần chạy kế tiếp theo lịch là 28/09/2026.

Chạy thủ công với cấu hình mới đã thành công lúc **20:34:13 ngày 27/09/2026 (UTC+7)**. Bản `control-auto-20260927T133413289568Z-f2553881.sqlite3` có dung lượng **126.976 byte (124 KiB)**, kiểm tra toàn vẹn và khôi phục đạt; nội dung khớp kho vừa chuyển. Tổng 12 file sao lưu tại thời điểm kiểm tra là **1.433.600 byte**, khoảng 1,37 MiB. Bản sao nằm trên máy này; lịch tự động cần máy và kết nối mạng hoạt động.

Railway CLI hiện đăng nhập tài khoản mới. Khóa SSH đã đăng ký `nghetinh-migration-20260927` nằm trong thư mục `.ssh` của người dùng Windows để tác vụ sao lưu tiếp tục truy cập server. Không đưa khóa, file SQLite, token hoặc biến bí mật vào Git. Bản cấu hình backup trước chuyển và helper thực hiện nằm trong `outputs/railway-migration-20260927/`, đã được Git ignore.
