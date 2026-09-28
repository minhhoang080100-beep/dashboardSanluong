# Kế hoạch phân quyền dashboard sản lượng

Ngày lập: 17/09/2026. Cơ sở rà soát: mã nguồn tại commit `7605a58`.

Đây là phương án đề xuất, chưa áp dụng thay đổi quyền cho tài khoản hoặc dữ liệu đang sử dụng. Phạm vi kiểm tra lần này là mã nguồn; chưa kiểm kê tài khoản và phân công nhân sự trên production.

## 1. Hiện trạng đã kiểm tra

| Nội dung | Hiện tại | Cơ sở mã nguồn |
|---|---|---|
| Vai trò | `viewer`, `manager`, `admin` | `backend/control_store.py`, `ROLES` |
| Nghiệp vụ | `manager` và `admin` cùng có quyền tạo, sửa, duyệt, xóa kế hoạch, xử lý đối soát và chốt báo cáo | `require_editor`, các hàm thay đổi kế hoạch và `close_report` |
| Tự duyệt | Chưa chặn người lập hoặc sửa kế hoạch tự duyệt phiên bản đó | `ControlStore.approve_plan` |
| Phạm vi dữ liệu | Có kiểm tra Cửa Lò/Bến Thủy tại backend; chưa có quyền truy cập riêng cho Cảng Nghệ Tĩnh/Cầu 5/Chưa xác định cầu | `require_scope`, `backend/main.py`, `backend/integration.py` |
| Phiên đăng nhập | Đổi vai trò, xí nghiệp, trạng thái hoặc đặt lại mật khẩu có thu hồi phiên | `ControlStore.update_user`, `reset_password` |
| Lịch sử | Có lịch sử kế hoạch và đối soát; bản chốt bất biến. Chưa có nhật ký đầy đủ cho thay đổi quyền tài khoản | `plan_events`, `issue_events`, `update_user` |
| Quản trị theo xí nghiệp | Giới hạn khi cấp/sửa quyền; danh sách tài khoản hiện vẫn trả toàn bộ tài khoản cho admin | `create_user`, `update_user`, `list_users` |

Các cơ chế hiện có về thu hồi phiên, lịch sử, phiên bản, kiểm tra quyền trong transaction và bảo vệ quản trị viên cuối cùng cần được giữ lại.

## 2. Mô hình đề xuất

Quyền sử dụng gồm ba phần: **thao tác được làm + phạm vi dữ liệu + điều kiện của bản ghi**. Chức danh không tự động đồng nghĩa với quyền quản trị.

Khởi đầu bằng bốn nhóm quyền có sẵn; một người có thể được giao nhiều nhóm khi công việc yêu cầu. Giao diện quản trị dùng các nhóm này, không bắt người cấp tài khoản cấu hình hàng chục quyền nhỏ.

| Nhóm quyền | Xem dữ liệu | Kế hoạch | Đối soát và chốt | Tài khoản |
|---|---|---|---|---|
| Xem báo cáo | Báo cáo, tiến độ, kế hoạch đã duyệt trong phạm vi được cấp | Không thay đổi | Xem kết quả và báo cáo đã chốt | Đổi mật khẩu của mình |
| Lập kế hoạch / thống kê | Báo cáo và các kế hoạch được giao | Tạo, nhập Excel, sửa nháp được giao, gửi duyệt, rút bản đang chờ duyệt | Ghi nhận vấn đề, giải trình, đề nghị hoàn tất đối soát | Đổi mật khẩu của mình |
| Duyệt kế hoạch / chốt báo cáo | Báo cáo và hồ sơ cần duyệt trong phạm vi được cấp | Duyệt, trả lại; hủy hiệu lực hoặc xóa mềm theo quyền riêng | Xác nhận đối soát, chốt báo cáo theo thẩm quyền | Đổi mật khẩu của mình |
| Quản trị hệ thống | Tài khoản, nhật ký quản trị, tình trạng vận hành | Không mặc định có quyền nghiệp vụ | Không mặc định có quyền nghiệp vụ | Tạo, khóa, đặt lại mật khẩu và cấp quyền trong phạm vi quản trị |

Ví dụ phân công: lãnh đạo xem toàn công ty và nhận thêm quyền duyệt nếu được giao; quản lý xí nghiệp duyệt phạm vi xí nghiệp; nhân viên thống kê lập kế hoạch; CNTT quản trị hệ thống. Quyền lập và duyệt có thể cùng nằm trên một tài khoản, nhưng tài khoản đó vẫn không được tự duyệt phiên bản mình tham gia lập/sửa.

Tách quyền xuất báo cáo tổng hợp và xuất chi tiết. Mặc định người xem được xuất tổng hợp; quyền tải phiếu tác nghiệp/chi tiết khách hàng được cấp theo nhu cầu. Chặn tải file không thể ngăn người dùng sao chép dữ liệu đã được phép xem; mục tiêu là kiểm soát đúng dữ liệu trả về và ghi nhận thao tác xuất.

CSV hiện được tạo trong trình duyệt từ dữ liệu đã tải (`Dashboard.jsx`, `exportCsv`). Nếu áp dụng quyền xuất riêng và cần nhật ký tải file đáng tin cậy, chuyển chức năng xuất CSV chính thức sang endpoint có kiểm tra quyền; Excel cũng kiểm tra quyền xuất và phạm vi tại backend.

## 3. Phạm vi dữ liệu

- **Xí nghiệp:** Cửa Lò, Bến Thủy hoặc cả hai. Có quyền xem cả hai không tự động được duyệt kế hoạch toàn công ty; quyền này phải được giao riêng.
- **Phạm vi sản lượng:** Cảng Nghệ Tĩnh, Cầu 5, Chưa xác định cầu. Cầu 5 và dữ liệu chưa xác định cầu là các lựa chọn cấp quyền riêng; không thay đổi quy tắc phân loại theo cầu cập đầu tiên.
- **Phạm vi xem và phạm vi duyệt có thể khác nhau:** ví dụ xem toàn công ty nhưng chỉ duyệt kế hoạch Cửa Lò.
- Mỗi quyền phải gắn với đúng cặp **xí nghiệp × phạm vi sản lượng**. Không gộp riêng các vai trò rồi gộp các cảng: xem Bến Thủy và duyệt Cửa Lò không được biến thành quyền duyệt cả hai. Báo cáo toàn công ty của một phạm vi sản lượng cần đủ quyền trên cả hai xí nghiệp cho chính phạm vi đó.
- **Bản nháp:** người lập và người được giao xử lý/duyệt được xem; nhóm chỉ xem báo cáo mặc định thấy các bản đã duyệt. Lịch sử phải tuân theo cùng điều kiện, tránh lộ nội dung nháp qua lịch sử phiên bản.
- **Kế hoạch Cảng Nghệ Tĩnh:** giữ nguyên đối chiếu với tấn thông qua của Cảng Nghệ Tĩnh. Cấp quyền xem Cầu 5 không làm cộng sản lượng Cầu 5 vào kế hoạch này.
- **Dữ liệu cũ:** báo cáo đã chốt chưa xác định được phạm vi sản lượng cần được đánh dấu và giới hạn cho người được cấp quyền kiểm tra; không mặc nhiên coi là Cảng Nghệ Tĩnh.
- Tất cả tổng số, danh sách, tìm chuyến, khách hàng, chi tiết, kế hoạch, lịch sử, file xuất và báo cáo đã chốt đều dùng cùng kiểm tra phạm vi. Không chỉ ẩn nút hoặc tab trên giao diện.
- Lập kế hoạch cho chuyến chưa cập bến được tìm định danh tối thiểu trong xí nghiệp được cấp, dù chưa có cầu đầu tiên. Quyền này không mở quyền xem dữ liệu sản lượng chưa phân loại; bản kế hoạch phải thể hiện phạm vi chưa được xác nhận để kiểm tra lại khi có dữ liệu cầu.
- “Tất cả kế hoạch” nghĩa là tất cả kế hoạch người dùng được phép xem. Báo cáo “Toàn công ty” chỉ hiển thị khi có đủ quyền; không âm thầm lấy một xí nghiệp rồi đặt nhãn toàn công ty.

Nếu cần tách cả quyền xem tổng hợp và xem chi tiết, phải lọc payload ở backend: dashboard hiện có khách hàng/chuyến tàu, và bản chốt chứa dòng nguồn. Không trả các trường đó rồi chỉ ẩn giao diện. Giai đoạn đầu chưa đề xuất hạn chế theo ngày lịch sử để tránh làm sai kỳ so sánh và tiến độ toàn chuyến.

## 4. Quy trình kế hoạch và chốt báo cáo

Luồng đề xuất: **Nháp → Chờ duyệt → Đã duyệt**, hoặc **Trả lại → Sửa → Gửi duyệt lại**.

1. Nhân viên nhập chỉ tiêu, kỳ, phạm vi, số văn bản và ghi chú. Người được giao có thể sửa bản nháp; người khác cần được chuyển giao rõ ràng.
2. Gửi duyệt sẽ khóa nội dung phiên bản. Mọi thay đổi số lượng/kỳ/phạm vi/căn cứ đều phải rút về nháp hoặc được trả lại trước khi sửa.
3. Người duyệt kiểm tra căn cứ và chỉ tiêu. Backend chặn tự duyệt đối với người tạo hoặc từng sửa nội dung phiên bản đó, kể cả khi người này đồng thời là admin hoặc được giao nhiều vai trò.
4. Kế hoạch toàn công ty cần quyền duyệt toàn công ty. Kế hoạch xí nghiệp chỉ cần người duyệt đúng xí nghiệp; chưa đặt thêm nhiều tầng duyệt mặc định.
5. Bản đã duyệt được dùng cho tiến độ; thay đổi phải tạo phiên bản mới và duyệt lại. Bản chốt cũ giữ nguyên nội dung tại lúc chốt.
6. Người lập chỉ hủy/xóa mềm bản nháp được giao. Bản đã duyệt cần người có thẩm quyền riêng, lý do và lịch sử. Thao tác phải cho biết ảnh hưởng tới tiến độ; không tự khôi phục bản duyệt cũ sau khi xóa bản mới.
7. Nhân viên thống kê ghi nhận và đề xuất xử lý đối soát; người có thẩm quyền xác nhận hoàn tất hoặc bỏ qua với lý do. Quyền chốt báo cáo được cấp riêng với quyền duyệt kế hoạch.

Khi bổ sung hủy hiệu lực, không chỉ đổi trạng thái bản đã duyệt thành `cancelled`: truy vấn hiện tại có thể chọn lại bản duyệt cũ. Cần giữ dấu vết phiên bản thay thế/hủy, bao gồm trường hợp kế hoạch công ty bị hủy không tự chuyển sang cộng kế hoạch hai xí nghiệp.

Dashboard tiếp tục đọc dữ liệu sản lượng từ SQL Server. Phân quyền mới không cấp khả năng sửa phiếu tác nghiệp hoặc sản lượng gốc qua dashboard.

## 5. Quản trị tài khoản và nhật ký

- Mỗi người có tài khoản riêng. Tài khoản mới/reset phải đổi mật khẩu tạm; khóa tài khoản và đổi quyền tiếp tục thu hồi các phiên cũ.
- Màn hình cấp quyền hiển thị nhóm quyền, phạm vi xem, phạm vi thao tác/duyệt và thời hạn nếu được ủy quyền tạm thời; có bản tóm tắt trước khi lưu.
- Admin xí nghiệp chỉ thấy và quản lý tài khoản thuộc phạm vi được giao. Tài khoản bao phủ cả hai xí nghiệp do quản trị toàn công ty quản lý.
- Không tự cấp thêm quyền đặc biệt cho chính mình. Việc cấp quyền duyệt toàn công ty, xóa bản đã duyệt hoặc quyền quản trị cần người có thẩm quyền khác xác nhận. Duy trì quy trình khôi phục quản trị được ghi nhận, tránh khóa toàn bộ người quản trị.
- Ghi nhật ký tạo/khóa tài khoản, cấp/thu hồi quyền, đặt lại mật khẩu, duyệt/hủy/xóa kế hoạch, chốt báo cáo và xuất chi tiết. Lưu người thực hiện, thời gian, đối tượng, thay đổi trước/sau và lý do; không lưu mật khẩu hoặc token vào nhật ký.
- Giới hạn người được xem nhật ký; không cho sửa/xóa nhật ký qua API thông thường. Rà soát lại quyền theo quý và khi nhân sự thay đổi công việc.

## 6. Các bước triển khai và nghiệm thu

| Bước | Công việc | Điều kiện hoàn thành |
|---|---|---|
| 1. Chốt phân công | Lập danh sách người dùng, nhóm quyền, phạm vi xem/duyệt, người duyệt toàn công ty và người quản trị dự phòng | Mỗi người có một cấu hình cụ thể; mọi phạm vi có người lập và người duyệt độc lập |
| 2. Bảo vệ backend | Xây dựng kiểm tra quyền dùng chung; bổ sung phạm vi sản lượng và điều kiện bản ghi; tách quyền nghiệp vụ khỏi admin | Kiểm thử gọi thẳng API, thay ID/tham số và dùng lại report_id đều không vượt quyền |
| 3. Quy trình và giao diện | Thêm gửi duyệt/trả lại, danh sách cần duyệt, nhật ký và màn hình cấp quyền; thay kiểm tra `canManage` bằng các quyền cụ thể | Nút/tab khớp quyền backend; người lập không tự duyệt; thay đổi bản đang duyệt bị kiểm soát; giao diện dùng được trên điện thoại |
| 4. Chuyển đổi và chạy thử | Sao lưu state; chuyển đổi tài khoản theo danh sách đã chốt; giữ bản đã duyệt và lịch sử; kiểm tra bằng tài khoản thử riêng | Không mất kế hoạch/bản chốt; không tự mở rộng quyền; người bị thu hồi quyền không dùng lại phiên cũ; có bản sao lưu đã thử khôi phục |
| 5. Áp dụng | Chạy thử tại một xí nghiệp, đối chiếu quyền với công việc thực tế, sau đó áp dụng toàn công ty | Người xem, người lập, người duyệt và quản trị hoàn thành đúng luồng; không ảnh hưởng cách tính tấn thông qua |

Chuyển đổi `manager` cũ không tự động thành người duyệt toàn công ty. Cần gán rõ từng người trước khi chuyển, để vừa tránh cấp dư quyền vừa không làm gián đoạn công việc. Admin cũ chỉ giữ quyền nghiệp vụ khi có phân công tương ứng.

Cần chuyển đổi cả bản nháp đang xử lý: giao người phụ trách và người duyệt, không tự gửi duyệt hàng loạt. Nháp cũ thiếu lịch sử người sửa phải được rà soát và tạo phiên bản mới do người lập xác nhận trước khi gửi duyệt; không giả định lịch sử còn thiếu là chưa có ai sửa. Các bản đã duyệt trước đây giữ nguyên hiệu lực và lịch sử.

Các ca kiểm thử bắt buộc:

- Chưa đăng nhập bị từ chối; người chỉ xem không thể tạo/sửa/duyệt/xóa/chốt dù gọi trực tiếp API.
- Người chỉ có Cửa Lò không thấy Bến Thủy hoặc dữ liệu toàn công ty qua tổng số, tìm kiếm, ID trực tiếp, lịch sử, file Excel/CSV hay báo cáo chốt.
- Người không có quyền Cầu 5/Chưa xác định cầu không xem được các dữ liệu đó qua cache, chi tiết chuyến, bộ lọc hoặc file tải. Tổng số và phân trang chỉ phản ánh bản ghi được phép xem.
- Người lập hoặc sửa nội dung phiên bản không tự duyệt được; người duyệt sai phạm vi cũng bị từ chối.
- Xem cả hai xí nghiệp nhưng chưa được giao duyệt toàn công ty thì không duyệt được kế hoạch công ty.
- Quyền truy cập bản nháp được áp dụng cả cho chi tiết và lịch sử; người chỉ xem không nhìn thấy nháp qua phiên bản liên quan.
- Quyền xuất tổng hợp/chi tiết được kiểm tra riêng; xuất Excel dùng dữ liệu đúng phạm vi, không chỉ dựa vào nút trên giao diện.
- Đổi quyền, khóa tài khoản hoặc hết ủy quyền có hiệu lực ở lần kiểm tra tiếp theo; không tái sử dụng được phiên hoặc bản tra cứu cũ để vượt quyền.
- Khi đổi quyền lúc người dùng đang mở dashboard, frontend kiểm tra lại phiên khi quay về tab và định kỳ; mục tiêu trong vòng 60 giây khi tab hoạt động phải đóng chi tiết và xóa dữ liệu không còn được phép hiển thị. Backend từ chối ngay yêu cầu mới sau khi thu hồi quyền.
- Hai người thao tác cùng phiên bản không ghi đè hoặc duyệt nhầm; thu hồi quyền trong lúc thao tác phải được kiểm tra lại trước khi ghi dữ liệu.
- Mỗi thay đổi quan trọng có nhật ký, không chứa bí mật; không thể làm mất quản trị viên cuối cùng hoặc sửa dữ liệu báo cáo đã chốt.

Ưu tiên đầu tiên: tách lập/duyệt/xóa/chốt và kiểm tra phạm vi tại backend. Sau đó hoàn thiện giao diện cấp quyền và luồng phê duyệt; giữ nguyên cơ chế tính sản lượng đã đối chiếu.
