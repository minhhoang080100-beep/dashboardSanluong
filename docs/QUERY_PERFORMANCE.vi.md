# Hiệu năng báo cáo và phạm vi kiểm chứng

Ghi nhận ngày 17/09/2026. Bản tối ưu đã triển khai Railway, deployment `1e75f49a-9a2c-42f0-8a41-2551d20ea92f` đạt `SUCCESS`, hash module khớp gói phát hành; runtime UID `10001`, timeout SQL 20 giây, cache 120 giây. Tại thời điểm triển khai thủ công bản này, frontend local đã cập nhật nhưng mã chưa push Git và Vercel chưa triển khai lại. Lịch sử phát hành được ghi riêng trong [hướng dẫn triển khai](DEPLOYMENT.vi.md).

## Thay đổi truy vấn và xử lý

Truy vấn tạo một danh sách `JobMethod` đủ điều kiện cho mỗi nguồn, dùng bảng biến cục bộ theo yêu cầu. Danh sách lấy ID duy nhất bằng đúng điều kiện thành viên `SANLUONG-QUACANG` hiện có, rồi nối theo ID khi đọc `TallyShift`. Cách này tránh tính lại điều kiện danh mục trên nhiều dòng tác nghiệp; không thêm phương án hay thay bộ lọc sản lượng.

Giữ nguyên kỳ hiện tại/kỳ đối chiếu, dòng nguồn, đơn vị, trạng thái dữ liệu thiếu và quy tắc `initial-berth-v1`. Bằng chứng cầu đầu vẫn được đọc để phân loại toàn chuyến trước khi tổng hợp. `DECLARE`/`INSERT` chỉ tạo và nạp bảng biến của câu lệnh; khóa chính thuộc bảng biến nội bộ, không ghi bảng TOS, không tạo index trên bảng nguồn, không có migration nguồn. Timeout truy vấn SQL mặc định và cấu hình triển khai giữ ở **20 giây**.

Python sao chép nhanh những dict fact có giá trị scalar bất biến; bản ghi lồng nhau hoặc có kiểu tùy biến vẫn được sao chép sâu. Mục đích là giảm CPU khi giữ snapshot và mở chi tiết, đồng thời giữ Decimal chính xác và cách ly dữ liệu nguồn khỏi các phép xử lý sau đó. Cấu trúc báo cáo lồng nhau vẫn được sao chép sâu trước khi trả cho người dùng.

## Đối chiếu nguồn thật có giới hạn

Phạm vi đo: **01/01–17/09/2026**, `terminal=all`, `production_scope=nghe_tinh`, hai database nguồn. Các lần đo được thực hiện trên dữ liệu nguồn thật bằng thao tác chỉ đọc.

| Truy vấn / phạm vi đo | Thời gian đo |
|---|---:|
| Bản cũ — kết nối, thực thi, nhận dữ liệu, tổng hợp | 59,373 giây |
| Ứng viên — lần 1, override timeout 60 giây riêng để chẩn đoán | 16,840 giây |
| Ứng viên — lần 2, timeout mặc định 20 giây | 25,949 giây |
| Bản đã triển khai — toàn bộ service đọc mới và lưu snapshot, timeout 20 giây | 19,502 giây |

Override 60 giây chỉ dùng cho phép đo chẩn đoán ứng viên lần 1; không thay cấu hình mặc định hoặc biến môi trường triển khai. Số đo bản cũ kết thúc sau tổng hợp nguồn; số đo service đã triển khai còn bao gồm bổ sung metadata và lưu snapshot/cache, nên hai con số không có phạm vi đo hoàn toàn đồng nhất.

Số dòng tác nghiệp và các KPI của kỳ hiện tại khớp kết quả trước tối ưu; số liệu kỳ đối chiếu cũng không đổi. Trạng thái tấn là **`partial`**; tối ưu hiệu năng không biến phần khối lượng đã biết thành số liệu đầy đủ. Mục tiêu kế hoạch năm đã duyệt được giữ nguyên, tỷ lệ đối chiếu vẫn tạm tính. Giá trị kinh doanh cụ thể được lưu trong hồ sơ kiểm chứng nội bộ, không đưa vào tài liệu công khai.

Đây là số đo thực nghiệm cho một kỳ/phạm vi, không phải SLA, percentile hoặc cam kết mọi báo cáo đều hoàn tất trong thời gian này. Tải SQL Server, cache của máy chủ và mạng có thể khác giữa các lần đo. Lần đọc bản đã triển khai được đo bằng mã dịch vụ trong một tiến trình kiểm tra riêng tại container Railway, không phải độ trễ từ trình duyệt qua HTTP đến khi vẽ xong. Tổng thời gian đọc bao gồm các bước kết nối, thực thi, nhận dữ liệu và xử lý; không đồng nghĩa với timeout 20 giây của mỗi thao tác truy vấn. Chưa suy rộng kết quả sang mọi năm, xí nghiệp hoặc phạm vi cầu khác.

Phép đo synthetic riêng 40.000 fact trên máy local cho bước sao chép: median **0,917 → 0,286 giây**; toàn bộ drilldown **4,245 → 3,697 giây**, payload trước/sau bằng nhau. Số này chỉ chứng minh phần CPU được cải thiện trong fixture, không phải số đo Railway và không cộng/trừ trực tiếp với bảng thời gian nguồn thật.

## Cache, refresh và độ mới của số liệu

Instance API dùng `REPORT_CACHE_TTL_SECONDS`, mặc định **120 giây**, chỉ nhận số nguyên **1–300**. Cấu hình áp dụng cho cache RAM và thời hạn dùng lại (`fresh_until`) của snapshot mới trên ổ đĩa. Snapshot vẫn được giữ tối đa **900 giây** để tra cứu theo `report_id`; các giới hạn dung lượng/số dòng giữ nguyên. Constructor `ReportingService()` riêng vẫn mặc định 30 giây để không đổi hành vi của người dùng mã hoặc kiểm thử hiện có.

- Mở lại cùng bộ lọc hoặc **Thử lại** sau lỗi tải ban đầu được phép dùng báo cáo còn hạn, tránh đọc nguồn trùng lặp.
- **Tải lại**, **Thử cập nhật lại** và cập nhật tự động gửi `refresh=true`, bỏ qua cache. Các yêu cầu trùng đang chạy vẫn được gom trong cùng worker.
- Cache hit giữ nguyên `report_id` và `meta.source_read_at`; cache không chứng minh TOS chưa thay đổi sau lần đọc đó.
- Nếu cập nhật lỗi, chỉ báo cáo cũ của cùng bộ lọc được giữ trên màn hình, kèm cảnh báo và thời điểm đọc cũ. Không đổi nhãn thành dữ liệu mới, không thay lỗi bằng số 0.

Kiểm thử dùng nguồn giả đã xác nhận dùng lại cả RAM/ổ đĩa ở giây 60 với đúng timestamp cũ, refresh đọc nguồn mới và tạo timestamp mới, cache hết hạn sau 120 giây trong khi snapshot cũ vẫn còn để tra cứu.

Đối chiếu thêm trên bản Railway đã triển khai, dùng instance `ReportingService` production rồi một service mới để đọc lại cache ổ đĩa:

| Thao tác trong tiến trình máy chủ | Thời gian |
|---|---:|
| Báo cáo đọc mới | 19,502 giây |
| Phần đọc nguồn trong lần trên | 19,164 giây |
| Dùng lại cache RAM | 0,0057 giây |
| Dùng lại cache ổ đĩa bằng service mới | 0,2262 giây |

Hai lần cache giữ cùng `report_id` và `source_read_at`; tổng cộng chỉ **1 lần đọc SQL nguồn**. Đây là kiểm chứng thêm về cache trên deployment thật, không phải kiểm tra thời gian qua trình duyệt.

## Kiểm chứng phát hành

- **464 backend tests đạt trong 199,94 giây; 110 frontend tests, lint và build đạt.**
- Payload báo cáo thật **459.057 byte** được hàm `validateDashboard` và `validateThroughputProgress` của frontend chấp nhận bằng Node. Health Railway và qua Vite proxy trả HTTP 200. Chưa kiểm tra trực quan bằng browser trong lượt này.
- Bản sao trước nâng cấp `before-query-performance` kiểm tra khôi phục đạt trên container và local. Hash nội dung tài khoản, kế hoạch và lịch sử không đổi sau triển khai; không ghi dữ liệu TOS.
- Kết quả đo và kiểm tra frontend lưu trong `outputs/railway-annual-performance-deployed-20260917.json` và `outputs/railway-annual-performance-frontend-check-20260917.json`; các tệp này được Git ignore.
