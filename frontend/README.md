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

## Điều hướng

Thanh điều hướng đầu trang mở ba khu vực riêng:

- **Báo cáo sản lượng** (`#overview`): bộ lọc kỳ/xí nghiệp, KPI, biểu đồ và tra cứu tác nghiệp/chuyến tàu.
- **Kế hoạch & đối soát** (`#management`): ba tab **Kế hoạch**, **Đối soát**, **Báo cáo đã chốt**. Người xem đọc dữ liệu; người quản lý và quản trị viên thao tác theo phạm vi được cấp.
- **Quản trị** (`#admin`): hai tab **Tài khoản**, **Vận hành**, chỉ dành cho quản trị viên.

Mỗi lựa chọn thay nội dung chính của trang. Có thể dùng nút quay lại/tiến tới của trình duyệt để chuyển khu vực. Bộ lọc báo cáo tiếp tục xác định kỳ và xí nghiệp khi đối soát hoặc chốt báo cáo.

## Sử dụng báo cáo

- Đăng nhập tài khoản nội bộ; mật khẩu tạm phải được đổi trước khi đọc báo cáo. Phiên lưu theo tab trình duyệt và hết hạn sau 8 giờ. Có nút đổi mật khẩu/đăng xuất ở đầu trang; tài khoản chỉ thấy xí nghiệp được cấp.
- Chọn hôm nay, hôm qua, **tuần này**, **tuần trước**, tháng này, tháng trước, từ đầu năm; hoặc nhập khoảng ngày và chọn xí nghiệp rồi bấm **Áp dụng**. Khoảng ngày bao gồm cả hai đầu, tối đa 366 ngày, theo giờ Việt Nam. Bộ lọc được nhớ riêng theo tài khoản trên trình duyệt, kiểm tra lại theo quyền hiện tại khi mở.
- **Lọc theo tuần:** tuần bắt đầu thứ Hai, kết thúc Chủ nhật. “Tuần này” tính đến hôm nay; “Tuần trước” lấy đủ 7 ngày. Để xem một tuần khác, dùng **Chọn tuần → Xem tuần**; khoảng ngày được hiển thị cạnh ô chọn trước khi áp dụng. Tuần được đánh số theo ISO, nên tuần 1 có thể bắt đầu cuối năm trước. Không chọn được tuần chưa bắt đầu; xí nghiệp và phạm vi Cảng Nghệ Tĩnh/Cầu 5 đang chọn được giữ nguyên.
- Bấm số tấn/TEU, cột ngày hoặc ngày trong bảng, tên xí nghiệp, nhóm hàng và khách hàng để mở các dòng tác nghiệp cùng bản dữ liệu. Có phân trang, lọc dòng và xuất Excel toàn bộ kết quả đã lọc; không giới hạn file xuất vào trang đang xem. Nếu bản dữ liệu hết hạn, chọn **Tải lại báo cáo**.
- Đọc **sản lượng qua cảng (tấn)**, TEU và số chuyến có phát sinh. Tấn chỉ cộng khối lượng có đơn vị nguồn xác định; các đơn vị khác hiển thị riêng tại **Sản lượng chưa cộng vào tấn**.
- Khối lượng và số lượng giữ tối đa 3 chữ số thập phân; số chuyến hiển thị số nguyên. Giá trị chưa đủ cơ sở tính hiển thị `—`. Các lưu ý và độ đầy đủ được gom trong mục **Nguồn dữ liệu & định nghĩa**, đóng mặc định.
- Biểu đồ chuyển được ngày/tháng và tấn/TEU. Kỳ tối đa 62 ngày mặc định hiển thị theo ngày. Ngày không có bản ghi theo bộ lọc hiển thị 0, không tự kết luận ngừng sản xuất. Bảng dưới biểu đồ cung cấp số liệu tương đương để đọc bằng bàn phím.
- Tỷ lệ thay đổi dùng khoảng liền trước có cùng số ngày, chỉ hiển thị khi đủ cơ sở so sánh. Tỷ trọng và thanh cơ cấu không hiển thị khi có dữ liệu điều chỉnh âm hoặc tổng chưa đủ cơ sở; số lượng có dấu vẫn được giữ nguyên.
- Mục **Chuyến tàu** hiển thị danh sách cùng phạm vi với KPI; tìm bằng tên tàu hoặc mã chuyến, có thể nhập tên không dấu. **Xem chi tiết** mở thông tin đến/rời thực tế nếu có, hàng hóa/ngày làm hàng và phiếu tác nghiệp theo từng trang 25 dòng. Dùng nút đóng hoặc phím Esc để quay về danh sách.
- Bảng **Tác nghiệp qua cảng trong kỳ** mặc định hiện **Có phát sinh**: số lượng hoặc trọng lượng nguồn khác 0. Chọn **Tất cả** để xem cả dòng 0/chưa có trọng lượng, hoặc **Thiếu trọng lượng** để đối chiếu dữ liệu `NULL`. Đổi bộ lọc đưa bảng về trang 1; tổng sản lượng và biểu đồ vẫn tính trên toàn bộ dòng nguồn đủ điều kiện. Thẻ **Dòng có phát sinh** và số dòng nguồn giúp phân biệt hai cách đếm. Dòng tác nghiệp có mã, ngày nghiệp vụ và ca; không diễn giải số dòng thành số lượt xe hoặc phiếu cân.
- **Sản lượng theo ngày** trong chi tiết chuyến bắt đầu từ ngày vào cảng, kết thúc ở ngày rời cảng hoặc cuối kỳ nếu chưa có ngày rời, trong phạm vi bộ lọc. Thiếu ngày vào thì bắt đầu ở ngày tác nghiệp đầu tiên. Nếu ngày vào/rời không khớp ngày phiếu, các ngày có bản ghi vẫn được giữ để tổng không bị thiếu. Lịch ngày của báo cáo tổng quan vẫn bao gồm toàn bộ kỳ.
- **Tải lại** truy vấn lại kỳ hiện tại. Thay bộ lọc hủy yêu cầu cũ; dữ liệu không trộn giữa các kỳ. Lỗi nguồn được hiển thị riêng, không thay bằng số 0.
- **Tự cập nhật mỗi 2 phút** mặc định tắt, được nhớ riêng theo tài khoản. Chỉ chạy khi kỳ kết thúc hôm nay; tạm dừng khi tab trình duyệt ẩn, đang nhập/chọn dữ liệu, có bộ lọc chưa áp dụng, đang mở chi tiết hoặc đang ở Kế hoạch/Quản trị. Khi cập nhật lỗi, giữ bản dữ liệu cùng kỳ với cảnh báo **Chưa cập nhật được**; chốt báo cáo cần cập nhật thành công trước. Dữ liệu nguồn hơn 3 phút có nhãn thời gian riêng.
- Nhãn **Số liệu chưa đầy đủ** cạnh thời điểm đọc nguồn dẫn tới phần định nghĩa. Chi tiết phân biệt trọng lượng chưa ghi với số lượng bằng 0, khác 0 hoặc cũng chưa ghi; không tự đổi NULL thành 0.
- Dashboard và chi tiết chuyến tự thử lại một lần sau 500 ms khi lỗi mạng hoặc HTTP 502/503/504; tổng thời gian vẫn giới hạn 45 giây. Thay bộ lọc, đổi trang hoặc đóng chi tiết hủy cả lần thử lại. Lỗi JSON/cấu trúc và lỗi bộ lọc không được tự thử lại.
- **Xuất báo cáo CSV** xuất số liệu thật và danh sách chuyến trong kỳ, kèm xí nghiệp, đơn vị, thời điểm nguồn, định nghĩa và lưu ý. Tên tàu/khách hàng được chống diễn giải thành công thức spreadsheet; không tự tải toàn bộ phiếu tác nghiệp để xuất.
- **Xuất Excel** chỉ tạo file khi tải xong toàn bộ nội dung, giới hạn 45 giây cả phần tải file. Nút xuất chi tiết bị khóa khi đang tải; đóng chi tiết sẽ hủy tải, tránh tạo file sau khi đã rời cửa sổ.
- Mục **Nguồn & định nghĩa** phân biệt giờ tổng hợp báo cáo và giờ phát sinh tại nguồn. Chỉ tiêu hiệu suất/kho bãi giữ trạng thái chưa đủ cơ sở tính cho đến khi được xác nhận.

## Kế hoạch & đối soát

- **Kế hoạch:** nhập tay hoặc tải mẫu Excel, điền số liệu từ văn bản đã duyệt, xem trước rồi lưu nháp. Duyệt phiên bản sau khi kiểm tra số văn bản và phạm vi. Hỗ trợ tuần, tháng, quý, năm, khoảng ngày hoặc chuyến, chỉ tiêu tấn/TEU riêng; không có số kế hoạch giả lập trong ứng dụng.
- **Theo tuần:** chọn tuần ISO `YYYY-Www` trong biểu mẫu hoặc bộ lọc danh sách, kiểm tra khoảng thứ Hai–Chủ nhật trước khi lưu. Cho phép lập tuần tương lai; tuần hiện tại đối chiếu thực tế từ thứ Hai đến hôm nay với chỉ tiêu đủ 7 ngày. Tuần giao năm dùng năm ISO, có thể chứa ngày thuộc năm khác. Excel có cột **Tuần** cuối mẫu, dùng `period_type=week`; mẫu 8/12 cột cũ vẫn nhập được cho loại kỳ cũ.
- **Sửa nháp / Hủy nháp:** bản nháp được sửa trước khi duyệt hoặc hủy kèm lý do. Bản đã duyệt giữ nguyên; cần thay đổi thì tạo phiên bản mới. Nếu người khác đã sửa bản nháp, tải lại danh sách và kiểm tra lại trước khi tiếp tục. **Lịch sử** hiển thị người lập/duyệt theo ID tài khoản, thời gian, giá trị và văn bản từng lần thay đổi.
- Kế hoạch chuyến dùng danh mục chuyến tại xí nghiệp độc lập kỳ báo cáo. Chọn xí nghiệp, nhập tên tàu hoặc mã, bấm **Tìm chuyến**, đối chiếu mã và ngày vào rồi chọn. Chuyến chưa có sản lượng trong kỳ vẫn có thể lập kế hoạch nếu đã có trong nguồn.
- **Thực hiện so với kế hoạch tháng:** dùng kỳ từ ngày 1 đến một ngày trong cùng tháng, so với kế hoạch cả tháng; không chia kế hoạch tuyến tính theo số ngày. Thiếu kế hoạch, kế hoạch 0 hoặc thực hiện thiếu dữ liệu hiển thị trạng thái phù hợp.
- **Tiến độ toàn chuyến:** trong chi tiết tàu có kế hoạch, tổng đã ghi nhận toàn chuyến, còn lại, phần trăm và sản lượng theo ca. Tổng này có thể khác tổng trong kỳ ở phía trên; có thời điểm đọc riêng. Năng suất và dự kiến hoàn thành chưa được tính khi thiếu giờ làm hàng/dừng được xác nhận.
- **Đối soát:** lọc dòng thiếu số liệu, đơn vị chưa xác định hoặc điều chỉnh âm; ghi chú và cập nhật trạng thái xử lý, xem lịch sử. Ghi chú không thay đổi nguồn TOS.
- **Báo cáo đã chốt:** lưu phiên bản của báo cáo đang xem, xuất Excel và so sánh với bản đang xem cùng kỳ/xí nghiệp. Bản chốt giữ nguyên khi nguồn thay đổi.
- **Kế hoạch đã chốt** mở kế hoạch, phiên bản, văn bản và tỷ lệ hoàn thành được lưu cùng lần chốt. Các bản chốt cũ chưa lưu kế hoạch sẽ ghi rõ điều này; không dùng kế hoạch hiện tại để thay thế lịch sử.

## Quản trị

- **Tài khoản:** quản trị viên tạo tài khoản, cấp quyền/xí nghiệp, khóa hoặc cấp mật khẩu tạm mới. Quyền thay đổi làm hết hiệu lực các phiên cũ. Người xem không có nút ghi dữ liệu; API cũng kiểm tra quyền.
- **Vận hành:** quản trị viên xem số lượt yêu cầu/lỗi, thời gian đọc và thống kê cache. Quản lý tài khoản, kế hoạch và báo cáo chốt vẫn truy cập được khi nguồn SQL đang lỗi.

Phạm vi báo cáo dùng nhóm thống kê qua cảng tại nguồn theo định nghĩa API. Không diễn giải số chuyến có phát sinh thành số lượt tàu cập cảng, TEU tác nghiệp thành số container vật lý duy nhất, hoặc khối lượng ghi nhận thành khối lượng cân thực tế.

## Kiểm tra

```powershell
npm run lint
npm test
npm run build
```

Bộ kiểm tra logic bao gồm múi giờ, biên kỳ báo cáo, độ chính xác số lượng, mẫu số và điều chỉnh âm, phản hồi sai bộ lọc/cấu trúc, NULL và đơn vị nguồn, lỗi 503, tín hiệu hủy truy vấn và CSV an toàn. Kiểm tra trình duyệt toàn dự án nằm tại `../tests/browser_smoke.py`.

`../tests/browser_refresh_download.py` kiểm tra tải file bị treo sau khi nhận header, hủy khi đóng cửa sổ, chống bấm xuất lặp, giữ snapshot khi cập nhật lỗi và các điều kiện tạm dừng tự cập nhật. `../tests/browser_management.py` kiểm tra sửa/hủy nháp có revision, danh mục chuyến ngoài kỳ và kế hoạch đã chốt. Cả hai chỉ dùng API giả lập, không ghi nguồn thật.

Kiểm tra phục hồi sau lỗi tạm thời, dừng sau hai lần, thử lại thủ công và hủy khi đóng chi tiết nằm tại `../tests/browser_availability.py`. Chạy từ thư mục gốc với Vite đang hoạt động: `.venv-audit\Scripts\python.exe tests/browser_availability.py --url http://127.0.0.1:5173`. Bài kiểm tra chặn yêu cầu trong trình duyệt và chỉ dùng dữ liệu giả lập được ghi nhãn; không truy vấn hoặc sửa database.
