# Rà soát tổ và nhân sự khai thác — 25/09/2026

## Kết luận về nguồn

Theo cách hiểu người dùng vừa xác nhận, `portOpTeam` là **tổ**, `portOpStaff` là **nhân sự trực thuộc từng tổ**. Phạm vi gồm **tất cả các tổ, kể cả tổ hỗ trợ** như Kỹ thuật, Vệ sinh, Văn phòng, Bảo vệ. Nguồn phù hợp để tiếp tục là `Organization` và `Employee.organizationMainId`; không dùng ID phân công ca/chuyến trong `Gang` làm ID tổ.

Đã SELECT mới cả hai database qua Railway, chỉ lấy cấu trúc, danh mục đơn vị và thống kê nhân sự. Không xuất họ tên, liên hệ, giấy tờ hoặc hồ sơ nhân sự để khảo sát. Không sửa SQL nguồn hoặc bật ánh xạ/API mới trong lượt rà soát này.

## Ánh xạ có bằng chứng

| Trường API | Nguồn | Cách hiểu |
|---|---|---|
| `teamId` | `Organization.organizationId` | ID đơn vị được xác nhận là tổ trong phạm vi gửi |
| `teamCode` | `Organization.organizationCode` | Mã nguồn, không tự thêm tiền tố |
| `teamName` | `Organization.organizationName` | Tên tổ/đơn vị nguồn |
| `staffId` | `Employee.employeeId` | ID nhân viên nguồn |
| `staffCode` | `Employee.employeeCode` | Mã nhân viên nguồn |
| `staffName` | `Employee.employeeFullName` | Họ tên đầy đủ |
| `portOpStaff.teamId` | `Employee.organizationMainId` | Chỉ nhận nếu tham chiếu đến tổ đã chọn |
| `isDeleted` | `rowDeleted` của từng bảng | Giữ đúng null/0/1; không thay thế trạng thái lao động |
| `createdDate` / `modifiedDate` | `createTime` / `updateTime` của chính bảng | Không lấy ngày phân công hoặc ngày chạy API để lấp |

Quan hệ này thể hiện **đơn vị chính hiện được lưu**, không chứng minh mọi tổ kiêm nhiệm hay lịch sử chuyển tổ. Nhân viên có một `organizationMainId`; khi tổng hợp theo cây chỉ đếm mỗi `employeeId` một lần trong từng nguồn. Không cộng lại nhân sự cây con vào dòng trực thuộc cha.

## Không thể lọc tổ chỉ bằng loại hoặc tên

`OrganizationType` có loại 8 = Tổ, nhưng dữ liệu thực tế không gán loại nhất quán:

- Tổ Cẩu ID 43, Tổ Xe ID 44 và Tổ Điều độ ID 45 đều có `organizationTypeId=3` (Công ty).
- Các tổ Bến Thủy ID 48, 50, 53–56 cũng khai loại 3. Lọc loại 8 sẽ bỏ sót những đơn vị này.
- Loại 8 còn chứa “Công nhân” (ID 33), là nút cha của nhiều tổ, và các tổ CNTT/Bảo vệ cũ. Tên có chữ “Tổ” cũng không đủ xác định phạm vi khai thác.

Vì vậy, bộ lọc API cần **danh sách ID tổ theo cây đơn vị và chức năng**, không dùng riêng loại 8 hoặc tìm chuỗi tên. Đã chốt bao gồm các tổ hỗ trợ; vẫn phân biệt tổ với phòng/ban, đơn vị tổng và các bản ghi tổ lịch sử để không gán nhầm nhân sự. Không loại hồ sơ chỉ vì tổ thuộc nhóm hỗ trợ.

## Cây đơn vị và số hồ sơ trực thuộc

Cây ID 27 là Xí nghiệp xếp dỡ Cửa Lò; cây ID 28 là Xí nghiệp xếp dỡ Bến Thủy. Đây là các phạm vi ứng viên để kiểm tra nguồn sở hữu, chưa phải quy tắc chọn nguồn đã bật. Dữ liệu của cả hai cây có mặt trong cả hai database: nguồn Cửa Lò còn 52 hồ sơ thuộc cây Bến Thủy; nguồn Bến Thủy có 503 hồ sơ cây Cửa Lò và cả 503 được đánh dấu xóa.

Các bảng dưới lấy cây 27 trong SmartTOS và cây 28 trong SmartTOS_BenThuy. Số lượng là **hồ sơ nguồn**, gồm xóa mềm, không phải số lao động đang làm việc. Cột “Cờ xóa trống” là `rowDeleted IS NULL`, không được tự coi là “đang làm”. Không có hồ sơ `rowDeleted=0` trong hai phạm vi này. Các đơn vị phòng/ban cũng được giữ để minh họa phần cần loại khỏi phạm vi API tổ.


### Cửa Lò

20 nút đơn vị; 568 hồ sơ, gồm 304 đánh dấu xóa và 264 cờ xóa trống.

| ID | Mã | Tên đơn vị | Loại nguồn | Hồ sơ trực thuộc | Đánh dấu xóa | Cờ xóa trống |
|---|---|---|---:|---:|---:|---:|
| 29 | — | Ban lãnh đạo | 3 | 2 | 0 | 2 |
| 30 | — | Phòng tài chính kế toán | 3 | 1 | 0 | 1 |
| 31 | — | Trung tâm khai thác | 3 | 5 | 1 | 4 |
| 34 | TO1CL | Tổ 1 | 8 | 42 | 22 | 20 |
| 35 | TO2CL | Tổ 2 | 8 | 33 | 16 | 17 |
| 36 | TO6CL | Tổ 6 | 8 | 21 | 21 | 0 |
| 37 | TO7CL | Tổ 7 | 8 | 32 | 15 | 17 |
| 38 | TO8CL | Tổ 8 | 8 | 44 | 21 | 23 |
| 39 | TO10CL | Tổ 10 | 8 | 19 | 19 | 0 |
| 40 | TO11CL | Tổ 11 | 8 | 33 | 16 | 17 |
| 41 | TO15CL | Tổ 15 | 8 | 43 | 23 | 20 |
| 42 | GNCL | Giao nhận | 3 | 79 | 42 | 37 |
| 43 | TOCAUCL | Tổ Cẩu | 3 | 83 | 39 | 44 |
| 44 | TXCL | Tổ Xe | 3 | 36 | 17 | 19 |
| 45 | DDCL | Tổ Điều độ | 3 | 33 | 15 | 18 |
| 46 | VSCNCL | Tổ VSCN | 8 | 62 | 37 | 25 |
| 47 | CBCL | Cầu bến | 3 | 0 | 0 | 0 |

### Bến Thủy

10 nút đơn vị; 59 hồ sơ, gồm 15 đánh dấu xóa và 44 cờ xóa trống.

| ID | Mã | Tên đơn vị | Loại nguồn | Hồ sơ trực thuộc | Đánh dấu xóa | Cờ xóa trống |
|---|---|---|---:|---:|---:|---:|
| 48 | TONGHOP | Tổ Tổng hợp | 3 | 17 | 4 | 13 |
| 49 | DIEUDOGIAONHAN | Điều độ - Giao nhận | 3 | 10 | 4 | 6 |
| 50 | KYTHUAT | Tổ Kỹ thuật | 3 | 6 | 0 | 6 |
| 53 | VANPHONG | Tổ Văn phòng | 3 | 8 | 4 | 4 |
| 54 | VESINHBT | Tổ Vệ sinh | 3 | 6 | 1 | 5 |
| 55 | COGIOI | Tổ Cơ giới | 3 | 6 | 0 | 6 |
| 56 | BAOVE | Tổ Bảo vệ | 3 | 6 | 2 | 4 |

## Điều kiện còn thiếu trước khi gửi API

1. **Ngày và trạng thái tổ:** 22 tổ loại 8 ở mỗi nguồn đều thiếu cả ngày tạo/sửa. Toàn bộ các nút trong hai cây ứng viên cũng thiếu cả hai ngày. Chưa có cột xác nhận trạng thái tổ `1=đang làm, 2=giải thể, 3=tạm nghỉ` trong bảng đã kiểm tra.
2. **Trạng thái nhân sự:** `Employee` không có cột thể hiện trực tiếp `1=đang làm, 2=đã nghỉ, 3=tạm nghỉ`. Cờ xóa, ẩn và trạng thái tài nguyên không tương đương trạng thái lao động. Không tự điền 1.
3. **ID trùng giữa hai nguồn:** toàn bộ Employee có 1.098 ID chung; 824 ID giống mã/họ tên/đơn vị chính, 1 ID giống mã/họ tên nhưng khác đơn vị chính và 273 ID khác mã hoặc họ tên. Những con số này là kết quả so sánh trường, không phải kết luận có 273 người khác nhau.
4. **Sau khi giới hạn cây đơn vị tương ứng vẫn còn xung đột:** 568 + 59 = 627 hồ sơ nguồn, có 10 ID trùng nhưng khác mã hoặc họ tên. Cả 10 dòng Cửa Lò có cờ xóa; phía Bến Thủy có 4 dòng cờ xóa và 6 dòng cờ xóa trống. Không được âm thầm lấy bản chưa đánh dấu xóa để ghi đè ID lịch sử hoặc đổi ID nguồn.
5. **Phạm vi tổ:** đã xác nhận bao gồm tất cả các tổ, kể cả hỗ trợ. Danh sách ID vẫn cần phân biệt với các nút tổng và phòng/ban. Giữ nhân viên không có tổ hoặc thuộc phòng/ban trong danh sách đối chiếu riêng, không tự gán vào một tổ chung.

Truy vấn metadata tìm thêm bảng membership đã timeout; không kết luận database không có bảng kiêm nhiệm/chuyển tổ. Các phép nối Organization/Employee và thống kê trong báo cáo đều đã đọc thành công. Danh mục JobResourceType chỉ có ít liên kết vai trò ở Employee, chưa đủ làm nguồn chức danh hay trạng thái lao động.

Đã kiểm tra thêm `JobResource.organizationId` của nhóm tài nguyên nhân lực: đủ 1.352 dòng Cửa Lò và 891 dòng Bến Thủy, toàn bộ tổ tham chiếu đều null/0. Vì vậy, nguồn này không bổ sung được tổ còn thiếu của Employee. Có nhiều tài nguyên cùng trỏ một root nhân sự (372/139 root lặp) và nhiều loại tài nguyên cho một root (102/86), nên không đếm dòng JobResource thành số nhân viên. Phép so sánh org bằng nhau có thể gồm 0=0, không phải một liên kết tổ hợp lệ.

## Bước triển khai phù hợp

- Chọn ID tổ thuộc phạm vi gửi trong cây đơn vị đã kiểm tra; giữ ID gốc và khóa `organizationMainId`.
- Tạo adapter tổ/nhân sự có kiểm tra phạm vi và khóa tham chiếu; không đơn giản thay `Gang` bằng toàn bộ bảng `Organization`.
- Giải quyết ngày/trạng thái nguồn và quy tắc ID xung đột trước khi công bố. Hai API hiện vẫn chưa được mở bằng dữ liệu suy diễn.

## Tái lập và bằng chứng

- `outputs/oprt-teams-20260925/capture_teams.py` → `team-live.json`: 14/14 SELECT thành công, gồm metadata và thống kê trực thuộc.
- `capture_links.py` → `links-live.json`: so sánh ID/mã/họ tên ngay trên SQL, chỉ trả tổng hợp; truy vấn tìm thêm tên bảng bị timeout và được dừng có giới hạn.
- `capture_home_scopes.py` → `home-scopes-live.json`: kiểm tra lại 2/2 truy vấn thành công, gồm ID nhân viên trong hai cây tương ứng.
- `capture_resource_membership.py` → `resource-membership-live.json`: 4/4 truy vấn thành công; xác nhận nguồn tổ thay thế trong JobResource không có ID tổ dương.
- `review_teams.py` → `team-review.json` và báo cáo này: kiểm tra số tổng bằng các nhóm cờ xóa, kiểm tra đường cha đến gốc không có chu trình, đối chiếu đúng 10 xung đột ID trong phạm vi ứng viên.

Các tệp outputs giữ ở local và được Git bỏ qua. Không dùng kết quả số lượng trên làm chứng nhận nhân sự hiện hành; danh mục nguồn có dữ liệu lịch sử và cờ trạng thái còn thiếu.
