# Sử dụng kế hoạch sản lượng

Trong **Báo cáo sản lượng**, chọn **Năm xem quý**, sau đó bấm **Quý 1**, **Quý 2**, **Quý 3** hoặc **Quý 4**. Quý đã kết thúc lấy đủ ba tháng; quý đang diễn ra lấy từ đầu quý đến hôm nay. Quý chưa bắt đầu chưa có số liệu thực tế để xem. Muốn xem quý của năm trước, đổi năm trước khi chọn quý.

## Nhập chỉ tiêu

Tài khoản có quyền **Quản lý** hoặc **Quản trị** thực hiện:

1. Mở **Kế hoạch & đối soát → Kế hoạch**, bấm **Tạo kế hoạch**; hoặc bấm **Nhập kế hoạch** trên thanh tiến độ.
2. Chọn kỳ **tháng / quý / năm / tùy chọn**; nhập kỳ tương ứng. Khoảng ngày tùy chọn tối đa 366 ngày. Có thể lập kế hoạch cho kỳ tương lai.
3. Chọn **Toàn công ty** hoặc xí nghiệp. Chọn **Sản lượng thông qua (tấn)**, nhập chỉ tiêu và văn bản hoặc nguồn giao kế hoạch. Nhập số theo cách viết Việt Nam: `150.000` là 150 nghìn tấn; `150.000,5` là 150 nghìn phẩy 5 tấn. Cũng có thể nhập `150000`. Kiểm tra giá trị diễn giải ngay dưới ô nhập; tối đa 6 chữ số thập phân.
4. Lưu bản nháp, kiểm tra lại và bấm **Duyệt**. Bản nháp chưa dùng để tính tiến độ.
5. Bấm **Xem tiến độ** sau khi duyệt hoặc tại dòng kế hoạch đã duyệt đang có hiệu lực. Dashboard tự mở đúng phạm vi, từ ngày đầu kỳ đến hôm nay hoặc ngày cuối kỳ nếu kỳ đã kết thúc. Kế hoạch tương lai vẫn lưu và duyệt được; chưa xem tiến độ trước ngày bắt đầu.

Trên thanh tiến độ, **Kế hoạch đối chiếu** liệt kê các kỳ đã duyệt trong phạm vi đang xem. Chọn kế hoạch năm khi đang xem tháng sẽ mở báo cáo từ đầu năm, không lấy sản lượng riêng của tháng chia cho chỉ tiêu cả năm. Kế hoạch chưa đủ hai xí nghiệp khi xem toàn công ty sẽ báo thiếu chỉ tiêu, không hiển thị tỷ lệ giả.

Nếu lưu bị lỗi, đọc thông báo ngay cạnh ô cần sửa. Nếu mất kết nối khi lưu hoặc duyệt, tải lại danh sách để kiểm tra trạng thái trước khi thử lại, tránh nhập trùng. Số văn bản/nguồn phê duyệt là bắt buộc.

Khi mở tiến độ cả năm hoặc khoảng ngày dài hơn 92 ngày, tổng hợp dữ liệu có thể cần khoảng một phút. Trang chờ tối đa 90 giây và hiển thị trạng thái đang tải. Nếu nguồn SQL báo lỗi, bấm thử lại; không cần nhập hoặc duyệt lại kế hoạch.

Ví dụ: lập kế hoạch **Quý 3/2026 = 500.000 tấn**, chọn báo cáo **Quý 3/2026**. Ngày 17/09, thanh tiến độ lấy sản lượng từ 01/07 đến 17/09 chia cho 500.000 tấn của cả quý. Chỉ tiêu không tự chia nhỏ theo số ngày đã qua.

## Đọc thanh tiến độ

Tỷ lệ hoàn thành = **tấn thông qua thực tế của kỳ báo cáo / chỉ tiêu kế hoạch × 100%**.

| Tỷ lệ | Màu |
|---|---|
| Dưới 20% | Đỏ |
| Từ 20% đến dưới 40% | Cam |
| Từ 40% đến dưới 60% | Vàng |
| Từ 60% đến dưới 80% | Xanh nhạt |
| Từ 80% trở lên | Xanh đậm |

Từ 100% hiển thị **Đạt kế hoạch** khi dữ liệu đủ điều kiện. Nếu vượt chỉ tiêu, phần trăm vẫn hiển thị trên 100% và có số tấn vượt; thanh được tô tối đa 100%. Các màu thể hiện tỷ lệ hoàn thành, không phải đánh giá nhanh/chậm theo thời gian của kỳ.

Nếu nguồn còn thiếu số liệu, tỷ lệ được ghi **Tạm tính** và chưa xác nhận đạt kế hoạch. Thiếu chỉ tiêu, chỉ tiêu bằng 0, sản lượng âm hoặc chưa có giá trị thực tế sẽ hiện trạng thái tương ứng thay vì tỷ lệ giả.

## Phạm vi và phiên bản

- Kế hoạch công ty áp dụng cho phần **Cảng Nghệ Tĩnh**, đã tách các chuyến cập đầu tiên vào **Cầu 5** của Vietsun.
- Khi xem toàn công ty, kế hoạch toàn công ty được ưu tiên. Nếu chưa có, chỉ cộng kế hoạch của hai xí nghiệp khi đủ cả hai và cùng loại kỳ, cùng thời gian. Không cộng thêm kế hoạch xí nghiệp vào kế hoạch toàn công ty.
- Không cộng chồng kế hoạch tháng, quý và năm. Mỗi lần đối chiếu dùng một kỳ kế hoạch được chọn.
- Thay đổi chỉ tiêu đã duyệt bằng nút **Tạo phiên bản mới**, sửa chỉ tiêu/căn cứ rồi lưu nháp và duyệt. Phiên bản cũ vẫn có hiệu lực cho đến khi bản mới được duyệt; lịch sử vẫn được giữ. Báo cáo đã chốt giữ các chỉ tiêu tại thời điểm chốt.
- Local dùng API Railway thì các kế hoạch nhập tại local cũng được lưu trên server Railway.

**Chưa xác định cầu** là dữ liệu nguồn chưa đủ để xác định cầu cập đầu tiên của chuyến: thiếu mã chuyến, thiếu lịch sử cầu hoặc có thông tin đầu tiên không thống nhất. Đây là phần dành cho đối soát, không phải một cầu riêng; chưa được tự cộng vào Cảng Nghệ Tĩnh hay Cầu 5.

## Xóa kế hoạch

Tài khoản **Quản lý** hoặc **Quản trị** được xóa kế hoạch trong phạm vi xí nghiệp được cấp, gồm bản nháp, bản đã duyệt và bản đã hủy. Kế hoạch **Toàn công ty** yêu cầu quyền với cả hai xí nghiệp. Tài khoản chỉ xem không được xóa.

1. Trong danh sách kế hoạch, tìm đúng kỳ, xí nghiệp, chỉ tiêu và phiên bản cần bỏ.
2. Bấm **Xóa**, kiểm tra lại thông tin trong hộp **Xóa kế hoạch?**, rồi chọn **Xóa kế hoạch** để xác nhận hoặc **Giữ kế hoạch** để bỏ qua. Kế hoạch được ẩn khỏi danh sách mặc định và không còn dùng cho tiến độ hoặc báo cáo mới.
3. Bật **Hiện kế hoạch đã xóa** khi cần tra cứu nội dung và **Lịch sử**. Hệ thống lưu người xóa, thời điểm xóa và phiên bản; không có thao tác khôi phục hoặc xóa vĩnh viễn trong giao diện.

Xóa chỉ đánh dấu bản kế hoạch đã chọn, không xóa dữ liệu sản lượng TOS. Lịch sử tạo/sửa/duyệt/hủy được giữ nguyên. Báo cáo đã chốt và Excel xuất từ bản chốt vẫn giữ đúng chỉ tiêu tại thời điểm chốt, kể cả khi kế hoạch gốc bị xóa sau đó. Việc xóa không cần kết nối nguồn SQL sản xuất.

Nếu xóa phiên bản đã duyệt mới nhất, hệ thống **không tự dùng lại phiên bản đã duyệt cũ**. Cần tạo và duyệt phiên bản mới để áp dụng lại chỉ tiêu. Xóa bản nháp hoặc một phiên bản cũ không thay đổi bản đã duyệt khác đang có hiệu lực.

Nếu xóa kế hoạch **Toàn công ty** đang có hiệu lực, tiến độ toàn công ty **không tự chuyển sang cộng chỉ tiêu hai xí nghiệp** cho cùng loại kỳ, khoảng thời gian và chỉ tiêu. Cần duyệt một phiên bản Toàn công ty mới; các kế hoạch riêng của từng xí nghiệp vẫn dùng được khi xem chính xí nghiệp đó.

Nếu kế hoạch đã thay đổi hoặc đã bị người khác xóa trước khi bạn xác nhận, hệ thống từ chối yêu cầu và thông báo tải lại. Khi gặp lỗi mạng, tải lại danh sách, bật hiển thị kế hoạch đã xóa để kiểm tra trước khi thử tiếp; không cần tạo lại hoặc duyệt lại bản khác chỉ để xử lý lỗi kết nối.
