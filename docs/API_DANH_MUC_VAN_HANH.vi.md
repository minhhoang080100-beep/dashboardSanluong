# API danh mục vận hành Bulk và Container

Phạm vi đợt này là **20 API danh mục khác nhau**: 17 của Bulk và 3 đường dẫn riêng của Container. Mười danh mục còn lại của Container dùng chung đường dẫn với Bulk. Dùng tài khoản máy và `/api/login` hiện có; không tạo cơ chế đăng nhập riêng cho từng bộ.

Các API nghiệp vụ chuyến tàu, phiếu tác nghiệp, tồn kho, thiết bị hoạt động, kế hoạch tác nghiệp và doanh thu không nằm trong đợt danh mục này. RORO không được triển khai.

[Kết quả rà soát từng API mới nhất](RA_SOAT_TUNG_API_VAN_HANH_20260925.vi.md) ghi rõ số bản ghi, trường thiếu, loại chênh lệch ID và việc cần xử lý cho cả 20 danh mục. Tên nhân viên đã sửa sang `employeeFullName`; chẩn đoán kiểm tra mọi lỗi trên mỗi dòng và phân biệt thiếu thuộc tính với khác giá trị. Báo cáo đối soát cũng kiểm tra các danh mục được tham chiếu trước khi kết luận sẵn sàng.

Cập nhật lượt xử lý tiếp theo ngày 25/09: `serviceType` đã sửa sang nguồn `PortServiceType`, lấy đủ **10 loại dịch vụ** và kiểm tra thành công trên đường dẫn chính thức của server local. Các danh mục còn lại tiếp tục giữ điều kiện kiểm tra dữ liệu trước khi công bố. Chi tiết nguồn, kết quả và giới hạn tại [Xử lý API tiếp theo](XU_LY_API_TIEP_THEO_20260925.vi.md).

## Danh sách

[Rà soát tổ và nhân sự khai thác](RA_SOAT_TO_VA_NHAN_SU_KHAI_THAC_20260925.vi.md) xác nhận phạm vi gồm cả tổ hỗ trợ và quan hệ `Employee.organizationMainId → Organization.organizationId`. Đã [triển khai adapter có scope tổ và màn hình đối chiếu local](TRIEN_KHAI_TO_VA_NHAN_SU_20260925.vi.md), không lọc riêng loại 8. Màn hình đọc được nhân sự thật; hai API công khai vẫn chặn khi ngày/trạng thái chưa đủ.

[Bổ sung nguồn và hợp nhất container](BO_SUNG_NGUON_VA_HOP_NHAT_CONTAINER_20260925.vi.md) ghi kết quả kiểm tra quan hệ tổ đội/thiết bị/container, cơ chế TEU tùy chọn đang chờ xác nhận và bản sửa kiểm tra độ mới của danh mục liên quan.

Tất cả đường dẫn dưới đây là `GET`. Tên quyền là khóa dùng trong `--resource`, không phải mã đối tượng trả về.

| Danh mục | Đường dẫn | Tên quyền | Bộ sử dụng |
|---|---|---|---|
| Thiết bị Bulk | `/api/oprt/catalog/portEquipment` | `oprt.portEquipment` | Bulk |
| Loại thiết bị | `/api/oprt/catalog/portEquipType` | `oprt.portEquipType` | Bulk |
| Kho, bãi Bulk | `/api/oprt/catalog/portWHYard` | `oprt.portWHYard` | Bulk |
| Loại kho, bãi | `/api/oprt/catalog/portWHYardType` | `oprt.portWHYardType` | Chung |
| Cầu, bến | `/api/oprt/catalog/berths` | `oprt.berths` | Chung |
| Loại tác nghiệp | `/api/oprt/catalog/jobType` | `oprt.jobType` | Chung |
| Phương án tác nghiệp | `/api/oprt/catalog/jobMethod` | `oprt.jobMethod` | Chung |
| Phương thức giao nhận | `/api/oprt/catalog/deliveryMethod` | `oprt.deliveryMethod` | Chung |
| Loại dịch vụ | `/api/oprt/catalog/serviceType` | `oprt.serviceType` | Chung |
| Mặt hàng | `/api/oprt/catalog/cargoItems` | `oprt.cargoItems` | Bulk |
| Nhóm hàng | `/api/oprt/catalog/cargoGroups` | `oprt.cargoGroups` | Bulk |
| Đơn vị tính | `/api/oprt/catalog/unitMeasurement` | `oprt.unitMeasurement` | Bulk |
| Hướng hàng | `/api/oprt/catalog/cargoDirect` | `oprt.cargoDirect` | Chung |
| Loại vị trí tác nghiệp | `/api/oprt/catalog/operationLocationType` | `oprt.operationLocationType` | Bulk |
| Đội tác nghiệp | `/api/oprt/portOpTeam` | `oprt.portOpTeam` | Chung |
| Nhân viên tác nghiệp | `/api/oprt/portOpStaff` | `oprt.portOpStaff` | Chung |
| Loại tàu | `/api/oprt/catalog/vesselType` | `oprt.vesselType` | Chung |
| Thiết bị Container | `/api/oprt/catalog/equipments` | `oprt.equipments` | Container |
| Kho, bãi Container | `/api/oprt/catalog/contwhYards` | `oprt.contwhYards` | Container |
| Kích cỡ, loại Container | `/api/oprt/catalog/contSizeType` | `oprt.contSizeType` | Container |

Thiết bị, kho bãi và kích cỡ container có DTO riêng theo từng bộ. Không lấy payload của `/api/containerSize` hoặc `/api/handlingMethodList` thuộc bộ S rồi đổi tên đường dẫn thành bộ vận hành.

## Cách lấy dữ liệu

1. Đăng nhập bằng tài khoản máy có quyền trên `CNT` và đúng tài nguyên.
2. Gọi đường dẫn danh mục với ba tham số bắt buộc `companyId=CNT`, `startDate`, `endDate`. Ngày lọc có dạng `yyyyMMdd`, từ `19700101` trở đi; truyền `page=1`, `limit` trong khoảng 1–100 và Bearer token. Ví dụ `/api/oprt/catalog/vesselType?companyId=CNT&startDate=20260901&endDate=20260930&page=1&limit=100`.
3. Đọc `X-Snapshot-Id`, `X-Total-Count`, `X-Total-Pages`, `X-Has-Next` trong header. Từ trang 2, truyền lại `snapshotId` của trang 1 để giữ nguyên phiên dữ liệu khi tác vụ nền đang cập nhật.
4. Khi phiên đã hết hạn, lấy lại từ trang 1. Không nối các trang thuộc hai phiên khác nhau.

Body trả về theo quy ước đã chọn cho dự án:

```json
{"data": [], "code": "1", "message": "Lấy dữ liệu thành công"}
```

Thông tin phân trang và thời điểm đọc nguồn đặt trong header; không thêm `sourceDatabase` hoặc dữ liệu chẩn đoán nội bộ vào JSON công khai. ID trả về giữ nguyên ID nguồn, không có tiền tố `CNT-CL-...` hay `CNT-BT-...`.

Bản ghi xóa mềm vẫn được giữ cùng cờ `isDeleted`. Bộ lọc kỳ là kỳ **tạo/sửa danh mục**, không phải ngày sản lượng, cũng không phải ảnh chụp danh mục lịch sử tại cuối kỳ. Một dòng được lấy khi **ngày tạo hoặc ngày sửa** nằm trong khoảng, tính cả hai ngày đầu/cuối theo giờ Việt Nam. Dòng tạo trong kỳ nhưng sửa sau kỳ vẫn được lấy. Nếu còn dòng không có cả hai ngày, yêu cầu lọc kỳ trả lỗi thay vì âm thầm bỏ dòng đó. `reportDate` của payload vận hành dùng `yyyy-MM-dd`, khác định dạng `yyyyMMdd` của tham số lọc.

Đặc tả danh mục B/C hiện chỉ mô tả lấy danh sách. Không tự mở đường dẫn `/{id}` hay phương thức POST/PUT/DELETE khi chưa có hợp đồng cho chúng.

## Chuẩn bị và công bố

API đọc bản dữ liệu đã công bố trong kho export. Một yêu cầu `GET` không truy vấn trực tiếp SQL Server. Tác vụ riêng thực hiện SELECT nguồn, kiểm tra schema, chuyển đổi, đối chiếu rồi công bố nguyên tử.

Tài khoản cũ không tự có thêm quyền vận hành. Người vận hành dùng CLI `corporate_api.manage_clients` để cấp từng tài nguyên cần thiết. Mật khẩu được nhập ở lời nhắc kín; không ghi mật khẩu/token trong lệnh, tài liệu hoặc file kết quả.

Ví dụ cấp tài khoản chỉ được đọc loại tàu:

```powershell
python -m backend.corporate_api.manage_clients create --username tct-vessel-reader --company CNT --resource oprt.vesselType
```

Lệnh `manage_exports extract` và `sync` có `--domain operations` để chọn 20 danh mục này; `--domain all` chọn cả bộ S và vận hành. Mặc định `production` giữ phạm vi bộ S hiện có, để các tác vụ cũ không tự mở rộng quyền hoặc truy vấn thêm dữ liệu. Dùng `--resource oprt.<tên>` khi chỉ cần một danh mục. Lệnh `manage_exports publish` lấy danh mục từ file preview hoặc các `--resource` đã chỉ rõ; không truyền `--domain` cho lệnh publish.

Chỉ công bố bản đọc đã được đối chiếu và profile đã duyệt. Việc kiểm tra thất bại giữ nguyên phiên đang phục vụ; không đưa một phần kết quả thiếu thành công khai. Bộ kiểm tra khóa tham chiếu ngăn xóa một mã danh mục đang được dữ liệu đã công bố sử dụng.

Ví dụ đọc đủ danh mục vận hành từ thư mục gốc dự án, dùng profile riêng đã chuẩn bị tại `backend/.data/corporate-profile.json`:

```powershell
python -m backend.corporate_api.manage_exports extract --domain operations --profile backend/.data/corporate-profile.json --start 2026-01-01 --end 2026-12-31 --output outputs/oprt-preview.json --report outputs/oprt-review.json
```

Lệnh in số dòng, trạng thái và lỗi từng danh mục; đọc thêm file `outputs/oprt-review.json` để đối chiếu. Danh mục được chụp **toàn bộ bảng nguồn**, kể cả xóa mềm; khoảng ngày truyền ở bước này ghi nhận kỳ yêu cầu, không cắt mất các dòng ngoài kỳ. Bộ lọc tạo/sửa thực hiện khi gọi GET. Vì không có API sản lượng trong lệnh trên, khoảng một năm được chấp nhận; giới hạn 31 ngày vẫn áp dụng khi chọn API sản lượng S.

Khi profile và toàn bộ danh mục trong preview đã đủ điều kiện, công bố rồi kiểm tra trạng thái:

```powershell
python -m backend.corporate_api.manage_exports publish --profile backend/.data/corporate-profile.json --input outputs/oprt-preview.json
python -m backend.corporate_api.manage_exports status
```

Có thể chỉ rõ `--resource oprt.vesselType` khi công bố một danh mục đã đủ điều kiện; các khóa tham chiếu vẫn được kiểm tra. Profile mẫu giữ `approved: false`, không phải cấu hình đã được duyệt. Không sửa cờ `ready` hoặc xóa `blockers` trong preview để vượt kiểm tra. Nếu sửa profile sau khi trích xuất, cần đọc lại ra file preview mới vì mã kiểm tra cấu hình phải khớp. Các file output được tạo mới, không ghi đè file đối soát đã có.

## Nguồn dữ liệu và trạng thái xác minh ngày 25/09/2026

Lần khảo sát đầu gặp timeout. Trong lượt kiểm tra tiếp theo ngày 25/09, đã kết nối thành công cả hai database, xác minh cấu trúc 18 bảng mỗi nguồn và đọc đủ 16 bảng danh mục mỗi nguồn (32 lượt đọc bảng thành công). Bản đọc mới và tình trạng từng API được ghi trong [Rà soát API ngày 25/09](RA_SOAT_API_20260925.vi.md). Việc đọc nguồn thành công chưa đồng nghĩa đủ điều kiện công bố: còn ID trùng khác dữ liệu, ngày tạo/sửa thiếu và trường bắt buộc chưa có nguồn.

Ánh xạ mặc định nằm trong `operation_source.py`. Các bảng mặc định đã được kiểm tra cấu trúc ngày 25/09 bằng mô tả cột ODBC của truy vấn `SELECT TOP (0)`; Gang và Employee chưa đọc dữ liệu chi tiết. Cấu trúc tồn tại không chứng minh đã đủ cột hay đúng ý nghĩa nghiệp vụ. Trước khi SELECT dữ liệu, adapter kiểm tra lại bảng, tên cột, kiểu dữ liệu và giới hạn số dòng.

Profile có mục `operation_sources: {}`. Mỗi tài nguyên có thể cấu hình `table`, `columns`, `value_maps` sau khi đã xác minh và duyệt profile. Chỉ chấp nhận bảng trong danh sách cho phép, tên cột hợp lệ và trường thuộc hợp đồng; không nhận đoạn SQL tùy ý. `value_maps` hỗ trợ chuyển giá trị thuộc tính đã có quy tắc, **không được thay ID nguồn**, mã công ty hoặc tự tạo đối tượng.

Trường danh sách ID chỉ nhận phép chuyển đổi `transforms: {"<tên trường>": "json_array"}`
khi cột nguồn đã được xác minh là chuỗi JSON mảng. Không tự tách mã theo dấu phẩy
hay đổi giá trị ID. Tên tài nguyên viết sai trong `operation_sources` bị từ chối.

Một số điều kiện đang được chặn rõ ràng trong bộ đọc nguồn:

- Thiết bị chưa có ánh xạ các trường bắt buộc như số sê-ri, đăng ký và trạng thái thuê; thiết bị Container còn có các thuộc tính kỹ thuật bắt buộc riêng.
- `equipmentTypeId` đã ánh xạ vào thiết bị Bulk nhưng cả 80 dòng thiết bị của hai nguồn đang null. `warehouseTypeId` đã ánh xạ vào cả hai danh mục kho/bãi; không tự tạo loại kho khi nguồn dùng giá trị 0.
- Mặt hàng chưa có bằng chứng cho cờ `dangerousGoodsCheck`; không suy hàng nguy hiểm từ tên hàng.
- Đội/nhân viên chưa có ánh xạ đủ trạng thái, nhân viên còn cần quan hệ đội bắt buộc theo hợp đồng; không coi cờ đang hoạt động bất kỳ là trạng thái lao động.
- Dòng thiếu cả ngày tạo và sửa làm danh mục chưa sẵn sàng ngay ở bước trích xuất. Kho export cũng kiểm tra ngày khi đọc để không bỏ sót dòng từ dữ liệu cũ.
- Thiếu nguồn một xí nghiệp, thiếu schema, ID xung đột hoặc vượt giới hạn đọc đều chặn công bố danh mục liên quan; không trả phần dữ liệu đọc được như thể đã đủ toàn công ty.

Độ sâu cầu/bến giữ giá trị có dấu của nguồn (ví dụ `-13 m`); không dùng ràng buộc số không âm của chỉ tiêu sản lượng cho thuộc tính này. Bộ đọc tổng hợp mọi lỗi theo trường qua tất cả dòng, giữ số lượng nguồn/hợp lệ/lỗi, số lỗi riêng và tối đa 10 chỉ số dòng/ID số hoặc UUID mỗi nhóm lỗi trong báo cáo riêng. Dữ liệu chưa xác định trả số lượng `null`, khác với nguồn đã đọc thành công có 0 dòng. JSON công khai không chứa phần chẩn đoán này.

Các kết quả phát lại từ bản đọc 22–23/09, nếu có, chỉ chứng minh adapter xử lý được bằng chứng đã lưu. Chúng không thay thế truy vấn mới, không chứng minh dữ liệu hiện tại đầy đủ và không tự kích hoạt công bố lên Railway.

Đã phát lại bản đọc ngày 23/09 bằng adapter mới: `jobMethod` có 1.037 dòng Cửa Lò
và 660 dòng Bến Thủy; `cargoGroups` có 48 và 43 dòng, bao gồm bản ghi xóa mềm.
Hai danh mục xử lý được riêng từng nguồn nhưng việc gộp toàn công ty bị chặn
lần lượt bởi 354 và 14 ID trùng có nội dung khác nhau. `cargoDirect` bị chặn vì
có dòng thiếu cả ngày tạo và ngày sửa. Các danh mục không có đủ bằng chứng cũ
được ghi là chưa xác minh, không kết luận bảng không tồn tại trong SQL.
Chi tiết nội bộ: `outputs/oprt-catalog-20260925/historical-replay.json`.

Các nhóm kiểm thử liên quan đã chạy thành công: 188 kiểm thử bộ S, 55 hợp đồng
vận hành, 63 HTTP/công bố vận hành, 32 đọc nguồn và 5 lựa chọn đồng bộ/công bố
(343 bài). Đây là kết quả chạy theo nhóm với dữ liệu kiểm thử; phát lại bằng
chứng SQL cũ được báo riêng ở trên.

## Quy tắc dữ liệu và giới hạn

Profile mẫu có `operation_metadata_merge: {}`, mặc định không gộp ID chỉ khác ngày. Tính năng tùy chọn `source_date_variants_v1` cần được duyệt riêng theo tài nguyên; chỉ hợp nhất khi toàn bộ thuộc tính nghiệp vụ giống nhau và giữ các cặp ngày nguồn để lọc kỳ không mất dữ liệu. Chưa bật quy tắc này trong cấu hình thật. Xem điều kiện và ví dụ trong tài liệu cập nhật ở trên.

- Dùng mã công ty `CNT` như cấu hình hiện có; không tự tạo mã Cửa Lò/Bến Thủy trong hợp đồng công khai.
- Khi hai database có cùng ID nhưng nội dung khác nhau, dừng công bố danh mục bị xung đột. Chưa có quy định của Tổng công ty để tự đổi mã hoặc chọn một bên.
- Chỉ hợp nhất cùng ID khi nội dung đối tượng tương thích theo bộ kiểm tra; không gộp chỉ vì tên giống nhau.
- Không suy đoán bảng nguồn từ tên gần giống, không điền giá trị giả cho thuộc tính kỹ thuật, trạng thái hay ngày chưa có bằng chứng.
- Việc khai báo trường nullable trong mô hình giúp mô tả dữ liệu thiếu, không thay thế xác nhận của Tổng công ty về việc chấp nhận null.
- Kiểm thử HTTP với dữ liệu tổng hợp chỉ xác nhận hợp đồng, phân quyền, phân trang và cơ chế công bố. Nó không xác nhận số lượng thực tế trong SQL, độ đầy đủ dữ liệu, hay việc endpoint đã được triển khai lên Railway.

Đặc tả tham chiếu: `API Vận Hành/API_Domain_VanHanh_CB_Bulk (Spec)_v1.5.xlsx` và `API Vận Hành/API_Domain_VanHanh_CB_Cont (Spec)_v1.5.xlsx`. Các điểm khác nhau giữa bảng trường và ví dụ trong Excel được ghi nhận ở hợp đồng mã nguồn; không dùng dữ liệu minh họa làm dữ liệu thật.

## Cách xử lý điểm không thống nhất trong đặc tả

`operation_contracts.py` ghi rõ các quyết định sau để có thể đối chiếu với bên nhận:

- Ưu tiên bảng trường, bỏ các trường có dấu gạch bỏ. Không chép các trường chỉ xuất hiện trong ví dụ cũ.
- Khóa của danh mục loại kho/bãi là `whTypeId`; trường `whYardTypeId` của kho/bãi tham chiếu khóa này.
- `cargoGroupName` bị lặp trong bảng đặc tả chỉ xuất hiện một lần. Không tự tạo thêm `cargoGroupShortName`. `cargoItems` và `cargoGroups` không có `isUpdated` vì bảng trường không yêu cầu.
- `operationLocationTypeId` của thiết bị Bulk là danh sách ID theo phần mô tả, dù ví dụ dùng một giá trị. `workPerHour` chưa bị gạch trong bảng nhưng có bình luận đề nghị bỏ; giữ là trường tùy chọn, không suy ra giá trị.
- Các cờ và ngày thay đổi của kho/bãi Container nằm ở cấp bản ghi theo bảng trường; không thêm cấu trúc `Metadata` hay `rowguid` từ ví dụ không khớp.
- Không tự suy ra bản ghi đã xóa cứng khỏi SQL là một tombstone hợp lệ: phải có nhật ký xóa hoặc nguồn theo dõi thay đổi để xác nhận. API hiện có thể truyền cờ xóa mềm đã có trong nguồn.

## Phạm vi đã kiểm thử

Cập nhật 27/09: đã rà tiếp cơ chế lọc ngày, đối soát, cấu hình nguồn và công bố. Bộ corporate đạt 871 bài; nhóm sync/publication/freshness sau bản sửa `--check-only` đạt 72 bài. Kiểm tra 32 đường dẫn bằng HTTP trong tiến trình, không mở server. Xem [báo cáo ngày 27/09](RA_SOAT_API_20260927.vi.md) để phân biệt kết quả kiểm thử, tuổi bản đọc và điều kiện nguồn còn thiếu. Phần dưới là bằng chứng của lượt 25/09.

Lượt kiểm tra mới nhất ngày 25/09: `python -m pytest tests -q -k corporate` đạt **656 bài**, 603 bài ngoài phạm vi không chạy. Đã gọi cả 20 đường dẫn vận hành trên local; chỉ `serviceType` có bản công bố hợp lệ, 19 mục còn lại tiếp tục báo 503. Màn hình tổ–nhân sự đối chiếu riêng đã đọc đúng 612/554 hồ sơ nguồn với kiểm tra phân quyền, chưa công bố thành API tổ/nhân sự. Bộ đối chiếu mới hiển thị từng trường của 13 ID tổ và 275 ID nhân sự khác nhau; bản sửa cũng từ chối cấu hình nguồn rỗng/sai và boolean bị hiểu nhầm thành trạng thái nghiệp vụ. Chi tiết xem [triển khai tổ và nhân sự](TRIEN_KHAI_TO_VA_NHAN_SU_20260925.vi.md). Đã kiểm tra phương án container bằng dữ liệu thật trong kho tạm: đủ 740 ID qua 8 trang và 8 kỳ ngày nguồn, chưa bật lên API đang phục vụ. Kiểm tra trước đó với bản đọc SQL thật của `serviceType` trả đủ 10 ID qua 4 trang; lọc ngày tạo trả 3 dòng, ngày sửa trả 10 dòng, kỳ không phát sinh trả 0 dòng. Đây là kiểm tra local; chưa triển khai bản cập nhật này lên Railway.

`tests/test_corporate_operation_api.py` kiểm tra cả 20 đường dẫn và quyền tài nguyên, dữ liệu khác công ty bị từ chối trước khi đọc export, phân trang qua nhiều trang khi có bản công bố mới, lọc ngày tạo **hoặc** sửa và múi giờ Việt Nam, giữ dòng xóa mềm, báo lỗi khi thiếu ngày, dữ liệu quá cũ, xung đột ID và tính nguyên tử khi công bố. Kiểm thử cũng ngăn thay danh mục làm mất khóa đang được danh mục khác tham chiếu.

Chạy từ thư mục gốc dự án:

```powershell
python -m pytest tests/test_corporate_operation_api.py -q
```

Bộ kiểm thử dùng tài khoản và dữ liệu tổng hợp trong thư mục tạm. Kết quả kiểm thử này không phải biên bản đối soát dữ liệu SQL thật. Kết quả khảo sát nguồn được lưu riêng trong `outputs/oprt-catalog-20260925`; triển khai Railway và mở quyền cho bên nhận là các bước vận hành riêng.

## Kiểm tra lỗi

| HTTP | Ý nghĩa |
|---|---|
| 401 | Thiếu hoặc sai token máy |
| 403 | Tài khoản không có quyền công ty/tài nguyên |
| 409 | Thiếu `snapshotId` ở trang tiếp theo, hoặc danh mục liên quan của phiên cũ đã đổi; lấy lại từ trang 1 |
| 410 | Phiên dữ liệu hết hạn hoặc không thuộc tài nguyên yêu cầu |
| 422 | Tham số không hợp lệ, bộ lọc không hỗ trợ |
| 503 | Chưa có bản công bố, dữ liệu quá cũ, nguồn/bộ lọc chưa đủ điều kiện |

Đọc `X-Error-Code` để phân biệt các lỗi cùng HTTP status. Một danh mục chưa công bố trả lỗi rõ ràng, không trả thành công với `data: []` để che việc thiếu nguồn.
