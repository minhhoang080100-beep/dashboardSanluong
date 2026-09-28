# Triển khai nguồn tổ và nhân sự — 25/09/2026

Tiếp theo [báo cáo nguồn](RA_SOAT_TO_VA_NHAN_SU_KHAI_THAC_20260925.vi.md), đã sửa adapter và thêm màn hình đối chiếu local. Phạm vi người dùng xác nhận gồm **tất cả các tổ, kể cả các tổ hỗ trợ**.

## Thay đổi adapter

- `oprt.portOpTeam` chuyển nguồn mặc định từ `Gang` sang `Organization`: ID, mã và tên tổ lấy trực tiếp từ nguồn. Nguồn `Gang` vẫn bị từ chối vì chứa phân công theo ca/chuyến.
- `oprt.portOpStaff` bổ sung khóa tổ `Employee.organizationMainId`, giữ `employeeId`, `employeeCode`, `employeeFullName`.
- Bộ lọc mới dùng `operation_team_scope`: danh sách ID tổ hữu hạn theo từng database. Không lọc riêng `organizationTypeId=8`, không suy từ tên, không tự lấy bản ghi của một nguồn để ghi đè nguồn kia.
- Truy vấn sử dụng tham số `IN (?, …)`, tối đa 1.000 ID tổ mỗi nguồn. Từ chối ID âm/0, bool, ID lặp, terminal lạ và cấu hình chưa duyệt trước khi đọc dữ liệu.
- Kiểm tra đủ ID tổ tồn tại; nếu một tổ được chọn không có trong nguồn thì báo lỗi. Tổ có thật nhưng không có nhân viên là trường hợp rỗng hợp lệ.
- Bản ghi xóa mềm vẫn được đọc. Không đổi cờ xóa thành trạng thái lao động; không dùng ngày chạy API làm ngày nguồn.
- Cấm ánh xạ lại ID, mã, tên và khóa tổ gốc trong hai nguồn native. Khi công bố, kiểm tra lại phạm vi tổ và đầy đủ ID tổ, kể cả nếu file preview bị sửa thành `ready=true`.

Ví dụ cấu trúc cấu hình cho một danh sách nhỏ đã đối chiếu; không phải bộ ID đầy đủ và không tự bật production:

```json
{
  "approved": true,
  "operation_team_scope": {
    "cua_lo": [34, 43, 44, 46],
    "ben_thuy": [48, 49, 50, 53, 54, 55, 56]
  }
}
```

Trong cấu hình mẫu của repository, `operation_team_scope` vẫn là `{}` và `approved=false`. Các nguồn danh mục riêng `WorkTeam`/`Staff` chỉ được dùng qua override đã duyệt, không bị giả định có cùng cấu trúc với SmartTOS.

## Màn hình đối chiếu local

Mở `http://127.0.0.1:8765/`, chọn **Tổ và nhân sự**:

1. Chọn **Nguồn đối chiếu**: Cửa Lò hoặc Bến Thủy.
2. Chọn tổ hoặc để “Tất cả tổ”; tìm nhân viên bằng tên/mã.
3. Danh sách nhân sự hiển thị 25 dòng mỗi trang. Danh sách tổ đầy đủ nằm trong mục thu gọn.
4. Cờ xóa hiển thị đúng trạng thái nguồn; ID có dữ liệu khác giữa hai nguồn được đánh dấu để đối chiếu.
5. Mục **Đối chiếu ID** cho chọn Tổ/Nhân sự, lọc loại khác biệt, tìm theo ID và xem 20 ID mỗi trang. Mỗi trường thay đổi hiển thị hai giá trị nguồn cạnh nhau. Chỉ so sánh ID có ở cả hai nguồn; nếu thiếu một nguồn thì báo chưa đủ đối chiếu.

Nguồn SQL mới được đọc ngày 25/09; đây là bản đọc đã lưu, không phải mỗi lần GET lại truy vấn SQL Server. Chỉ lấy ID, mã, họ tên, tổ, cờ xóa và ngày nguồn của nhân viên thuộc danh sách đang đối chiếu; không lấy giấy tờ, địa chỉ, liên hệ, ảnh hoặc hồ sơ cá nhân khác.

| Nguồn database | Tổ/đơn vị ứng viên | Hồ sơ gắn các tổ này | Chưa có đơn vị chính | Thuộc đơn vị ngoài phạm vi |
|---|---:|---:|---:|---:|
| Cửa Lò | 35 | 612 | 533 | 12 |
| Bến Thủy | 35 | 554 | 533 | 11 |

Số lượng gồm các hồ sơ đã đánh dấu xóa và bản sao tổ của xí nghiệp khác trong cùng database. **Không cộng 612 và 554 thành số người của công ty**, không gọi các hồ sơ có cờ xóa trống là “đang làm”. Hai số 533 là hồ sơ nguồn chưa gắn đơn vị chính, không phải số lao động đang thiếu phân công ca.

Có 32 ứng viên đã phân loại là tổ, gồm tổ hỗ trợ và tổ lịch sử; ba ID **11, 18, 47** (hai đơn vị mang tên tàu lai và Cầu bến) được giữ trên màn hình với nhãn **Cần xác nhận**. Ba ID này chưa nằm trong scope kiểm tra API local. Các nút tổng như “Công nhân” ID 33 và phòng/ban không được biến thành tổ chỉ để gán nhân viên.

Trong toàn bộ danh sách đối chiếu của hai nguồn, có **275 ID nhân viên và 13 ID tổ có dữ liệu khác nhau**. Đây là khác biệt của các trường đang xem, có thể gồm ngày/cờ xóa, không đồng nghĩa 275 người khác nhau. Phạm vi này rộng hơn phép thử riêng hai cây xí nghiệp trong báo cáo trước (10 ID khác mã/họ tên); không so sánh hai số như cùng một chỉ tiêu.

Kết quả phân loại theo từng trường, từ bản đọc SQL đã lưu ngày 25/09:

| Đối tượng | Loại khác biệt ưu tiên đối chiếu | Số ID |
|---|---|---:|
| Nhân sự | Khác mã hoặc họ tên | 273 |
| Nhân sự | Mã/tên giống, khác đơn vị chính | 1 |
| Nhân sự | Chỉ khác ngày nguồn | 1 |
| Tổ | Chỉ khác cờ xóa nguồn | 13 |

Phân loại có thứ tự ưu tiên: thông tin nghiệp vụ, quan hệ tổ, thuộc tính thiếu, cờ nguồn, ngày. Một ID chỉ tính một nhóm, nhưng bảng chi tiết giữ **tất cả trường khác nhau**; 273 ID khác mã/tên vẫn có thể khác thêm cờ/ngày. Kết quả không tự khẳng định cùng người hay khác người, không gộp ID hoặc chọn nguồn thắng.

35 tổ ở mỗi nguồn đều thiếu cả hai ngày tạo/sửa (70 bản ghi nguồn). Nhân sự có 16 hồ sơ ở Cửa Lò và 1 ở Bến Thủy thiếu một trường ngày; **không hồ sơ nào trong 612/554 dòng thiếu cả hai ngày**. Thiếu một ngày không đồng nghĩa không lọc được kỳ; API sử dụng ngày tạo **hoặc** sửa. Hai bộ đếm được tách trên màn hình để tránh hiểu nhầm.

## Giới hạn công bố

Màn hình đối chiếu chưa phải payload đủ điều kiện gửi Tổng công ty. Các vấn đề còn lại:

- Chưa có nguồn `status` 1/2/3 của tổ và nhân viên theo đặc tả; 35 tổ ứng viên đều thiếu cả ngày tạo/sửa.
- Còn ID trùng có dữ liệu khác giữa hai database; chưa áp quy tắc chọn nguồn thắng, đổi ID hoặc bỏ dòng lịch sử.
- Ba đơn vị cần xác nhận phân loại vẫn giữ riêng.

Người dùng đã xác nhận trạng thái tổ/nhân sự được quản lý bằng **Excel hoặc danh sách đã duyệt**. Chưa có file đó trong lượt này để xác minh cấu trúc và ghép bản ghi. Khi nhận file, cần đối chiếu ID/mã tổ, ID/mã nhân viên và nguồn tương ứng; tên dùng hỗ trợ đối chiếu, không làm khóa duy nhất. Trạng thái tổ và trạng thái lao động phải được ánh xạ theo đúng hai danh mục trong đặc tả, không suy từ cờ xóa. Nếu file không có lịch sử ngày tạo/sửa tổ, cần làm rõ nguồn ngày thay đổi với bên nhận; không dùng ngày nhập Excel hoặc ngày chạy API làm ngày nguồn.

Đối chiếu bốn sheet Bulk/Container cho thấy `status` có dấu bắt buộc; từng trường ngày không có dấu bắt buộc, nhưng truy vấn yêu cầu khoảng ngày và mô tả lọc theo `createdDate`/`modifiedDate`. Chưa tìm được bảng/view trạng thái hoặc lịch sử HR thay thế trong các bằng chứng đã đọc. Một số lần đọc metadata trước đây bị timeout hoặc chỉ kiểm tra phạm vi hẹp, nên không kết luận SQL không có các bảng khác.

Hai đường dẫn công khai tiếp tục trả lỗi chưa sẵn sàng thay vì tạo trạng thái/ngày giả. Cấu hình scope chỉ giải quyết chọn đúng tổ và khóa nhân sự, không giải quyết thay các dữ liệu nguồn còn thiếu.

Route riêng `/local-team-audit` chỉ phục vụ loopback, yêu cầu đồng thời quyền `oprt.portOpTeam` và `oprt.portOpStaff`, đọc file có giới hạn dung lượng và chỉ trả các trường được phép. Thiếu một quyền bị từ chối trước khi đọc file. Phản hồi không cache. JSON các API công khai vẫn chỉ gồm `data`, `code`, `message`.

## Sửa lỗi phát hiện khi kiểm tra công bố

- Dùng chung kiểm tra `terminals` trước trích xuất/công bố: danh sách không rỗng, không trùng và chỉ chứa nguồn hợp lệ. Cấu hình rỗng trước đây có thể vượt kiểm tra phạm vi nếu preview cũng rỗng; nay bị từ chối và giữ nguyên snapshot đang phục vụ.
- Trạng thái tổ/nhân sự từ chối boolean `true`/`false`, tránh việc `true` bị chuyển thành mã nghiệp vụ `1`. Các mã số `1/2/3` giữ nguyên.
- Bộ đối chiếu riêng từ chối nguồn trùng, ID lặp trong cùng nguồn, nhân viên gắn sai tổ và cờ xóa sai kiểu. Thông báo lỗi không chứa giá trị hồ sơ. Những thay đổi này không sửa dữ liệu SmartTOS.

## Bằng chứng và kiểm tra

- `outputs/oprt-teams-20260925/roster-live.json`: 6/6 SELECT thật thành công, chỉ các trường nghiệp vụ cần cho đối chiếu.
- `build_roster.py` kiểm tra số tổng, ID không lặp trong từng nguồn, nhân viên thuộc đúng tổ, đủ phạm vi; ghi `roster-review.json` và `roster-summary.json`.
- Các file roster nằm trong `outputs`, đã xác nhận được Git bỏ qua. Không đưa danh sách nhân sự hoặc token vào tài liệu/console.
- Kiểm tra HTTP thật cả 20 đường dẫn công khai và route roster: chỉ `serviceType` có bản công bố hợp lệ (10 dòng); roster trả đúng 612/554 hồ sơ theo nguồn, còn hai API tổ/nhân sự vẫn bị chặn.
- Kiểm tra route local: không đăng nhập 401, thiếu từng quyền 403, thiếu capture 503, đủ quyền 200; whitelist trường, `no-store` và chặn Origin ngoài đều đạt.
- `hr-status-date-source-audit.json`: đối chiếu bốn sheet đặc tả và bằng chứng nguồn trạng thái/ngày; không chứa danh sách cá nhân.
- `roster_reconciliation.py` có 30 kiểm thử cho phân loại từng trường, dữ liệu thiếu, nguồn trùng, ID trùng, khóa tổ sai và không rò trường riêng. Route local tự tính lại dấu ID xung đột từ nội dung, không tin danh sách đánh dấu có thể đã cũ trong capture.
- JavaScript/Python qua kiểm tra cú pháp. Chưa xác minh trực quan bằng trình duyệt trong lượt này vì công cụ không có trình duyệt khả dụng.

Lượt chạy cuối `python -m pytest tests -q -k corporate`: **656 passed**, 603 bài ngoài phạm vi không chạy, 2 cảnh báo deprecation của thư viện; thời gian 197,71 giây. Bao gồm kiểm thử scope trước đó, 30 bài bộ đối chiếu và các regression mới cho nguồn rỗng/sai, boolean status, giữ nguyên snapshot khi công bố lỗi. HTTP local sau cập nhật trả đúng các tổng đối chiếu trong bảng trên; 20 API công khai vẫn giữ trạng thái sẵn sàng như trước. Chưa push Git, triển khai production hoặc sửa SQL nguồn.
