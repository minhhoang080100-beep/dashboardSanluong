# Xử lý API tiếp theo — 25/09/2026

Lượt rà soát sau đã bổ sung chẩn đoán theo trường, kiểm tra danh mục tham chiếu, sửa nguồn họ tên nhân viên và chặn nhầm bảng phân công thành đội. Xem [kết quả cho từng API](RA_SOAT_TUNG_API_VAN_HANH_20260925.vi.md); số kiểm thử dưới đây là mốc trước lượt bổ sung này.

## Loại dịch vụ: đã sửa nguồn và chạy được trên local

`GET /api/oprt/catalog/serviceType` lấy từ **dbo.PortServiceType**:

| Trường API | Cột SQL |
|---|---|
| serviceTypeId | portServiceTypeId |
| serviceTypeCode | portServiceTypeCode |
| serviceTypeName | portServiceTypeName |
| isDeleted | rowDeleted |
| createdDate / modifiedDate | createTime / updateTime |

Bản SQL ngày 25/09 có 10 dòng ở mỗi database, trùng khớp cả ID, nội dung, trạng thái và ngày. Sau gộp đúng bản ghi giống nhau còn **10 loại dịch vụ**. Có đủ ngày tạo hoặc sửa cho tất cả dòng; không tạo mã hoặc ngày thay thế.

Ánh xạ cũ dùng `PortService` (234 dòng Cửa Lò, 212 dòng Bến Thủy), tức danh sách dịch vụ chi tiết. Bảng này có khóa `portServiceTypeId` trỏ tới loại dịch vụ; không dùng `portServiceId` làm ID loại. Đặc tả Bulk sheet `17. serviceType`, Container sheet `19. serviceType`, các ô N56–N58 yêu cầu ID/mã/tên **loại**. Các API nghiệp vụ triển khai sau phải dùng cùng khóa loại này.

Đã kiểm tra hợp đồng và công bố bản đọc hợp lệ vào kho **local riêng** để chạy HTTP. Chưa triển khai hoặc công bố lên Railway/Tổng công ty. Các thuộc tính tùy chọn chưa có nguồn vẫn null, không điền cờ giả.

## Đọc cấu trúc SQL và xử lý lỗi

Truy vấn liệt kê rộng `sys.tables` vẫn gặp timeout trong lượt khảo sát này. Đã thêm bộ đọc cấu trúc theo từng bảng bằng `SELECT TOP (0)`, chỉ lấy mô tả cột ODBC, không lấy bản ghi. Kiểm tra database/tên bảng, từ chối kiểu chưa hỗ trợ và không trả lỗi driver có thông tin kết nối.

Bộ đọc danh mục vận hành dùng khả năng này khi chạy với SQL thật; bản đọc lưu sẵn và bộ kiểm thử vẫn dùng giao diện truy vấn cũ. Kiểu trả về là nhóm kiểu ODBC đã xác minh, không khẳng định độ dài/độ chính xác SQL đầy đủ. Việc đóng cursor/connection cũng được bảo vệ để lỗi đóng kết nối không che lỗi gốc hoặc làm lộ thông tin driver. Log ghi đúng bước cursor/execute/fetch.

## ID chỉ khác ngày: tính năng đã làm, mặc định chưa bật

Không dùng cách lấy ngày tạo nhỏ nhất và ngày sửa lớn nhất: cách đó làm mất các ngày trung gian của một nguồn. Cơ chế mới giữ **các cặp ngày gốc**, lọc theo ngày tạo **hoặc** ngày sửa của bất kỳ cặp nào, và đếm mỗi ID một lần. Dòng trả về dùng một cặp ngày thực sự khớp kỳ yêu cầu. Phân trang giữ cùng snapshot; bản công bố mới không làm thay đổi trang của snapshot cũ.

Chỉ được gộp khi tất cả thuộc tính nghiệp vụ và trạng thái giống nhau, kể cả null. Khác tên, mã, TEU hoặc cờ xóa vẫn bị chặn. ID không được thay đổi. Các cặp ngày phụ nằm trong kho riêng, không thêm trường vào JSON công khai.

Tính năng cần bật rõ theo từng API trong profile đã duyệt. Ví dụ cấu hình sau **chỉ minh họa**, chưa được bật trong cấu hình thật:

```json
{
  "approved": true,
  "operation_metadata_merge": {
    "oprt.jobType": "source_date_variants_v1"
  }
}
```

Trước khi duyệt, cần xác nhận các ID tương ứng thực sự là cùng đối tượng của công ty; payload giống nhau chưa đủ để suy ra hai kho ở hai địa điểm là cùng kho. Ngày hoặc dữ liệu nghiệp vụ bị thiếu không được tự bổ sung bằng cơ chế này.

Kết quả thử trên bản đọc SQL thật, **chưa áp dụng**:

| API | Kết quả thử gộp ngày | Vướng mắc còn lại |
|---|---|---|
| jobType | 19 bản ghi, gộp 15 ID chỉ khác ngày | Chờ chốt quy tắc áp dụng |
| portWHYard / contwhYards | 22 bản ghi ở mỗi API, gộp 10 ID | Cần xác nhận đối tượng kho và danh mục loại kho tham chiếu; loại kho còn thiếu ngày |
| berths | Gộp được 5 ID chỉ khác ngày | Còn 2 ID khác nghiệp vụ |
| jobMethod | Gộp được 54 ID chỉ khác ngày | Còn 300 ID khác nội dung/trạng thái |
| cargoGroups | Gộp được 1 ID chỉ khác ngày | Còn 13 ID khác nội dung/trạng thái |
| unitMeasurement / contSizeType | Không gỡ bằng cách gộp ngày | Còn khác trạng thái/nội dung/TEU |

## Nguồn bổ sung đã kiểm tra

- `JobResourceType` có 32/31 dòng và liên kết `JobResourceGroup`, phân biệt cẩu, phương tiện nâng hạ, thiết bị, nhân lực, cân, phương tiện vận chuyển. Không được lấy toàn bộ bảng làm loại thiết bị vì có cả nhân lực và một số ID khác nghĩa giữa hai nguồn.
- `Equipment` có 40 dòng mỗi nguồn, toàn bộ `equipmentTypeId` và `jobResourceId` null; chỉ `jobResourceTypeId` có giá trị. Đây không phải danh sách đầy đủ thiết bị đang được quản lý bằng JobResource. Chưa thay ID thiết bị hoặc gộp máy móc/nhân lực.
- `JobResource` có dữ liệu thuộc nhiều nhóm. Số dòng nhóm phi nhân lực: Cửa Lò 1.002, Bến Thủy 783, gồm cả dữ liệu xóa mềm; đây chưa phải số thiết bị hoạt động đã xác minh. Metadata của Crane/ReachVehicle tồn tại; chưa thấy đủ trường số sê-ri, đăng ký, nhà sản xuất và thuê thiết bị theo hợp đồng.
- `Organization` có 56 dòng mỗi nguồn, gồm công ty, phòng, tổ sản xuất; `Employee.organizationMainId` có thể dẫn tới cơ cấu này. Chưa coi toàn bộ đơn vị là đội tác nghiệp hoặc biến cờ xóa thành trạng thái lao động.
- `Cargo` vẫn thiếu cờ hàng nguy hiểm; còn một dòng mỗi nguồn không có cargoGroupId. Không suy cờ nguy hiểm từ tên hàng.
- Bảng `PortServiceType` đã được phân biệt với `PortService` và áp dụng vào API như phần đầu.

## Bằng chứng và tái kiểm tra

- Kiểm thử toàn bộ phần API doanh nghiệp: **454 passed**, 603 bài ngoài phạm vi không chạy; hai cảnh báo deprecation của thư viện kiểm thử, không có lỗi kiểm thử.
- HTTP local của `serviceType`: trả 10 bản ghi qua 4 trang, giữ nguyên ID 1–10 và body chỉ có `data`, `code`, `message`. Lọc tháng 03/2017 theo ngày tạo trả 3 dòng; lọc ngày 02/01/2022 theo ngày sửa trả 10 dòng; tháng 01/2025 trả 0 dòng. Kết quả lưu tại `http-results.json`.
- Đường dẫn chính thức của `jobType` vẫn trả 503 khi chưa bật quy tắc hợp nhất đã được duyệt; dữ liệu thử 19 dòng chưa được công bố.

Các bản SQL, tổng hợp và script nằm trong `outputs/oprt-next-20260925/`, được Git bỏ qua. File `review-results.json` tách trạng thái mặc định khỏi kết quả thử gộp ngày; `service-local-check.json` ghi nhận phạm vi công bố local. `review.py` chỉ phát lại dữ liệu đã lưu, không truy vấn SQL mới.

Trang xem: `http://127.0.0.1:8765/`, chọn **Loại dịch vụ** trong danh mục vận hành. JSON công khai giữ `data`, `code`, `message`; không thêm sourceDatabase. Các bảng nguồn chỉ được SELECT; chưa đẩy Git hoặc deploy.
