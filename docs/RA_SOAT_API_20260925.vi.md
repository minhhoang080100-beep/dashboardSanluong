# Rà soát API ngày 25/09/2026

Tài liệu này ghi nhận lượt rà soát đầu ngày. Lượt xử lý tiếp theo đã sửa nguồn `serviceType` sang `PortServiceType` (10 bản ghi hợp lệ trên local), bổ sung cách đọc schema bằng `SELECT TOP (0)` và đạt 454 kiểm thử. Xem [bản cập nhật tiếp theo](XU_LY_API_TIEP_THEO_20260925.vi.md); các số liệu về `serviceType`, 20 endpoint chưa công bố và 370 kiểm thử dưới đây là trạng thái trước cập nhật đó.

Đã kiểm tra lại cấu trúc 18 bảng ở mỗi database và đọc đủ 16 bảng danh mục ở mỗi database qua Railway bằng SELECT. Có 32 lượt đọc bảng thành công; dữ liệu gồm cả bản ghi xóa mềm. Hai bảng Gang và Employee mới kiểm tra cấu trúc, chưa lấy bản ghi nhân sự vì ánh xạ đội/trạng thái chưa được xác minh. Không thay đổi SQL nguồn, không công bố hoặc triển khai production.

## Các lỗi đã sửa

- Cầu/bến: bỏ ràng buộc độ sâu phải không âm; giữ đúng dấu của SmartTOS. Có 27/28 dòng Cửa Lò và 23/25 dòng Bến Thủy có độ sâu âm. Không lấy trị tuyệt đối hoặc tự đổi mốc độ sâu. Tham chiếu ô D59/N59 của sheet portBerth trong hai đặc tả B/C; đặc tả không yêu cầu giá trị dương.
- Kho/bãi Bulk và Container: bổ sung quan hệ `warehouseTypeId → whYardTypeId`, giữ ID nguồn. Thiết bị Bulk bổ sung `equipmentTypeId`.
- Đọc danh mục vận hành: kiểm tra toàn bộ dòng, nhóm lỗi và số dòng bị ảnh hưởng; không dừng ở lỗi đầu tiên. Mẫu lỗi chỉ chứa chỉ số dòng, không chứa dữ liệu cá nhân.
- Bộ S: kết quả truy vấn sai cấu trúc không còn được hiểu là 0 dòng; metadata sai và cột SELECT bị thiếu không còn gây KeyError hoặc âm thầm trở thành null.
- Báo cáo đối soát: phân biệt 0 dòng đã đọc thành công với số dòng chưa xác định; giữ số dòng nguồn, hợp lệ và lỗi ngay cả khi dataset chưa đủ điều kiện.
- Truy vấn cấu trúc vận hành chuyển sang `sys.columns` theo `OBJECT_ID` và `TYPE_NAME`. Truy vấn khảo sát rộng có JOIN hết thời gian 8,79 giây; truy vấn theo bảng đã kiểm tra với Warehouse mất 3,04/1,46 giây ở hai nguồn. Đây là phép kiểm tra cụ thể, chưa phải đo tải toàn hệ thống.

## 20 danh mục vận hành — dữ liệu thật ngày 25/09

Số nguồn dưới đây đếm cả xóa mềm, trước gộp ID. “Hợp lệ” chỉ đánh giá từng nguồn theo hợp đồng, không đồng nghĩa đã được bên nhận duyệt. Khi còn lỗi dòng hoặc ID xung đột, dữ liệu gộp toàn công ty chưa được công bố.

| API | Dòng nguồn Cửa Lò / Bến Thủy | Hợp lệ Cửa Lò / Bến Thủy | Vướng mắc còn lại |
|---|---:|---:|---|
| `oprt.portEquipment` | 40 / 40 | Chưa xử lý / Chưa xử lý | Chưa có ánh xạ trường bắt buộc: serialCode, registrationCode, isRent. |
| `oprt.portEquipType` | 0 / 0 | Chưa xử lý / Chưa xử lý | Thiếu cột nguồn EquipmentType.equipmentTypeCode cho equipmentTypeCode. |
| `oprt.portWHYard` | 22 / 20 | 22 / 20 | 10 ID xung đột giữa hai nguồn |
| `oprt.portWHYardType` | 11 / 11 | 4 / 4 | 14 dòng thiếu cả ngày tạo/sửa |
| `oprt.berths` | 28 / 25 | 28 / 25 | 7 ID xung đột giữa hai nguồn |
| `oprt.jobType` | 19 / 17 | 19 / 17 | 15 ID xung đột giữa hai nguồn |
| `oprt.jobMethod` | 1037 / 660 | 1037 / 660 | 354 ID xung đột giữa hai nguồn |
| `oprt.deliveryMethod` | 13 / 13 | 0 / 0 | 26 dòng thiếu cả ngày tạo/sửa |
| `oprt.serviceType` | 234 / 212 | 234 / 212 | 13 ID xung đột giữa hai nguồn |
| `oprt.cargoItems` | 172 / 132 | Chưa xử lý / Chưa xử lý | Chưa có ánh xạ trường bắt buộc: dangerousGoodsCheck. |
| `oprt.cargoGroups` | 48 / 43 | 48 / 43 | 14 ID xung đột giữa hai nguồn |
| `oprt.unitMeasurement` | 90 / 82 | 90 / 82 | 43 ID xung đột giữa hai nguồn |
| `oprt.cargoDirect` | 6 / 6 | 0 / 0 | 12 dòng thiếu cả ngày tạo/sửa |
| `oprt.operationLocationType` | 6 / 0 | 0 / 0 | 6 dòng thiếu cả ngày tạo/sửa |
| `oprt.portOpTeam` | Chưa đọc / Chưa đọc | Chưa xử lý / Chưa xử lý | Chưa có ánh xạ trường bắt buộc: status. |
| `oprt.portOpStaff` | Chưa đọc / Chưa đọc | Chưa xử lý / Chưa xử lý | Chưa có ánh xạ trường bắt buộc: teamId, status. |
| `oprt.vesselType` | 18 / 18 | 5 / 5 | 26 dòng thiếu cả ngày tạo/sửa |
| `oprt.equipments` | 40 / 40 | Chưa xử lý / Chưa xử lý | Chưa có ánh xạ trường bắt buộc: equipmentTypeCode1, equipmentTypeName1, equipmentTypeCode2, equipmentTypeName2, manufacturerName, serialCode, registrationCode, installDate, currentRunningHour, isRent. |
| `oprt.contwhYards` | 22 / 20 | 22 / 20 | 10 ID xung đột giữa hai nguồn |
| `oprt.contSizeType` | 740 / 411 | 740 / 411 | 411 ID xung đột giữa hai nguồn |

Điểm cần xử lý với chủ dữ liệu/bên nhận:

1. **ID trùng:** cần quy tắc hợp nhất hoặc định danh thống nhất giữa hai database. Kho/bãi (10 ID ở mỗi API) và loại tác nghiệp (15 ID) chỉ khác ngày; cần quy tắc giữ lịch sử tạo/sửa trước khi gộp. Các danh mục khác còn có khác mã/tên hoặc trạng thái xóa, không thể tự chọn một nguồn hay bỏ dòng xóa mềm. Không thêm tiền tố vào ID.
2. **Ngày danh mục thiếu:** loại kho/bãi, giao nhận, hướng hàng, vị trí tác nghiệp và loại tàu có dòng thiếu cả ngày tạo/sửa. Cần nguồn lịch sử đáng tin cậy hoặc thống nhất chế độ lấy toàn bộ danh mục với bên nhận. Không lấy ngày hôm nay làm ngày tạo.
3. **Thiết bị và mặt hàng:** thiếu các trường bắt buộc trong nguồn đang dùng. `EquipmentType` hiện có 0 dòng và không có cột equipmentTypeCode; 80/80 dòng Equipment có equipmentTypeId=null. Cargo không có dangerousGoodsCheck; không mặc định mọi hàng đều không nguy hiểm.
4. **Đội/nhân viên:** Gang có ca/ngày/chuyến tàu, chưa đủ bằng chứng là danh mục đội tổ chức; Employee chưa có quan hệ đội và trạng thái 1/2/3 đúng nghĩa đặc tả. Chưa dùng rowInvisible/rowDeleted để suy ra trạng thái lao động.
5. **Kích cỡ container:** 411 ID chung có mã/tên giống nhau nhưng containerTeu của Bến Thủy đều null, trong khi Cửa Lò có 1/2. Đây là thiếu thông số, chưa phải bằng chứng có 411 đối tượng khác nhau. Cần thống nhất nguồn chuẩn trước khi bổ sung TEU cho bản gộp.
6. **Dịch vụ:** PortService còn có portServiceTypeId. Cần đối chiếu với bên nhận để xác nhận serviceType yêu cầu dịch vụ cụ thể hay nhóm dịch vụ; hiện vẫn bị chặn công bố do xung đột.

## Bộ S — rà lại 12 API đã triển khai

Phần này dùng bằng chứng SQL và HTTP local ngày 23/09; không phải lần lấy lại sản lượng ngày 25/09. Kỳ kiểm tra là 10/2025, 11/2025, 12/2025 và 01/2026. Số dòng dưới đây là dữ liệu xem thử chưa đầy đủ, không phải tổng báo cáo đã được xác nhận.

| API sản lượng | Tổng dòng xem thử 4 tháng | Lỗi cần đối soát |
|---|---:|---|
| `bulkGateVolumesCB` | 867 | CARGO_KIND_UNMAPPED, METHOD_ID_UNAVAILABLE, VESSEL_TYPE_UNAVAILABLE, WEIGHT_OR_UNIT_UNAVAILABLE |
| `bulkQuayVolumesCB` | 1129 | CARGO_KIND_UNMAPPED, PHYSICAL_SHIP_OR_DIRECTION_UNAVAILABLE, SOURCE_SCOPE_UNKNOWN, WEIGHT_OR_UNIT_UNAVAILABLE |
| `contGateVolumesCB` | 1078 | CARGO_KIND_UNMAPPED, CONTAINER_QUANTITY_UNIT_UNCONFIRMED, METHOD_ID_UNAVAILABLE, VESSEL_TYPE_UNAVAILABLE, WEIGHT_OR_UNIT_UNAVAILABLE |
| `contQuayVolumesCB` | 638 | CARGO_KIND_UNMAPPED, CONTAINER_QUANTITY_UNIT_UNCONFIRMED, SOURCE_SCOPE_UNKNOWN, WEIGHT_OR_UNIT_UNAVAILABLE |

| API danh mục S | Kết quả kiểm tra 23/09 |
|---|---|
| `cargoCategory` | 29 ID xung đột; chưa công bố |
| `cargoType` | 2 ID xung đột; chưa công bố |
| `class` | 6 dòng chính thức local trong đợt kiểm tra |
| `containerSize` | 6 ID xung đột; chưa công bố |
| `customers` | 313 ID xung đột; chưa công bố |
| `handlingMethodList` | 109 ID xung đột; chưa công bố |
| `origins` | 2 dòng chính thức local trong đợt kiểm tra |
| `shipDetails` | 55 ID xung đột; chưa công bố |

Login đã được kiểm tra bằng tài khoản máy local; quyền cũ không tự mở rộng sang các API vận hành. JSON công khai vẫn chỉ gồm `data`, `code`, `message`; số trang và phiên dữ liệu nằm trong header.

## Xem và tái kiểm tra

- Trang local: `http://127.0.0.1:8765/`. Danh mục vận hành dùng bản đọc SQL mới ngày 25/09, kèm trạng thái riêng từng nguồn và số ID xung đột. Đây là trang xem thử, không phải công bố cho Tổng công ty.
- Dữ liệu bằng chứng riêng của máy nằm trong `outputs/oprt-review-20260925/`: `describe-live.json`, `masters-live.json`, `operation-api-review.json`, `s-api-review.json`. Các file này được Git bỏ qua.
- `audit_live.py` chạy lại adapter hiện tại trên bản đọc đã lưu; không truy vấn SQL mới. `capture.py` là công cụ SELECT có giới hạn thời gian, không triển khai lên Railway.
- Giữ nguyên công thức sản lượng, phạm vi Cầu 5, loại trừ RORO/nắp hầm và ID gốc. Không thêm số giả, không cập nhật bảng nguồn.

## Kết quả kiểm thử bản sửa

- `.venv-audit/Scripts/python.exe -m pytest tests -q -k corporate`: **370 passed**, 603 bài ngoài phạm vi không chạy. Có hai cảnh báo deprecation của thư viện kiểm thử, không có lỗi kiểm thử.
- Kiểm tra HTTP local cả 20 danh mục vận hành: 9 mục có dữ liệu xem thử (200), 11 mục chưa đủ điều kiện xem dữ liệu (503). Cả 20 đường dẫn chính thức vẫn trả 503 vì chưa có bản công bố hợp lệ; không trả thành công với dữ liệu thiếu.
- Trang chính và Swagger trả 200; JavaScript qua kiểm tra cú pháp. API cầu/bến xem thử có đủ 53 dòng từ hai nguồn. Kiểm tra JSON không có `sourceDatabase` hay chẩn đoán nội bộ.
- Chưa kiểm tra trực quan trên trình duyệt hoặc triển khai production. Các kiểm thử mô phỏng và HTTP local không thay thế đối soát nghiệp vụ với Tổng công ty.
