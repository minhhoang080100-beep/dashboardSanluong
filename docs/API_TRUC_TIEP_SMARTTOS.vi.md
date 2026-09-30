# API đọc trực tiếp SmartTOS

Chế độ `live` nhận yêu cầu có token, đọc SQL Server SmartTOS, kiểm tra dữ liệu
và trả JSON `data`, `code`, `message`. **Mỗi lần gọi API sẽ truy vấn lại SmartTOS**,
kể cả cùng bộ lọc hoặc chuyển trang. Không dùng cache kết quả giữa các yêu cầu;
không cần trích xuất trước, công bố trước hoặc lưu bản sao dữ liệu sản lượng
vào SQLite trên Railway.

Kho tài khoản API, tài khoản dashboard và kế hoạch vẫn dùng volume hiện có.
Không xóa volume khi chuyển chế độ đọc.

## Cấu hình

```env
CORPORATE_API_ENABLED=true
CORPORATE_READ_MODE=live
CORPORATE_SOURCE_PROFILE=/data/corporate-source.json
CORPORATE_SYNC_ENABLED=false
```

`CORPORATE_SOURCE_PROFILE` là file ánh xạ bảng/cột và quy tắc nguồn, không phải
file dữ liệu sản lượng. Có thể dùng `CORPORATE_SYNC_PROFILE` làm đường dẫn dự phòng.
File phải hợp lệ, được rà soát, chỉ rõ cả Cửa Lò và Bến Thủy. Không lấy nguyên
profile cũ có ID tự đặt hoặc tự chấp nhận trường thiếu để bật API.

Giá trị mặc định `CORPORATE_READ_MODE=published` giữ hành vi cũ. Không tự chuyển
sang dữ liệu cũ nếu nguồn live gặp lỗi. Biến chưa đúng giá trị trả lỗi cấu hình.

Khi triển khai, giữ `published` cho đến khi cả backend và frontend mới đã
sẵn sàng; sau đó mới đổi sang `live`. Frontend cũ không hiểu trạng thái
catalog live. Việc đổi chế độ hoặc khởi động lại không xóa tài khoản.
Sau khi đổi bộ đọc, tải lại danh sách API để màn hình dùng đúng cách phân trang.

## Truy vấn và phân trang

- Sản lượng: tối đa 31 ngày, tính cả ngày đầu và cuối, mỗi yêu cầu. Kỳ nhiều
  tháng cần gọi từng khoảng; API không tự coi dữ liệu một tháng là đủ cả năm.
- Danh mục: giữ hợp đồng lọc ngày hiện có; giới hạn 31 ngày chỉ áp dụng sản lượng.
- Chỉ trích xuất danh mục được yêu cầu và những danh mục tham chiếu thực sự cần.
- Mỗi yêu cầu đọc lại nguồn và trả `X-Source-Read-At` của lần đọc hiện tại.
  Kết quả chỉ được xử lý trong yêu cầu đó, không giữ lại để dùng cho yêu cầu sau.
- Phân trang dùng `page`, `limit` cùng các bộ lọc. Không truyền `snapshotId` và
  không trả header `X-Snapshot-Id`. Những header số trang, số dòng, tổng dòng và
  còn trang sau vẫn giữ nguyên. Dữ liệu có thể thay đổi giữa các trang nếu nguồn
  được cập nhật; không bảo đảm các trang thuộc cùng một thời điểm dữ liệu.
- Kết quả mỗi yêu cầu chịu giới hạn bộ nhớ; tối đa 2 tác vụ đọc nguồn cùng lúc.
  Khi bận trả 503 kèm `Retry-After`, không tạo hàng đợi vô hạn.
- Có ngân sách 35 giây cho thao tác SQL; connect/query timeout giảm theo thời
  gian còn lại, kiểm tra giữa các đợt lấy dòng. Đây không phải cam kết toàn bộ
  thời gian HTTP, vì còn xử lý/kiểm tra dữ liệu và truyền kết quả.

Cấu hình Railway hiện tại vẫn giữ một replica/một worker vì tài khoản và kế hoạch
dùng volume SQLite. Bộ đọc live không phụ thuộc phiên phân trang hoặc cache trên
một tiến trình. Bộ đọc `published` vẫn giữ cách phân trang bằng snapshot cũ.

## Kiểm tra dữ liệu

Truy vấn trực tiếp thay cách lấy dữ liệu, không thay định nghĩa chỉ tiêu. Vẫn
kiểm tra nguồn đủ hai xí nghiệp, schema, đơn vị, ID, tham chiếu và các trường null
chưa có xác nhận. Không tự chọn một nguồn khi ID trùng khác nội dung, không coi
trường thiếu là 0 và không trả một phần sản lượng thành kết quả đầy đủ.

Mục Kiểm tra API dùng nhãn **Truy vấn SmartTOS** khi bật live. Nhãn này chỉ cách
đọc, không xác nhận từng danh mục đủ dữ liệu. Chế độ Tài khoản API và Dữ liệu
nội bộ dùng cùng bộ đọc; quyền của từng chế độ vẫn kiểm tra trước truy vấn nguồn.

## Kiểm tra ngày 29/09/2026

Lần kiểm tra trước khi bỏ cơ chế dùng lại kết quả: 998 kiểm thử nhóm API/SQL đạt;
53 kiểm thử bổ sung cho bộ đọc, phân quyền và ngân sách SQL đạt sau thay đổi fetch
theo lô. 192 kiểm thử frontend, lint và build đạt. Đây là kết quả của bản trước,
không xác nhận cơ chế mỗi yêu cầu đọc mới. Các bài kiểm thử dùng nguồn giả lập và
kho tạm, không chứng minh dữ liệu SmartTOS hiện tại đã đủ điều kiện trả cho bên nhận.

Sau khi bỏ dùng lại kết quả: 75 kiểm thử HTTP backend đạt, gồm gọi lặp lại và
trang 2 đọc dữ liệu mới, không có header snapshot. 194 kiểm thử frontend đạt;
lint và build đạt. Giao diện không gửi snapshot ở chế độ live, vẫn kiểm tra
phân trang bằng snapshot cho bộ đọc công bố.

Đã thử mã mới trong thư mục tạm trên Railway, không thay mã service đang chạy
và không ghi kho export. Hai danh mục `origins`, `class` chưa đọc được dữ liệu:
máy chủ không phân giải được tên miền SQL đang cấu hình (`EAI_AGAIN`), trong khi
phân giải `railway.com` thành công. Lệnh kết nối SQL tối thiểu lỗi `HYT00` sau
khoảng 6 giây. Kết quả này không phải bằng chứng mật khẩu sai hoặc dữ liệu rỗng.

Chưa bật `live` trên production. Cần khôi phục phân giải/kết nối máy chủ SQL,
rà soát profile đang áp dụng, rồi thử dữ liệu thật trước khi chuyển chế độ.
