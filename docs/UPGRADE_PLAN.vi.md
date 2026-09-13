# Nâng cấp dashboard sản lượng

Cập nhật 13/09/2026. Phạm vi: Cửa Lò và Bến Thủy. Người dùng đã chọn tài khoản nội bộ riêng và nhập kế hoạch từ Excel/văn bản được duyệt. Chức năng dưới đây đã được triển khai trong mã nguồn; bản nâng cấp đang chạy local, chưa phát hành lên Railway/Vercel.

## Chức năng đã triển khai

| Nhóm | Hành vi hiện tại | Kiểm tra |
|---|---|---|
| Điều hướng | Thanh đầu trang mở riêng Báo cáo sản lượng, Kế hoạch & đối soát (3 tab nghiệp vụ), Quản trị (2 tab tài khoản/vận hành, chỉ quản trị viên) | Chuyển khu vực theo hash, quay lại/tiến tới, bàn phím, quyền quản trị và hiển thị desktop/mobile |
| Ổn định lấy dữ liệu | Gom yêu cầu trùng đang chạy, cache ngắn có giới hạn; bản tra cứu lưu thêm trên ổ đĩa để phục hồi sau khi loại khỏi RAM hoặc khởi động lại | Đồng thời, timeout, lỗi, TTL, giới hạn RAM/ổ đĩa, phục hồi Decimal/ngày nguồn; thống kê yêu cầu/lỗi/thời gian |
| Tra cứu từ số tổng | Bấm KPI tấn/TEU, ngày, xí nghiệp, hàng, khách hàng để xem dòng tác nghiệp cùng report_id; chi tiết chuyến và Excel dùng cùng bản dòng nguồn | Tổng và danh sách, phạm vi xí nghiệp, dữ liệu đổi sau lần đọc, phân trang và Excel toàn bộ dòng |
| Kế hoạch | Nhập tay hoặc XLSX; tìm chuyến độc lập kỳ, kiểm tra đúng xí nghiệp; sửa/hủy nháp, lịch sử, duyệt theo văn bản và phiên bản | Xung đột chỉnh sửa/duyệt, bản đã duyệt bất biến, nhập sai chuyến, SQL lỗi không chặn kế hoạch tháng |
| Đối soát và chốt | Phân biệt dòng không ghi trọng lượng nhưng số lượng 0 với dòng có số lượng; chốt bản tổng, dòng nguồn và kế hoạch tháng tương ứng | Lưu kế hoạch, phiên bản, căn cứ và tỷ lệ trong cùng transaction chốt; bản cũ không lấy kế hoạch mới bù vào; Excel có kế hoạch đã chốt |
| Tiến độ chuyến | Tổng thực hiện toàn chuyến, kế hoạch, còn lại/phần trăm khi đủ dữ liệu, sản lượng theo ngày/ca, Excel chi tiết trong kỳ | Toàn chuyến đọc riêng và có thời điểm nguồn; không giả lập giờ làm hàng hoặc ETA |
| Truy cập nội bộ | Đăng nhập, đổi mật khẩu tạm, quyền xem/quản lý/quản trị và xí nghiệp; thu hồi phiên khi đổi quyền/mật khẩu | Bảo vệ cả endpoint cũ, drilldown, file tải và bản chốt; kiểm tra quyền trước khi đọc nguồn |
| Thao tác hằng ngày | Hôm nay/Hôm qua, nhớ bộ lọc; làm mới tùy chọn mỗi 2 phút khi xem kỳ hiện tại; trạng thái tuổi/độ đầy đủ dữ liệu; tải Excel có giới hạn đến hết file | Tạm dừng cập nhật khi nhập liệu, mở chi tiết, chuyển khu vực hoặc ẩn tab; giữ bản đang xem và báo lỗi khi làm mới thất bại |

## Cách tính và giới hạn nghiệp vụ

- Dòng nguồn là TallyShift thuộc hướng xếp/dỡ và nhóm thống kê SANLUONG-QUACANG, chưa bị xóa. Không diễn giải số dòng thành lượt xe hay phiếu cân.
- Tấn dùng khối lượng và đơn vị có cơ sở; giữ riêng đơn vị khác. NULL khác 0, điều chỉnh âm vẫn được giữ. Container 20/40/45 theo mã được nhận diện, không dùng riêng đơn vị CONT để suy ra kích thước.
- Chuyến có phát sinh là cặp xí nghiệp + chuyến tàu vật lý hợp lệ. Tổng qua cảng giữ dòng hợp lệ chưa gắn chuyến để tránh mất sản lượng.
- Kế hoạch tháng so với thực hiện từ ngày 1 đến ngày cuối đã chọn trong cùng tháng. Mẫu số là kế hoạch cả tháng; không chia đều theo ngày lịch. Kế hoạch chuyến so với tác nghiệp qua cảng toàn chuyến.
- Không tính tỷ lệ khi thiếu kế hoạch, kế hoạch 0 hoặc thực hiện chưa đầy đủ. Số kế hoạch chỉ xuất hiện sau khi nhập và duyệt tài liệu thực tế.
- Năng suất/giờ, dự kiến hoàn thành, trạng thái đang chờ/đang làm và sức chứa kho bãi cần dữ liệu được xác nhận. Chưa có ngày rời không chứng minh tàu đang làm hàng.
- report_id cố định tập dòng đã đọc tại tầng ứng dụng; không khẳng định snapshot giao dịch đồng thời giữa hai database SQL. Bản tra cứu ngắn hạn có thể hết hạn hoặc bị loại khi chạm giới hạn, còn bản đã chốt lưu bền vững.

## Vận hành bản nâng cấp

Tài khoản, phiên, kế hoạch, lịch sử đối soát và báo cáo chốt lưu trong control.sqlite3 riêng. Không tạo hoặc sửa bảng/view trong SQL Server nguồn. Bản tra cứu dùng lại 30 giây, hạn tra cứu 15 phút. RAM giữ tối đa 8 bản/100.000 dòng; report-cache.sqlite3 cùng thư mục state giữ tối đa 128 bản/1.000.000 dòng/128 MiB nội dung nén. Một báo cáo vẫn giới hạn 100.000 dòng. Chạm giới hạn ổ đĩa hoặc hết hạn vẫn cần tải lại. Triển khai tiếp tục dùng một instance, một worker; lưu trên ổ đĩa không tự giải quyết cân bằng tải nhiều replica hoặc transaction đồng thời hai nguồn SQL.

Đã bổ sung CLI sao lưu online/kiểm tra khôi phục độc lập và workflow CI kiểm thử backend, frontend, trình duyệt và Docker. Build image và bài kiểm tra container chạy với volume thử riêng; không đưa tài khoản hoặc kế hoạch thử vào dữ liệu thật. Việc bật sao lưu định kỳ và xác minh volume Railway production cần phiên đăng nhập của quản trị.

Hướng dẫn local nằm trong [README](../README.md), luồng sử dụng trong [hướng dẫn frontend](../frontend/README.md). Khi phát hành cloud, cần [volume Railway, entrypoint, bootstrap quản trị và sao lưu](DEPLOYMENT.vi.md). Kiểm tra đăng nhập, quyền xí nghiệp, dữ liệu SQL thực và khả năng giữ tài khoản/kế hoạch sau khởi động lại.

## Dữ liệu cần đưa vào hệ thống

| Nội dung | Nguồn phụ trách | Cách dùng |
|---|---|---|
| Kế hoạch tháng và chuyến | Phòng Kế hoạch/Khai thác, Excel hoặc văn bản đã duyệt | Nhập dashboard, đối chiếu số văn bản, duyệt phiên bản đúng xí nghiệp/chỉ tiêu |
| Ngoại lệ phiếu và đơn vị | Khai thác/DBA | Ghi nhận đối soát; chỉnh nguồn theo quy trình công ty, đọc lại và so với bản chốt |
| Giờ làm hàng/dừng, máng/thiết bị, mốc cầu bến | Điều độ/Khai thác | Xác nhận ca qua đêm, chồng thời gian và giờ dừng trước khi bổ sung năng suất/ETA |
| Tồn kho và sức chứa hữu dụng | Kho bãi | Xác nhận tồn đầu, nhập/xuất/điều chỉnh và đơn vị trước khi tính mức lấp đầy |
| Phê duyệt định nghĩa KPI | Người lập báo cáo và Khai thác | Đối chiếu kỳ thường, cuối tháng, ca qua đêm; ghi chênh lệch theo chứng từ |

Các mục cuối là điều kiện dữ liệu cho chỉ tiêu bổ sung; ứng dụng không thay bằng số ước tính. Hồ sơ đọc nguồn và sản lượng thực được giữ local, loại khỏi Git. Bộ kiểm thử trong repository dùng dữ liệu tổng hợp, không chứa tài khoản hoặc dữ liệu sản xuất thật.
