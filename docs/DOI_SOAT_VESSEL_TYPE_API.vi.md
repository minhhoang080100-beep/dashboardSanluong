# Thử lọc API cổng/bãi theo vesselTypeCode

Ngày kiểm tra: 22/09/2026. Kỳ dữ liệu: 16/09/2026. Đọc SELECT trực tiếp qua
Railway từ SmartTOS và SmartTOS_BenThuy; cả sáu truy vấn schema, danh mục và
phiếu đều thành công. Đã đọc 334 dòng Cửa Lò và 18 dòng Bến Thủy.

## Quan hệ nguồn đã kiểm tra

`TallyShift.vesselVoyageId → VesselVoyage.vesselId → Vessel.vesselTypeId → VesselType.vesselTypeId`.

Danh mục hai database đều có:

| vesselTypeId | vesselTypeCode | Ý nghĩa nguồn |
|---|---|---|
| 100 | Container Yard | Bãi Container |
| 101 | Bulk Yard | Bãi hàng rời |
| 102 | Equipment Yard | Bãi hàng thiết bị |
| 103 | Ro-Ro Yard | Bãi Ro-Ro, ngoài phạm vi triển khai |
| 104 | Warehouse | Kho hàng |
| 105 | Boned Warehouse | Kho ngoại quan; giữ đúng cách viết mã trong SQL |
| 106 | CFS | Kho bãi CFS |

Không lọc `VesselType.rowDeleted = 0` đơn thuần vì nhiều mã đang dùng để null;
cần xử lý null giống các danh mục nguồn hiện hành, đồng thời giữ mã chưa tìm
được loại ở trạng thái chưa xác định.

## Kết quả lọc thô, chưa phải số liệu API đã nghiệm thu

| Nguồn | Loại | Dòng phiếu | Tấn xác định được | Dòng thiếu tấn/đơn vị |
|---|---|---:|---:|---:|
| Cửa Lò | Container Yard | 42 | 453,25 | 12 |
| Cửa Lò | Bulk Yard | 20 | 509,96 | 0 |
| Cửa Lò | Warehouse | 5 | 1.048 | 0 |
| Bến Thủy | Bulk Yard | 5 | 84,5 | 0 |

Tấn được lấy từ `weightNetSum` nhân hệ số chuyển đơn vị đã xác định, không
chuyển null thành 0. Không có dòng thuộc các loại kho/bãi khác trong ngày này.
Tất cả 72 dòng kho/bãi trên mang phạm vi cầu `unclassified` trong bộ lọc cũ.
Vì vậy không thể tiếp tục yêu cầu có cầu cập đầu tiên như với tàu cho nhóm này.
Điều này không chứng minh mọi dòng kho/bãi thuộc Nghệ Tĩnh hoặc thuộc Cầu 5;
cần kiểm tra quyền sở hữu/phạm vi kho bãi riêng nếu dùng để tính chỉ tiêu cảng.

## Các điểm cần xử lý khi đưa vào API

- Container Yard: phương án 1018 “Xuất nhập cont”. Các loại xác định được là
  20F: 25 tấn, 40F: 390 tấn, 20E: 38,25 tấn. Có 12 dòng thiếu tấn, trong đó
  6 dòng “Khác” có tổng số lượng 6; không thể mặc định chúng không phát sinh.
- Bulk Yard Cửa Lò: phương án 782 “Xúc gạt xe - bãi” ghi 375,46 tấn;
  phương án 852 “Xúc gom bãi” ghi 134,5 tấn. Tổng 509,96 tấn gồm cả gom nội bộ,
  nên không được tự coi toàn bộ là sản lượng qua cổng. Ngoài ra có cân, vệ sinh,
  chụp ảnh, phục vụ xưởng và công thời gian.
- Warehouse: “Nâng Xe - Kho” 926 tấn, “Nâng rút Cont - Kho” 62 tấn,
  “Nâng kho - xe” 60 tấn. Cần đối chiếu phạm vi rút hàng từ cont với đặc tả
  đầu nhận để không tính trùng luồng qua cổng/kho.
- Bulk Yard Bến Thủy: 84,5 tấn nằm trên phương án 424 “Trực thu phí”, còn
  ba dòng là “Trực cân điện tử”. Chưa coi số thu phí là sản lượng xếp dỡ.

Kết luận: vesselTypeCode xác định được nhóm địa điểm kho/bãi của phiếu, tốt hơn
việc lấy mọi phương án không qua cầu. Cần kết hợp loại hàng và phương án để
loại dịch vụ/đảo chuyển ngoài phạm vi báo cáo. Lần này chỉ lọc và đối soát,
chưa mở hai endpoint cổng/bãi hoặc thay số dashboard.

Bằng chứng riêng trong thư mục Git bỏ qua:
`outputs/api-plan-20260921/vessel-types-live.json` và các script
`capture_vessel_types.py`, `analyze_vessel_types.py`, `type_method_details.py`.

## Kiểm tra tiếp đơn vị nguồn ngày 22/09/2026

Đã đọc thêm 72 phiếu kho/bãi từ SQL và danh mục BaseUnit bằng SELECT.
Phát hiện quan trọng: `quantityUnitId=47` là `GIO` (Giờ), còn 52 là `CONT`.
Vì vậy số lượng 6 trên phiếu 312643, mặt hàng “Khác”, là **6 giờ**, không
phải 6 container. Nhận xét trước về 6 dòng Khác/tổng số lượng 6 chưa xác định
đơn vị; không được dùng để quy đổi TEU hay trọng lượng.

42 dòng Container Yard chia thành:

| Nhóm đơn vị | Dòng | Số lượng | Tấn ghi nhận | Tấn null |
|---|---:|---:|---:|---:|
| CONT (52) | 35 | 31 container | 453,25 | 5 dòng |
| GIO (47) | 7 | 6 giờ | Không có | 7 dòng |

35 dòng CONT có 5 dòng Khác số lượng 0, trọng lượng null. Chưa biến null
thành 0 hoặc xác nhận đây là dòng mẫu. Phần xác định được gồm 1 cont 20F,
13 cont 40F, 17 cont 20E: 31 container, 44 TEU và 453,25 tấn. Tất cả 12 dòng
null ban đầu đều không có trọng lượng ở các cột thay thế đã kiểm tra:
weightNetSumTemp, weightTallySum, weightTallyBerthSum, weightTallyWarehouseSum,
cargoWeightNetSum/GrossSum/NetSumFinal và cargoWeightBridgeNetSum.

Quy tắc đang đề xuất cho nhánh container: xác định vị trí bằng vesselTypeCode,
kiểm tra mặt hàng/kích cỡ, rồi xác minh đơn vị số lượng CONT trước khi quy đổi
TEU; không dùng mọi quantityTotalSum như số container. Không áp hệ số TONE của
đơn vị số lượng thay cho đơn vị trọng lượng (CONT trong danh mục đang có TONE=25).
Nhóm GIO là dữ liệu công thời gian, không đưa vào phép tính TEU.

Phạm vi phương án Xúc gom bãi, Nâng rút Cont - Kho và Trực thu phí đã được
hỏi để xác nhận trước khi chốt API. JSON vẫn giữ data/code/message và ID gốc.
Chưa thay số dashboard hay công bố hai endpoint cổng/bãi trong lần đối soát này.
Bằng chứng: yard-details-live.json và yard-units-live.capture trong thư mục
outputs/api-plan-20260921 (không đưa dữ liệu nguồn vào Git).

## Kết quả triển khai nhánh thử nghiệm

Đã bổ sung gate_selection=vessel_type vào adapter nguồn và kiểm tra lại SQL
hai database. Kho/bãi không dùng danh sách phương án để chọn phiếu; phương án
vẫn giữ trong JSON. Đơn vị số lượng lấy trực tiếp từ BaseUnit, không suy từ
mặt hàng; GIO bị loại, container yêu cầu CONT. Khối lượng vẫn đọc weightNetSum
và đổi theo đơn vị trọng lượng, không dùng hệ số của đơn vị số lượng.

Trang local có dữ liệu cả hai API cổng/bãi cho ngày 16/09/2026:

- Container: 6 dòng tổng hợp, 453,25 tấn, 44 TEU. Chưa đầy đủ: 5 dòng thiếu
  trọng lượng, 1 dòng container chung tại Bulk Yard chưa có đơn vị CONT phù hợp.
  7 dòng tính giờ đã được loại khỏi nhánh này.
- Bulk: 9 dòng tổng hợp, 1.642,46 tấn (Cửa Lò 1.557,96; Bến Thủy 84,5).
  Có cả gom bãi 134,5 tấn, rút cont 62 tấn và Trực thu phí 84,5 tấn, đúng với
  phạm vi lấy tác nghiệp theo kho/bãi thay vì giới hạn hàng qua cổng vật lý.

Kiểm thử source/end-to-end: 34 ca đạt; catalog/publication/HTTP: 74 ca đạt.
Sau khi bổ sung chặn Ro-Ro Yard kể cả bị gán phương án qua cầu, chạy lại nhóm
source: 31 ca đạt (nhóm này nằm trong 34 ca trước, không cộng lặp).
HTTP local xác nhận tổng tấn/TEU, cờ chưa đầy đủ ngoài JSON, và body chỉ gồm
data/code/message. Không sửa database nguồn, không đổi KPI dashboard, chưa
push/deploy hoặc công bố cấu hình này cho Tổng công ty.
