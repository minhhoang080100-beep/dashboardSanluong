# Rà soát API ngày 27/09/2026

Phạm vi: 12 API dữ liệu bộ S và 20 danh mục vận hành Bulk/Container đã thực hiện. Không mở lại server local; kiểm tra HTTP bằng FastAPI TestClient trong tiến trình kiểm thử. Không sửa SQL nguồn, không push/deploy, không bật chính sách gộp ID hoặc chấp nhận null đang chờ xác nhận.

## Các lỗi đã sửa

### Lọc ngày khách hàng và ngày không hợp lệ

- `/api/customers` lọc theo **ngày tạo** trong `metadata.createdDate`, thay vì ưu tiên ngày sửa. Hai bản ghi cùng tạo ngày 16/09, trong đó một bản ghi được sửa ngày 25/09, vẫn thuộc kết quả lọc ngày tạo 16/09.
- Cơ sở đặc tả: sheet `10. customer`, đường dẫn `/api/customers`, các ô X27/X28 và V41/V42 trong bộ S v3 đều chỉ định `createdDate`.
- Chỉ mục các snapshot cũ được dựng lại một lần từ payload ngày nguồn, gồm cả phiên lịch sử đang giữ. Không sửa payload và không dùng ngày sửa/ngày chạy thay ngày tạo. Nếu thiếu hoặc sai ngày tạo, yêu cầu có lọc ngày trả `FILTER_NOT_READY` thay vì âm thầm bỏ dòng.
- Trường `reportDate`/`finishDate` của bộ S kiểm tra lịch thật. Giá trị có đủ tám chữ số nhưng không tồn tại như `20260230` bị từ chối; JSON vẫn dùng `yyyyMMdd`.
- Giữ hành vi hiện tại cho phép gọi danh mục khách hàng không kèm khoảng ngày. Đặc tả đánh dấu các tham số ngày là bắt buộc; điểm tương thích này cần xử lý cùng bên nhận/client khi chốt hợp đồng, tách khỏi bản sửa cơ sở lọc ngày.

### Báo cáo đối soát

- Kiểm tra schema, ID thiếu/trùng, công ty và ngày của từng dòng, kể cả danh mục đứng riêng không được mục khác tham chiếu.
- Bộ S sản lượng phải có coverage đúng kỳ; ngày sản lượng nằm trong khoảng đã đọc. Tổng đối soát chỉ cộng các dòng hợp lệ, không biến tổng một phần thành tổng đầy đủ.
- Khóa liên kết được kiểm tra cho cả S và vận hành. Danh mục cha chưa có trong preview được ghi là **chưa đủ bằng chứng**, không báo sẵn sàng giao chỉ vì `ready=true`.
- Báo cáo thêm `rowValidation`, giữ số lỗi theo trường/loại và không đưa nội dung hồ sơ vào lỗi. Phạm vi kiểm tra FK của báo cáo là `preview_only`; không giả định đã đọc kho công bố.

### Đồng bộ và trạng thái vận hành

- Chọn danh sách API rỗng/sai bị từ chối trước truy vấn, không tự chạy toàn bộ 12 API bộ S. Chỉ khi bỏ hẳn lựa chọn mới dùng phạm vi mặc định.
- Lỗi trích xuất/đối soát ghi báo cáo thất bại ngay khi còn giữ khóa tác vụ. Báo cáo thành công của lần cũ không còn bị giữ lại như thể lần mới đã thành công.
- Báo cáo lỗi ngoài vòng đồng bộ cũng tuân thủ khóa, tránh ghi đè trạng thái của tác vụ khác đang chạy. Lỗi không chứa profile, mật khẩu, SQL hay dòng dữ liệu.
- Đồng bộ riêng một API vẫn kiểm tra được các danh mục đã công bố trong kho. Cờ `actualPublicationValidated=true` chỉ xuất hiện sau bước kiểm tra công bố thực tế thành công; `deliveryReady` trong từng mục vẫn là đánh giá của preview. Khi lỗi, snapshot hiện tại được giữ nguyên.
- `--check-only` thử toàn bộ thao tác công bố trong giao dịch rồi rollback, kể cả khi kiểm tra thành công. Cách này kiểm tra cả dữ liệu lịch sử còn tham chiếu danh mục sắp thay, tránh báo kiểm tra đạt nhưng công bố thật lại thất bại. Kiểm thử so sánh toàn bộ SQL dump trước/sau để xác nhận không giữ lại thay đổi.

### Cấu hình danh mục và JSON

- Cấu hình bảng sai kiểu tạo blocker cho đúng API, không làm hỏng các API độc lập trong cùng lô.
- `report_date` sai kiểu không bị đổi ngầm thành hôm nay. Chỉ `None` mới chọn ngày hiện tại; ngày truyền vào phải là `date` hợp lệ.
- Danh sách API vận hành phải hữu hạn, đúng kiểu, không rỗng/trùng và chỉ chứa tên đã hỗ trợ.
- Các số có dấu của độ sâu/vị trí được kiểm tra khi chuyển sang số JSON. Decimal hữu hạn nhưng chuyển sang `Infinity` bị từ chối; giữ nguyên số âm hợp lệ. Đây là lỗi biên nhập preview, chưa có bằng chứng xuất hiện trong SQL thật.

## Kiểm tra lại bằng dữ liệu thật đã lưu

Helper `outputs/api-review-20260927/audit_saved_data.py` chạy lại bộ đối soát hiện tại và kiểm tra HTTP trên **bản sao tạm** của kho export. Báo cáo `results.json` chỉ giữ tổng hợp, mã lỗi và dấu thời gian; không ghi payload nhân viên/khách hàng.

- 20 API vận hành dùng các bản đọc SQL ngày **25/09**, khoảng 01:43–03:43 UTC. Một API (`serviceType`) có dữ liệu trích xuất gộp hợp lệ; 19 API còn điều kiện chưa giải quyết.
- 12 API bộ S được kiểm tra riêng từng nguồn từ bản đọc ngày **22/09**, kỳ sản lượng 16/09. Đây là kiểm tra lại bằng chứng đã lưu, không phải số liệu SQL mới ngày 27/09 hay xác nhận báo cáo tháng/năm.
- Gọi 32 đường dẫn HTTP bằng tài khoản máy tạm: body giữ đúng `data`, `code`, `message`; thiếu token bị từ chối. Kho local đã sao chép có 30 mục chưa công bố (`DATASET_NOT_READY`) và 2 bản đã quá hạn (`DATASET_STALE`). Không tắt kiểm tra độ mới để biến bản đọc cũ thành dữ liệu hiện hành.

## Những điều kiện dữ liệu còn thiếu

| Nhóm API | Điều kiện còn thiếu |
|---|---|
| Tổ/nhân sự | Trạng thái từ Excel/danh sách đã duyệt chưa được thêm vào dự án; ngày thay đổi của tổ và xung đột ID còn cần đối chiếu. |
| Thiết bị, mặt hàng | Nguồn các trường bắt buộc như số đăng ký, sê-ri, trạng thái thuê hoặc cờ hàng nguy hiểm. |
| Loại kho, giao nhận, hướng hàng, vị trí, loại tàu | Một phần/toàn bộ dòng thiếu cả ngày tạo và sửa; chưa có cơ sở lọc kỳ. |
| Kho/bãi, cầu/bến, phương án, đơn vị, nhóm hàng, kích cỡ container | ID trùng khác nội dung hoặc thiếu thuộc tính; không chọn nguồn thắng, thêm tiền tố hay bỏ dòng lịch sử. |
| Sản lượng S | Còn lỗi/thiếu ánh xạ trong bằng chứng nguồn và chưa có xác nhận chấp nhận null từ Tổng công ty. |

Thư mục `API Vận Hành` hiện có ba file đặc tả ban đầu; chưa có danh sách trạng thái bổ sung. Các quy tắc bổ sung TEU và hợp nhất ID chỉ khác ngày vẫn giữ trạng thái chưa bật như lượt trước.

## Kiểm chứng

- Lượt `python -m pytest tests -q -k corporate`: **871 passed**, 603 bài ngoài phạm vi không chạy, 2 cảnh báo deprecation của thư viện; 187,50 giây.
- Sau khi bổ sung thử công bố rồi rollback cho `--check-only`, chạy lại nhóm sync/publication/freshness: **72 passed**, gồm ba regression mới cho tham chiếu lịch sử, SQL dump không đổi và exit code thất bại.
- Chạy lại helper trên bản đọc SQL đã lưu sau các thay đổi: 20 danh mục vận hành, 12 tài nguyên S ở mỗi nguồn và 32 đường dẫn HTTP đều hoàn tất kiểm tra như mô tả trên.
- Các cổng local 5173, 8000, 8765 vẫn đóng. Không đưa file dữ liệu riêng trong `outputs` hoặc `backend/.data` lên Git.

Các kiểm thử không thay thế xác nhận nghiệp vụ của chủ dữ liệu hoặc nghiệm thu với Tổng công ty. Chưa triển khai bản sửa này lên Railway.
