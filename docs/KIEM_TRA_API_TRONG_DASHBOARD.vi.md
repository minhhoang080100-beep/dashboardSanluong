# Kiểm tra API trong dashboard

Mục **Kiểm tra API** nằm trên thanh điều hướng, đường dẫn trong ứng dụng là `#api-inspector`. Chỉ quản trị viên có quyền cả Cửa Lò và Bến Thủy mới sử dụng được vì dữ liệu API Tổng công ty thuộc phạm vi toàn công ty. Quyền được kiểm tra tại máy chủ trước khi đọc dữ liệu.

## Cách sử dụng

1. Đăng nhập dashboard và mở **Kiểm tra API**.
2. Mặc định chọn **Tài khoản API**. Nhập tài khoản/mật khẩu API được cấp riêng rồi bấm **Đăng nhập API**. Đây không phải tài khoản đăng nhập dashboard. Khi thành công, trang hiển thị mã HTTP, tài khoản và thời điểm hết hạn; không hiển thị token.
3. Tìm hoặc chọn API trong ba nhóm: sản lượng, danh mục bộ S, danh mục vận hành. Danh sách có đủ 32 API dữ liệu đã thực hiện; không có RORO.
4. Nhập bộ lọc phù hợp, chọn số dòng mỗi trang rồi bấm kiểm tra. Ngày được chọn bằng lịch và gửi theo định dạng của hợp đồng API. Khi API ghi **Truy vấn SmartTOS**, khoảng ngày mặc định là từ đầu tháng hiện tại đến hôm nay; mỗi lần đọc API sản lượng tối đa **31 ngày**, tính cả ngày đầu và cuối. Kỳ dài hơn cần chia theo tháng. Giới hạn này không áp dụng cho API danh mục. Trình duyệt gọi GET công khai với token API vừa nhận để kiểm tra đúng quyền tài khoản.
5. Xem mã HTTP, thời gian xử lý, các header phân trang và nội dung kết quả dạng bảng hoặc JSON. Có thể sao chép JSON/đường dẫn yêu cầu để đối chiếu.
6. Bấm **Đăng xuất API** để thu hồi phiên API hiện tại trên máy chủ và xóa phiên khỏi màn hình. Thao tác này không đăng xuất dashboard hoặc thu hồi các token API khác.

Khi đọc trực tiếp SmartTOS, mỗi lần gọi API đều truy vấn lại nguồn, kể cả khi giữ nguyên bộ lọc hoặc chuyển trang. Phân trang dùng `page` và `limit`, không dùng `snapshotId`; `X-Source-Read-At` thể hiện thời điểm đọc của yêu cầu hiện tại. Dữ liệu có thể thay đổi giữa các trang nếu SmartTOS được cập nhật. Không cần nạp trước vào kho export SQLite và không dùng lại kết quả yêu cầu trước.

Với bộ đọc công bố, các trang tiếp theo vẫn giữ `snapshotId` của lần kiểm tra đầu. Nếu phiên phân trang hết hạn, chạy lại từ trang đầu. Đổi API hoặc bộ lọc sẽ xóa kết quả đang xem trước khi kiểm tra lại.

## Hiểu kết quả

- **Truy vấn SmartTOS** cho biết mỗi lần gọi API sẽ truy vấn lại nguồn. Nhãn này không khẳng định kết nối hoặc dữ liệu đã hợp lệ; trạng thái HTTP và kết quả bên dưới mới phản ánh lần đọc thực tế.
- **Có bản công bố** chỉ cho biết API có snapshot trong kho, không bảo đảm mọi kỳ đều có dữ liệu hoặc bản đọc còn mới.
- **Chưa công bố** nghĩa là chưa có bản export hợp lệ của API đó; không phải kết quả sản lượng bằng 0.
- HTTP 200 với `data: []` là kết quả rỗng hợp lệ cho yêu cầu vừa kiểm tra.
- Lỗi quá hạn dữ liệu, thiếu phạm vi ngày, thiếu bộ lọc hoặc snapshot hết hạn được giữ nguyên để đối chiếu. Không dùng bản cũ hoặc số liệu mẫu thay thế lỗi.
- Nếu API công khai chưa được kích hoạt, đăng nhập trả 503 `CORPORATE_API_DISABLED`. Trang hiển thị rõ trạng thái này; không dùng tài khoản thử hoặc dữ liệu nội bộ để giả thành đăng nhập thành công.
- Sai tài khoản/mật khẩu API hoặc token hết hạn trả 401 và chỉ ảnh hưởng phiên API. Lỗi 403 giữ phiên API để người dùng đối chiếu quyền tài nguyên. Đăng nhập thành công không bảo đảm nguồn dữ liệu sẵn sàng hoặc tài khoản được cấp tất cả API.
- Với truy vấn sản lượng trực tiếp dài hơn 31 ngày, màn hình báo lỗi trước khi gửi và giữ nguyên bộ lọc để sửa. Gọi API trực tiếp ngoài màn hình với khoảng ngày vượt giới hạn sẽ nhận HTTP 422.

Chọn **Dữ liệu nội bộ** để dùng quyền quản trị dashboard đọc dữ liệu bằng cùng bộ đọc đang được cấu hình: truy vấn SmartTOS khi chọn bộ đọc trực tiếp, hoặc đọc kho export khi chọn bộ đọc công bố. Chế độ này dùng được kể cả khi API công khai đang tắt; không dùng tài khoản máy và không kiểm tra đăng nhập, CORS hay khả năng truy cập từ bên ngoài. Hai chế độ không tự chuyển khi có lỗi.

Chế độ **Tài khoản API** thử HTTP thật tới máy chủ được cấu hình cho dashboard, bao gồm đăng nhập và header Authorization. Nó chứng minh kết nối từ trình duyệt đang kiểm tra, không thay thế kiểm thử từ hệ thống Tổng công ty. Không cho nhập URL máy chủ tùy ý. Token API chỉ nằm trong bộ nhớ của mục này; không lưu localStorage/sessionStorage, không đưa vào URL/clipboard hay dùng thay token dashboard. Ô mật khẩu được xóa khi gửi. Rời mục này hoặc tải lại trang sẽ bỏ phiên ở trình duyệt; muốn thu hồi ngay trên máy chủ hãy bấm **Đăng xuất API** trước khi rời trang. Nếu máy chủ không xác nhận đăng xuất, giao diện xóa phiên cục bộ và báo rõ; token máy chủ vẫn chịu thời hạn đã cấp.

## Cơ chế

- `GET /api/admin/corporate-api/catalog`: danh sách cố định và bộ lọc được hỗ trợ. Với bộ đọc trực tiếp, phản hồi có `readMode: "live"` và mỗi API có `status: "live"`; không có phiên snapshot và chưa biết số dòng trước khi chạy. Bộ đọc công bố giữ metadata của snapshot hiện tại và tương thích phản hồi cũ không có `readMode`.
- `POST /api/login`: đăng nhập tài khoản máy với `Username`/`Password`; trả token riêng và thời hạn tối đa 8 giờ. Frontend không tự thử lại khi lỗi mạng/timeout.
- GET của tài nguyên đã chọn gọi trực tiếp API công khai khi ở chế độ tài khoản API; giữ nguyên mã HTTP, body và header được cho phép để phân trang.
- `POST /api/logout`: thu hồi đúng token trong header Bearer; kiểm tra lại trong transaction. Token đã thu hồi/hết hạn trả 401. Không có thao tác thu hồi toàn bộ phiên của tài khoản từ màn hình này.
- `POST /api/admin/corporate-api/inspect`: chỉ dùng ở chế độ dữ liệu nội bộ. Thao tác chỉ đọc một API trong danh sách cho phép; không nhận URL tùy ý, câu SQL hoặc thao tác công bố.
- Kết quả inspect nội bộ dùng HTTP ngoài cho quyền truy cập dashboard; trường `statusCode`, `headers`, `body` bên trong mô tả kết quả của bộ đọc API. `body` vẫn gồm `data`, `code`, `message`.
- Bộ đọc trực tiếp truy vấn SQL Server SmartTOS trong từng yêu cầu, không giữ kết quả để tái sử dụng cho yêu cầu tiếp theo và không yêu cầu kho export đã công bố. Header phân trang gồm số trang, số dòng, tổng dòng, còn trang sau hay không và thời điểm đọc; không trả `X-Snapshot-Id`. Bộ đọc công bố tiếp tục đọc SQLite, kiểm tra độ mới, phạm vi ngày và snapshot. Cả hai không lấy các file riêng trong `outputs` làm nguồn production.
- Màn hình không cấp tài khoản máy, công bố dữ liệu hoặc đổi phân quyền. Các route công khai hiện có giữ nguyên cơ chế xác thực. Tài khoản API được quản lý bằng công cụ `backend.corporate_api.manage_clients` theo hướng dẫn vận hành.

## Kiểm chứng

Kiểm tra ngày 27/09/2026:

- 941 kiểm thử bộ API đạt, trong đó có 67 kiểm thử mới cho mục kiểm tra API. Bao gồm phân quyền trước khi đọc kho, phiên dashboard thật trên kho thử nghiệm, so sánh 32 API với bộ đọc công khai, lỗi dữ liệu quá hạn và phân trang giữ snapshot. Năm tình huống còn đi qua đăng nhập máy trên kho thử nghiệm để so sánh HTTP, header và nội dung phản hồi.
- 4 kiểm thử control API đạt; 171 kiểm thử frontend đạt, gồm 9 kiểm thử xử lý tham số/kết quả API và 2 kiểm thử điều hướng/phạm vi quyền.
- Lint và build production frontend thành công. Đã rà soát CSS responsive, căn lề bảng và khoảng cách với các trang hiện có.
- Kiểm thử dùng dữ liệu giả lập trong kho tạm, không xác nhận nguồn production đã đầy đủ hoặc còn mới. Không có trình duyệt khả dụng trong công cụ Computer Use ở lượt này; chưa kiểm tra hiển thị và thao tác bằng trình duyệt thực tế.
- Chưa push/deploy. Không khởi động server local; các cổng dự án 5173, 8000, 8765 không có listener lúc kiểm tra.

Kiểm tra bổ sung ngày 29/09/2026 cho đăng nhập API riêng:

- 89 kiểm thử backend nhóm đăng nhập, API công khai và đăng xuất đạt. Có kiểm tra đăng nhập thật trên kho tạm, thu hồi đúng một token trong khi token khác vẫn dùng được, token dashboard bị từ chối, hết hạn và đăng xuất đồng thời.
- 189 kiểm thử frontend đạt, gồm 18 kiểm thử mới cho HTTP đăng nhập/đọc/đăng xuất API. Bao gồm tách khỏi phiên dashboard, giữ nguyên mật khẩu khi gửi, không lưu token vào trình duyệt, lỗi HTTP/JSON, hủy yêu cầu, timeout cả lúc đọc body, phân trang và đường dẫn qua proxy.
- Lint/build frontend đạt. Rà soát giao diện xử lý 401 trước bước kiểm tra cấu trúc JSON, xóa kết quả cũ khi đổi tài khoản/chế độ và không giữ thông báo HTTP 200 khi phiên đã hết hạn.
- Công cụ Computer Use không có trình duyệt kết nối; chưa xác minh hiển thị và thao tác qua trình duyệt thực tế.
- Kiểm tra chỉ đọc trên Railway: health 200, route login/inspector đã có ở bản trước, API công khai trả 503 `CORPORATE_API_DISABLED`; `CORPORATE_API_ENABLED=false` và kho tài khoản máy tại đường dẫn cấu hình/mặc định chưa tồn tại. Không bật API, cấp tài khoản production hoặc công bố dữ liệu trong lượt này. Bản bổ sung login/logout còn ở local, chưa push/deploy.

Kiểm tra frontend bổ sung ngày 29/09/2026 cho bộ đọc SmartTOS trực tiếp:

- 192 kiểm thử frontend đạt. Ba kiểm thử mới bao gồm hợp đồng danh sách/bộ đọc và nhãn tương ứng, mặc định tháng theo giờ Việt Nam, giới hạn 31 ngày kể cả ngày nhuận và bảo toàn bộ lọc khi báo lỗi. API danh mục và bộ đọc công bố không chịu giới hạn giao diện mới.
- Lint và build production frontend đạt. Các kiểm thử này xác nhận logic tham số và cấu trúc phản hồi; không thay thế kiểm tra SQL Server hoặc thao tác qua trình duyệt trên production.

Kiểm tra bổ sung sau yêu cầu không dùng lại kết quả giữa các lần gọi API:

- 194 kiểm thử frontend đạt. Hai kiểm thử mới xác nhận truy vấn live trang 2 không cần/gửi snapshot, loại snapshot cũ, nhận phân trang không có `X-Snapshot-Id` chỉ ở bộ đọc live và vẫn kiểm tra trang/giới hạn/tổng dòng. Bộ đọc công bố giữ kiểm tra snapshot như trước.
- Giao diện và tài liệu ghi rõ mỗi lần gọi đọc lại SmartTOS, dữ liệu có thể thay đổi giữa các trang. Lint và build production frontend đạt.
