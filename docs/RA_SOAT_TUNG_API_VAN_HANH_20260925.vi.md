# Rà soát từng API vận hành — 25/09/2026

Phạm vi là 20 đường dẫn danh mục của Bulk và Container. Đã đối chiếu các bản SELECT thật của cả hai database, đồng thời lấy mới dữ liệu container và các thống kê quan hệ nhân viên/tổ đội qua Railway. Chưa thay SQL nguồn, bật quy tắc hợp nhất hoặc công bố lên production. Các bản đọc được thu ở những thời điểm khác nhau trong ngày; không phải một giao dịch chụp toàn bộ database cùng lúc.

## Những phần đã xử lý

Cập nhật định nghĩa người dùng: hai API nhân sự được hiểu là **tổ và nhân sự từng tổ, gồm cả tổ hỗ trợ**. Xem [rà soát tổ–nhân sự mới](RA_SOAT_TO_VA_NHAN_SU_KHAI_THAC_20260925.vi.md) về quan hệ Organization/Employee, tổ bị gán sai loại và trùng ID nhân viên. Các nhận xét nguồn Gang dưới đây mô tả adapter ở mốc báo cáo trước khi xác định lại nguồn tổ.

Lượt tiếp theo có thêm [bằng chứng nguồn và phương án xử lý container](BO_SUNG_NGUON_VA_HOP_NHAT_CONTAINER_20260925.vi.md): đã tính thử bổ sung TEU cho 411 ID, chưa bật cấu hình thật; đồng thời sửa kiểm tra độ mới của danh mục liên quan.

- `staffName` đổi từ `Employee.employeeName` sang `employeeFullName`. Trường họ tên đầy đủ có dữ liệu ở toàn bộ 1.157/1.098 dòng Cửa Lò/Bến Thủy; trường tên cũ thiếu 7 dòng mỗi nguồn và khác họ tên đầy đủ ở 1.148/1.089 dòng. Chỉ lấy thống kê tổng hợp, không xuất hồ sơ nhân sự để khảo sát.
- Chặn ánh xạ `Gang` thành danh mục đội, kể cả khi bổ sung được cột trạng thái. Muốn lấy đội cần cấu hình bảng danh mục thực sự đã xác minh; không thể dùng ID của lượt phân công làm ID đội.
- Báo cáo trích xuất kiểm tra mọi trường trên mỗi dòng, không dừng ở lỗi đầu tiên. Ghi trường lỗi, số dòng và tối đa 10 ID số/UUID mẫu; không ghi tên, nội dung trường lỗi hoặc thông tin driver. Số lỗi tách riêng số dòng lỗi để tránh đếm trùng.
- Phân biệt ID chỉ khác ngày, thiếu thuộc tính ở một nguồn, khác cờ xóa/cập nhật và khác giá trị nghiệp vụ. Không tự chọn nguồn thắng, điền null hoặc thay ID.
- Báo cáo đối soát kiểm tra các khóa tham chiếu. Kho/bãi không còn bị đánh dấu sẵn sàng giao khi danh mục loại kho còn lỗi. Phân biệt danh mục đích không nằm trong preview, chưa hợp lệ và thiếu ID. Phạm vi kiểm tra này chỉ là preview; thao tác công bố thật vẫn kiểm tra thêm kho dữ liệu đã công bố.
- Trang xem local hiển thị trường lỗi, ID mẫu và loại chênh lệch để đối chiếu từng API. JSON của đường dẫn công khai vẫn chỉ gồm `data`, `code`, `message`.

## Kết quả từng API

Số dòng ghi theo **Cửa Lò / Bến Thủy**, gồm cả xóa mềm. Đây là số bản ghi nguồn đã đọc hoặc đã đếm, không phải số đối tượng đang hoạt động hay số được phép gửi. Tất cả API ngoài `serviceType` còn điều kiện chưa được giải quyết; không mở thành công bằng cách loại bỏ các dòng lỗi.

| API | Dòng nguồn | Kết quả và việc cần xử lý |
|---|---:|---|
| `portEquipment` | 40 / 40 | Thiếu số sê-ri, đăng ký và trạng thái thuê. Cả 80 dòng thiếu `equipmentTypeId`; `jobResourceTypeId` có dữ liệu nhưng thuộc hệ phân loại khác, cần chọn nguồn thiết bị đầy đủ trước khi đổi FK. |
| `portEquipType` | 0 / 0 | `EquipmentType` rỗng và không có cột mã loại. `JobResourceType` là nguồn ứng viên nhưng trộn người/thiết bị và có ID khác nghĩa; chưa thay thế toàn bộ bảng. |
| `portWHYard` | 22 / 20 | 10 ID chỉ khác ngày. Thử hợp nhất được 22 ID nhưng chưa bật; còn phụ thuộc `portWHYardType` đang thiếu ngày. Cần thống nhất các ID là cùng kho thực tế. |
| `portWHYardType` | 11 / 11 | Mỗi nguồn có 7 dòng thiếu cả ngày tạo/sửa, ID 1–7. Cần ngày có căn cứ từ hệ thống nguồn hoặc quy tắc đồng bộ khởi tạo được bên nhận chấp nhận. |
| `berths` | 28 / 25 | 5 ID chỉ khác ngày; 2 ID khác nội dung: ID 14 là CẦU 3/CẦU 6; ID 10 có độ sâu -12/0. Giữ nguyên giá trị và yêu cầu quy tắc phân biệt ID. |
| `jobType` | 19 / 17 | 15 ID chỉ khác ngày. Thử hợp nhất được 19 ID; cơ chế giữ đầy đủ các cặp ngày đã có, còn chờ chốt áp dụng. |
| `jobMethod` | 1.037 / 660 | 354 ID có chênh lệch: 54 chỉ ngày, 236 chỉ cờ xóa, 64 khác nghiệp vụ (7 trong số 64 còn khác cờ xóa). Không lấy bản ghi mới hơn hoặc còn hoạt động để thay thế tùy ý. |
| `deliveryMethod` | 13 / 13 | Toàn bộ 13 dòng mỗi nguồn thiếu cả ngày tạo/sửa. Cần giải quyết cơ sở lọc kỳ; chưa suy nhóm giao nhận T/B/S từ tên phương án. |
| `serviceType` | 10 / 10 | Đã dùng `PortServiceType`; 10 ID hợp lệ sau gộp bản ghi giống hệt. Đường dẫn chính thức trên local trả dữ liệu, phân trang/lọc ngày đã kiểm tra. |
| `cargoItems` | 172 / 132 | Thiếu nguồn cờ hàng nguy hiểm bắt buộc; 1 dòng mỗi nguồn còn thiếu nhóm hàng. Không coi null là không nguy hiểm hoặc tự tạo nhóm. |
| `cargoGroups` | 48 / 43 | 14 ID chênh lệch: 1 chỉ ngày, 8 chỉ cờ xóa, 5 khác nghiệp vụ (3 trong số 5 còn khác cờ). Là danh mục cha cần giải quyết trước mặt hàng. |
| `unitMeasurement` | 90 / 82 | 43 ID chênh lệch: 39 chỉ cờ xóa, 4 khác nghiệp vụ. Ví dụ ID 34 là Xe thùng/Gàu, ID 82 là Chuyến/Ben, đều đang hoạt động ở hai nguồn. Không quy về cùng đơn vị. |
| `cargoDirect` | 6 / 6 | Cả 6 dòng mỗi nguồn thiếu ngày tạo/sửa. Không dùng ngày chạy API làm ngày cập nhật nguồn. |
| `operationLocationType` | 6 / 0 | Cửa Lò có 6 dòng thiếu ngày; Bến Thủy đã đọc thành công và có 0 dòng. Phân biệt nguồn rỗng đã xác minh với không đọc được nguồn. |
| `portOpTeam` | 29.628 / 10.421 dòng `Gang` | Đây là phân công theo chuyến/ca, không phải số đội. `Gang` không phải nguồn mặc định phù hợp để công bố đội; cần danh mục tổ/đội thực sự cùng trạng thái. |
| `portOpStaff` | 1.157 / 1.098 (đếm tổng hợp) | Đã sửa lấy họ tên đầy đủ. Còn thiếu quan hệ đội và trạng thái lao động. 533 dòng mỗi nguồn thiếu `organizationMainId`; phần có liên kết còn gồm công ty/phòng/ban, chưa thể dùng thẳng làm `teamId`. |
| `vesselType` | 18 / 18 | Mỗi nguồn có 13 dòng thiếu ngày tạo/sửa. Chưa thể chỉ trả 5 dòng có ngày rồi coi là danh mục đầy đủ. |
| `equipments` | 40 / 40 | Ngoài các thiếu hụt thiết bị Bulk, còn thiếu hai cấp loại, nhà sản xuất, ngày lắp đặt và giờ chạy bắt buộc. Cần nguồn quản lý tài sản/thiết bị được xác minh. |
| `contwhYards` | 22 / 20 | Cùng nguồn Warehouse, 10 ID chỉ khác ngày và cùng vướng loại kho như Bulk. Giữ DTO Container riêng; không tự điền block/bay/tier hoặc sức chứa. |
| `contSizeType` | 740 / 411 | 411 ID tương ứng có cùng mã/tên nhưng Bến Thủy thiếu TEU, Cửa Lò có 1/2. Đã phân loại là thiếu thuộc tính, chưa coi là 411 đối tượng khác nhau; chưa tự lấy TEU từ một bên cho cả công ty. |

## Những ánh xạ không nên tự động thực hiện

`Gang` có 29.614/10.401 dòng gắn chuyến và trải 1.702/1.002 ngày khác nhau. `Organization` chứa nhiều cấp tổ chức; chỉ 329/278 dòng nhân viên liên kết đến loại “Tổ”. `JobResourceStatus` là Good/Normal/Bad của tài nguyên, không phải trạng thái lao động 1/2/3. Không chuyển cờ xóa hoặc tình trạng máy móc thành trạng thái nhân viên.

`JobResourceType` ID 37 có mã MAYNANG ở Cửa Lò và DRT ở Bến Thủy, mỗi nguồn có một dòng Equipment tham chiếu. Việc đổi từ `equipmentTypeId` sang khóa này cần xử lý xung đột và chứng minh phạm vi thiết bị, không chỉ nối được bảng là đủ.

Kích thước container nguồn chưa đủ tin cậy để thêm `sizeCode`/`heightCode`: Cửa Lò có 678/740 chiều dài bằng 0 và 729/740 chiều cao bằng 0; Bến Thủy thiếu tương ứng 349/411 và 400/411. Các giá trị chiều dài khác 0 mới có 10/20. Chưa suy kích thước từ tiền tố ISO, chưa đổi 0 thành null, chưa lấy trọng lượng ở bảng khác khi chưa xác minh đơn vị. Điều này không thay đổi cách xử lý danh mục kích cỡ của bộ S hiện có.

## Thứ tự xử lý dữ liệu còn lại

1. Chốt quy tắc hợp nhất `jobType` chỉ khác ngày; tính năng đã sẵn và không cần đổi ID. Kho/bãi cần xác nhận thêm cùng đối tượng và danh mục loại kho.
2. Giải quyết các danh mục nền: đơn vị, nhóm hàng, cầu/bến và phương án. Chọn quy tắc ID theo bên nhận, không âm thầm thêm tiền tố hoặc bỏ một database.
3. Đối chiếu nguồn lịch sử/ngày cập nhật cho 5 danh mục thiếu ngày. Nếu không có, cần quy tắc đồng bộ khởi tạo chính thức; không gán ngày hôm nay để vượt bộ lọc.
4. Bổ sung nguồn thông tin thiết bị, tổ đội/nhân sự và cờ hàng nguy hiểm. Các trường chưa có nguồn tiếp tục được báo đúng trạng thái thiếu.
5. Sau khi dữ liệu đã đủ, chạy lại toàn bộ trích xuất, kiểm tra tham chiếu và HTTP rồi mới công bố danh mục tương ứng.

## Tái lập và kiểm tra

- Kiểm thử tại mốc lập báo cáo này: `python -m pytest tests -q -k corporate` đạt **490 passed**, 603 bài ngoài phạm vi không chạy; 2 cảnh báo deprecation của thư viện kiểm thử. Kết quả lượt xử lý tiếp theo được ghi trong tài liệu bổ sung ở đầu trang.
- Gọi HTTP cả 20 đường dẫn trên local: `serviceType` trả 200 với 10 dòng; 19 đường dẫn còn lại trả 503 do chưa có bản công bố hợp lệ. Màn hình đối chiếu riêng xem được dữ liệu của 9 mục, không đồng nghĩa 9 mục đã được phép gửi toàn công ty. Đã kiểm tra body đúng 3 trường và không lộ dữ liệu chẩn đoán trong payload công khai.
- Kiểm tra trang local nhận đủ phân loại thiếu TEU, ID mẫu thiếu ngày và lý do chặn nguồn Gang; JavaScript qua kiểm tra cú pháp. Chưa thực hiện kiểm tra giao diện bằng hình ảnh trong lượt này.

- `outputs/oprt-deep-20260925/relationship-live.json`: SELECT mới, metadata và thống kê quan hệ; không chứa hồ sơ nhân sự.
- `container-live.json`: 740/411 dòng container và 27 nhóm loại mỗi nguồn.
- `per-api-review.json`: ma trận trường nguồn và kết quả adapter cho cả 20 API, tạo bằng `review.py`.
- `identity-review.json`: phân tích chênh lệch và ví dụ giới hạn; helper `identity_review.py` có assertion đối chiếu.
- `http-results.json`: trạng thái đường dẫn chính thức và màn hình đối chiếu riêng cho từng API, tạo bằng `verify_http.py`.
- `review.py` phát lại bản SQL đã lưu, không giả làm lần truy vấn mới. File thiếu cột đã chụp được ghi “chưa có bằng chứng”, không tính là null.

Các file bằng chứng nằm trong thư mục outputs được Git bỏ qua. Tài liệu này không thay cho xác nhận dữ liệu của bộ phận thống kê hoặc bên nhận API.
