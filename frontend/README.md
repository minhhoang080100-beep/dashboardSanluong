# Dashboard điều hành sản xuất — Cảng Nghệ Tĩnh

Giao diện React + Vite tổng hợp dữ liệu từ API báo cáo. Tất cả chỉ tiêu và biểu đồ dùng cùng một bộ lọc ngày/xí nghiệp.

## Chạy cục bộ

Yêu cầu Node.js tương thích Vite 8 và backend đã cấu hình.

```powershell
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Mặc định giao diện gọi `/api/dashboard`; Vite chuyển tiếp `/api` đến `http://127.0.0.1:8000`. Nếu backend dùng cổng khác, cấu hình `VITE_API_PROXY_TARGET` trong `.env.local`. Khi triển khai bản build, máy chủ web phải chuyển tiếp `/api` đến backend; proxy Vite chỉ dùng trong phát triển. Có thể đặt `VITE_API_URL` thành URL gốc API khác khi cần, ví dụ `https://report.example/api`; backend phải cho phép origin của giao diện.

Không đặt mật khẩu SQL hoặc thông tin bí mật trong biến `VITE_*`: chúng có thể được đưa vào trình duyệt.

## Sử dụng báo cáo

- Chọn tháng này, tháng trước, từ đầu năm; hoặc nhập khoảng ngày và chọn xí nghiệp rồi bấm **Áp dụng**. Khoảng ngày bao gồm cả hai đầu, tối đa 366 ngày, theo giờ Việt Nam.
- Đọc **sản lượng qua cảng (tấn)**, TEU và số chuyến có phát sinh. Tấn chỉ cộng khối lượng có đơn vị nguồn xác định; các đơn vị khác hiển thị riêng tại **Sản lượng chưa cộng vào tấn**.
- Khối lượng và số lượng giữ tối đa 3 chữ số thập phân; số chuyến hiển thị số nguyên. Giá trị chưa đủ cơ sở tính hiển thị `—`. Các lưu ý và độ đầy đủ được gom trong mục **Nguồn dữ liệu & định nghĩa**, đóng mặc định.
- Biểu đồ chuyển được ngày/tháng và tấn/TEU. Kỳ tối đa 62 ngày mặc định hiển thị theo ngày. Ngày không có bản ghi theo bộ lọc hiển thị 0, không tự kết luận ngừng sản xuất. Bảng dưới biểu đồ cung cấp số liệu tương đương để đọc bằng bàn phím.
- Tỷ lệ thay đổi dùng khoảng liền trước có cùng số ngày, chỉ hiển thị khi đủ cơ sở so sánh. Tỷ trọng và thanh cơ cấu không hiển thị khi có dữ liệu điều chỉnh âm hoặc tổng chưa đủ cơ sở; số lượng có dấu vẫn được giữ nguyên.
- Mục **Chuyến tàu** hiển thị danh sách cùng phạm vi với KPI; tìm bằng tên tàu hoặc mã chuyến, có thể nhập tên không dấu. **Xem chi tiết** mở thông tin đến/rời thực tế nếu có, hàng hóa/ngày làm hàng và phiếu tác nghiệp theo từng trang 25 dòng. Dùng nút đóng hoặc phím Esc để quay về danh sách.
- **Tải lại** truy vấn lại kỳ hiện tại. Thay bộ lọc hủy yêu cầu cũ; dữ liệu không trộn giữa các kỳ. Lỗi nguồn được hiển thị riêng, không thay bằng số 0.
- **Xuất báo cáo CSV** xuất số liệu thật và danh sách chuyến trong kỳ, kèm xí nghiệp, đơn vị, thời điểm nguồn, định nghĩa và lưu ý. Tên tàu/khách hàng được chống diễn giải thành công thức spreadsheet; không tự tải toàn bộ phiếu tác nghiệp để xuất.
- Mục **Nguồn & định nghĩa** phân biệt giờ tổng hợp báo cáo và giờ phát sinh tại nguồn. Chỉ tiêu hiệu suất/kho bãi giữ trạng thái chưa đủ cơ sở tính cho đến khi được xác nhận.

Phạm vi báo cáo dùng nhóm thống kê qua cảng tại nguồn theo định nghĩa API. Không diễn giải số chuyến có phát sinh thành số lượt tàu cập cảng, TEU tác nghiệp thành số container vật lý duy nhất, hoặc khối lượng ghi nhận thành khối lượng cân thực tế.

## Kiểm tra

```powershell
npm run lint
npm test
npm run build
```

Bộ kiểm tra logic bao gồm múi giờ, biên kỳ báo cáo, độ chính xác số lượng, mẫu số và điều chỉnh âm, phản hồi sai bộ lọc/cấu trúc, NULL và đơn vị nguồn, lỗi 503, tín hiệu hủy truy vấn và CSV an toàn. Kiểm tra trình duyệt toàn dự án nằm tại `../tests/browser_smoke.py`.
