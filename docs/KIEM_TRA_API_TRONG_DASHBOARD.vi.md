# Kiểm tra API trong dashboard

Mục **Kiểm tra API** nằm trên thanh điều hướng, đường dẫn trong ứng dụng là `#api-inspector`. Chỉ quản trị viên có quyền cả Cửa Lò và Bến Thủy mới sử dụng được vì dữ liệu API Tổng công ty thuộc phạm vi toàn công ty. Quyền được kiểm tra tại máy chủ trước khi đọc kho dữ liệu.

## Cách sử dụng

1. Đăng nhập dashboard và mở **Kiểm tra API**.
2. Tìm hoặc chọn API trong ba nhóm: sản lượng, danh mục bộ S, danh mục vận hành. Danh sách có đủ 32 API dữ liệu đã thực hiện; không có RORO.
3. Nhập bộ lọc phù hợp, chọn số dòng mỗi trang rồi bấm kiểm tra. Ngày được chọn bằng lịch và gửi theo định dạng của hợp đồng API.
4. Xem mã HTTP, thời gian xử lý, các header phân trang và nội dung kết quả dạng bảng hoặc JSON. Có thể sao chép JSON/đường dẫn yêu cầu để đối chiếu.

Các trang tiếp theo giữ `snapshotId` của lần kiểm tra đầu. Đổi API hoặc bộ lọc sẽ bắt đầu một lần kiểm tra mới, tránh ghép dữ liệu từ nhiều phiên.

## Hiểu kết quả

- **Có bản công bố** chỉ cho biết API có snapshot trong kho, không bảo đảm mọi kỳ đều có dữ liệu hoặc bản đọc còn mới.
- **Chưa công bố** nghĩa là chưa có bản export hợp lệ của API đó; không phải kết quả sản lượng bằng 0.
- HTTP 200 với `data: []` là kết quả rỗng hợp lệ cho yêu cầu vừa kiểm tra.
- Lỗi quá hạn dữ liệu, thiếu phạm vi ngày, thiếu bộ lọc hoặc snapshot hết hạn được giữ nguyên để đối chiếu. Không dùng bản cũ hoặc số liệu mẫu thay thế lỗi.
- Nếu API công khai chưa được kích hoạt, trang vẫn cho quản trị viên kiểm tra dữ liệu nội bộ và hiển thị rõ trạng thái này.

Trang sử dụng phiên đăng nhập dashboard và cùng bộ đọc dữ liệu của API. Đây là kiểm tra dữ liệu nội bộ, **không kiểm tra đăng nhập tài khoản máy, cấu hình CORS, mạng Internet hoặc kết nối thực tế từ Tổng công ty**. Không yêu cầu nhập hay sao chép mật khẩu/token máy vào dashboard.

## Cơ chế

- `GET /api/admin/corporate-api/catalog`: danh sách cố định, bộ lọc được hỗ trợ và metadata của snapshot hiện tại.
- `POST /api/admin/corporate-api/inspect`: thao tác chỉ đọc một API trong danh sách cho phép. Không nhận URL tùy ý, câu SQL hoặc thao tác công bố.
- Kết quả inspect dùng HTTP ngoài cho quyền truy cập dashboard; trường `statusCode`, `headers`, `body` bên trong mô tả kết quả của bộ đọc API. `body` vẫn gồm `data`, `code`, `message`.
- Đọc kho export SQLite đã công bố, giữ kiểm tra độ mới, phạm vi ngày và snapshot. Không truy vấn SQL Server khi bấm kiểm tra, không lấy các file riêng trong `outputs` làm nguồn production.
- Không cấp tài khoản máy, không đổi dữ liệu hoặc phân quyền. Các route công khai hiện có giữ nguyên cơ chế xác thực.

## Kiểm chứng

Kiểm tra ngày 27/09/2026:

- 941 kiểm thử bộ API đạt, trong đó có 67 kiểm thử mới cho mục kiểm tra API. Bao gồm phân quyền trước khi đọc kho, phiên dashboard thật trên kho thử nghiệm, so sánh 32 API với bộ đọc công khai, lỗi dữ liệu quá hạn và phân trang giữ snapshot. Năm tình huống còn đi qua đăng nhập máy trên kho thử nghiệm để so sánh HTTP, header và nội dung phản hồi.
- 4 kiểm thử control API đạt; 171 kiểm thử frontend đạt, gồm 9 kiểm thử xử lý tham số/kết quả API và 2 kiểm thử điều hướng/phạm vi quyền.
- Lint và build production frontend thành công. Đã rà soát CSS responsive, căn lề bảng và khoảng cách với các trang hiện có.
- Kiểm thử dùng dữ liệu giả lập trong kho tạm, không xác nhận nguồn production đã đầy đủ hoặc còn mới. Không có trình duyệt khả dụng trong công cụ Computer Use ở lượt này; chưa kiểm tra hiển thị và thao tác bằng trình duyệt thực tế.
- Chưa push/deploy. Không khởi động server local; các cổng dự án 5173, 8000, 8765 không có listener lúc kiểm tra.
