# API sản lượng S — triển khai CNT

Nguồn hợp đồng: `API Vận Hành/API Domain SLG_CB (Spec)_v3.xlsx`.
Phạm vi triển khai ngày 21/09/2026: **13 API chính, không triển khai RORO**.
API phục vụ cách Tổng công ty chủ động gọi GET; không tự gửi POST dữ liệu sang hệ thống khác.

Ngày 25/09/2026 bổ sung danh mục B/C dưới `/api/oprt/` thành hợp đồng riêng;
xem [API_DANH_MUC_VAN_HANH.vi.md](API_DANH_MUC_VAN_HANH.vi.md). Các endpoint S và
lựa chọn đồng bộ mặc định không thay đổi khi thêm các danh mục vận hành.

## 1. Trạng thái và giới hạn kiểm chứng

Rà soát tài liệu ngày 21/10/2026. API dùng chế độ `live`, truy vấn SmartTOS trong
từng yêu cầu đã xác thực. Không cần trích xuất hoặc công bố dữ liệu trước.
Địa chỉ API riêng và Swagger: [API_RIENG_RAILWAY.vi.md](API_RIENG_RAILWAY.vi.md).

MISBI là số chuẩn; dữ liệu Lakehouse từ API cũ là dữ liệu cần kiểm tra sai lệch.
Đã nhận MISBI theo tháng cho kỳ đối chiếu, phạm vi cả Cửa Lò và Bến Thủy, loại
Cầu 5. Ngày hạch toán/kỳ chốt chưa rõ và số liệu chưa khớp hoàn toàn.
**Chưa nghiệm thu tổng với MISBI hoặc xác nhận Lakehouse nhận đúng dữ liệu.**

Số liệu nhận được của một phần kỳ không được coi là tổng đầy đủ khi API còn
lỗi dữ liệu nguồn. Lọc một tập con hợp lệ, đọc một kỳ thành công hoặc vượt qua
kiểm thử mã không chứng minh mọi API và toàn bộ lịch sử đã sẵn sàng. Nhật ký
kiểm chứng và các bảng đối soát chứa dữ liệu vận hành được giữ riêng ngoài Git.

Mã nguồn vẫn hỗ trợ `published` tùy chọn. Các lệnh công bố/lịch đồng bộ chỉ
dùng khi chủ động chọn chế độ đó. Xem [hướng dẫn đọc trực tiếp](API_TRUC_TIEP_SMARTTOS.vi.md)
và [hướng dẫn lấy đủ kỳ, kiểm tra danh mục và bàn giao](VAN_HANH_API_TONG_CONG_TY.vi.md).

`CORPORATE_API_ENABLED` mặc định tắt. Các ánh xạ còn trống trong
`backend/corporate_api/profile.example.json` là thông tin cần xác định, không
phải danh mục rỗng của công ty. Không chuyển `approved` thành `true` để bỏ lỗi.

## 2. Đường dẫn

| Method | Path | Dữ liệu |
|---|---|---|
| POST | `/api/login` | Tài khoản máy, lấy Bearer token |
| POST | `/api/logout` | Tiện ích kiểm tra API: thu hồi token máy hiện tại, không đổi phiên dashboard hoặc token khác |
| GET | `/api/contQuayVolumesCB` | Tấn và TEU container qua cầu |
| GET | `/api/contGateVolumesCB` | Tấn và TEU container tại cổng/kho bãi theo loại địa điểm hoặc phương án được cấu hình |
| GET | `/api/bulkQuayVolumesCB` | Tấn các loại hàng thuộc phạm vi Bulk qua cầu |
| GET | `/api/bulkGateVolumesCB` | Tấn Bulk tại cổng/kho bãi theo loại địa điểm hoặc phương án được cấu hình |
| GET | `/api/shipDetails` | Tàu vật lý |
| GET | `/api/customers` | Khách hàng và trạng thái xóa |
| GET | `/api/cargoType` | Nhóm hàng nguồn theo cấu hình `native_groups` |
| GET | `/api/cargoCategory` | Mặt hàng nguồn và quan hệ nhóm hàng |
| GET | `/api/handlingMethodList` | Phương án tác nghiệp nguồn |
| GET | `/api/class` | Hướng hàng từ CargoDirect |
| GET | `/api/origins` | Nguồn gốc hàng từ CargoOrigin |
| GET | `/api/containerSize` | Mã hàng container gốc hoặc kích cỡ theo chế độ đã xác minh |

Bảy danh mục đầu từ `shipDetails` đến `origins` có GET `/{record_id}`. Kết quả
chi tiết vẫn dùng `data` là mảng một phần tử. `containerSize` chưa thêm đường dẫn
chi tiết vì ví dụ trong Excel chép sang `cargoCategory`, không phải đặc tả rõ ràng.
Không triển khai POST cho các danh mục chỉ có mô tả GET và không có request body.

OpenAPI của FastAPI mô tả các kiểu dữ liệu và đường dẫn tại `/docs`. Hai nhóm đăng
nhập tách biệt: `/api/auth/login` dành cho người dùng dashboard, `/api/login` dành
cho hệ thống Tổng công ty. Token của hai nhóm không dùng thay cho nhau.

## 3. Quy ước hợp đồng CNT cần thống nhất với bên nhận

Đây là hồ sơ triển khai **S-v3-CNT-1**, không khẳng định những chỗ chưa thống nhất
trong Excel đã được Tổng công ty chấp thuận.

| Điểm trong Excel | Quy ước triển khai |
|---|---|
| `accessToken` / `AccessToken`, ví dụ `8h` không hợp lệ trong JSON | `accessToken`, `expiresIn` là số giây, mặc định 28.800 |
| ApiKey có ghi bắt buộc nhưng mô tả “nếu có” | Bearer token từ tài khoản máy riêng; không sử dụng Basic hoặc ApiKey |
| Production nhắc cả finishDate lẫn update_time | Lọc ngày ghi nhận sản lượng; chỉ cho ánh xạ `shiftDate` sau khi xác nhận đây là ngày báo cáo cần dùng |
| Customers lọc theo `createdDate` trong bộ S v3 | Khi truyền kỳ: chỉ lọc ngày tạo nguồn trong `metadata.createdDate`; thiếu hoặc sai ngày tạo trả `FILTER_NOT_READY`, không lấy ngày sửa thay thế |
| ID cùng số có thể nằm ở hai xí nghiệp | Mặc định `native` giữ chuỗi ID gốc và chặn xung đột khi gộp. Profile có thể chọn `source_identity_mode="terminal_prefixed_v1"` để trả `CL:<ID>` hoặc `BT:<ID>` đồng bộ trong danh mục S và sản lượng; xem quy tắc chuyển đổi bên dưới. |
| Mẫu chỉ có `data`, `code`, `message` | JSON giữ đúng ba trường; thông tin phân trang chuyển sang HTTP headers, không thêm thuộc tính vào payload |
| Các trường phản hồi không đánh dấu bắt buộc rõ | Trường chưa có nguồn được phép null theo schema hiện tại và được nêu trong cảnh báo; cần bên nhận xác nhận tính tương thích |

Tất cả GET yêu cầu `companyId=CNT`. Tài khoản phải được cấp quyền công ty và đúng
resource. Tên trường và URL phân biệt hoa/thường.
Các cờ `isCarrier`, `isAgent`, `metadata.isDeleted` xuất dạng số 0/1; hai cờ vai
trò khách hàng giữ null nếu chưa xác định được từ nguồn.

Production bắt buộc `startDate`, `endDate` dạng `yyyyMMdd`, gồm cả hai ngày.
Quay nhận thêm `shipId`, `handlingMethodId`; gate chỉ nhận `handlingMethodId`.
`cargoCategory` nhận `cargoTypeId`; `containerSize` nhận `containerSizeId`.
`origins` nhận `origin`: tìm chuỗi trong `originName`, không phân biệt hoa/thường,
giữ dấu tiếng Việt. Chuỗi sau trim dài 1–254 ký tự; `%` và `_` là ký tự thường,
không phải wildcard. Ví dụ `origin=Nội` khớp `Nội địa`. Excel không quy định
so sánh chính xác hay chuỗi con; đây là ngữ nghĩa triển khai được công khai.
Customers nhận cặp ngày tùy chọn, `customerTaxCode`, `customerType`. Bộ lọc không
áp dụng cho resource bị từ chối, không bị âm thầm bỏ qua.

Trong chế độ `native`, tra cứu danh mục theo ID kiểm tra đúng ID đó trong cả hai nguồn. Một mã khác
bị trùng không chặn ID đang tra cứu; chính ID đang tra cứu có nội dung xung
đột vẫn trả `503 SOURCE_ID_CONFLICT`. Không tồn tại ID trả `404 NOT_FOUND`,
khác với danh sách có bộ lọc hợp lệ nhưng không khớp dòng nào (`200`, mảng rỗng).
Các bộ lọc tên/nhóm/khách hàng chọn tập ID rồi kiểm tra mọi biến thể của các ID
đó ở hai nguồn, tránh che giấu xung đột bằng bộ lọc. Lấy toàn danh mục vẫn bị
chặn nếu còn ID trùng khác nội dung. Tham chiếu mặt hàng/nhóm hàng được kiểm
tra trong đúng nguồn phát sinh, không dùng mã cùng số của nguồn khác để bù.

### ID phân biệt nguồn của bộ S

Bản cập nhật mã ngày 22/10/2026 hỗ trợ lựa chọn
`source_identity_mode="terminal_prefixed_v1"`; **chưa xác nhận lựa chọn này
đã được kích hoạt trên production**. Bỏ khóa này hoặc đặt `"native"` giữ hành
vi cũ. Trong chế độ mới, `CL` là Cửa Lò, `BT` là Bến Thủy; ví dụ giả lập
`CL:123` và `BT:123` là hai định danh khác nhau dù phần ID nguồn cùng bằng `123`.

- Áp dụng cho cả tám danh mục S: `shipDetails`, `customers`, `cargoType`,
  `cargoCategory`, `handlingMethodList`, `class`, `origins`, `containerSize`.
  ID đối tượng và tham chiếu giữa các danh mục dùng cùng tiền tố, gồm quan hệ
  mặt hàng–nhóm hàng và hàng cha.
- Bốn API sản lượng dùng cùng ID có tiền tố cho các khóa tham chiếu có giá trị.
  Không gộp hai đối tượng khác nguồn chỉ vì ID số giống nhau. Tổng tấn/TEU,
  công thức, phạm vi, `companyId=CNT` và chính sách null giữ nguyên; số dòng
  tổng hợp có thể tăng vì hai nguồn được phân biệt.
- Tra cứu `/{record_id}` và các bộ lọc ID phải giữ nguyên ID API, ví dụ
  `shipId=CL:123`. Không bỏ tiền tố, ép thành số, đổi hoa/thường hoặc thêm số 0
  phía trước. Khi bật chế độ mới, ID không có tiền tố hoặc sai cú pháp bị từ chối
  trước khi đọc nguồn. ID hợp lệ chỉ tra đúng nguồn được mã hóa.
- ID và khóa ngoại trong SmartTOS không bị sửa. Các allowlist, ánh xạ phân loại
  và quy đổi trong profile vẫn dùng ID gốc theo `cua_lo`/`ben_thuy`; SQL nhận
  tham số ID gốc. Không thêm trường `sourceDatabase` vào JSON.
- Chế độ này dùng `cargo_catalog_source="native_groups"` và kích cỡ
  `native_cargo` hoặc `native_domestic` có quan hệ đã xác minh. Bộ danh mục vận
  hành `/api/oprt/` không đổi định danh theo lựa chọn này.

Triển khai mã hỗ trợ trước khi đổi profile. Bên nhận cần nạp lại danh mục và
sản lượng cùng chế độ, giữ nguyên chuỗi ID trong khóa/quan hệ và thay thế đúng
phân vùng đã chuyển đổi; không nối dữ liệu mới vào phân vùng còn ID `native`.
Xem [quy trình chuyển định danh](VAN_HANH_API_TONG_CONG_TY.vi.md#chuyển-id-bộ-s-sang-phân-biệt-nguồn).

Swagger chỉ liệt kê bộ lọc áp dụng cho từng endpoint, đánh dấu hai ngày bắt
buộc của sản lượng, mô tả lỗi theo `data/code/message` và các header phân trang.
Ở service live, Swagger ẩn `snapshotId`; truyền thủ công vẫn bị từ chối như
trước. Trong chế độ published, tham số này tiếp tục được hỗ trợ.

Bộ đọc áp bộ lọc tàu/phương án của sản lượng trước kiểm tra phiếu,
tổng hợp và tìm danh mục tham chiếu. Phiếu lỗi ngoài tàu/phương án được chọn
không chặn riêng tập con đó; phiếu lỗi bên trong vẫn bị chặn. ID so đúng chuỗi
API của chế độ đã chọn, không tự chuyển `001` thành `1` hoặc bỏ `CL:`/`BT:`.
Không thêm bộ lọc con để coi một phần dữ
liệu là tổng toàn kỳ. SQL truyền các bộ lọc ID bằng tham số, áp trước giới
hạn dòng của lần đọc phiếu; lịch sử cầu vẫn xét toàn bộ lịch sử của các chuyến
được chọn. Không cam kết thời gian truy vấn cố định cho mọi kỳ.

Khi kết quả sản lượng đã lọc rỗng ở chế độ live, API kiểm tra mã tàu/phương án
trong cả hai nguồn ở chế độ `native`, hoặc đúng nguồn trong ID có tiền tố,
bằng tối đa hai SELECT ngắn. Mã có thật nhưng không phát
sinh trong kỳ trả `200` với mảng rỗng; mã không tồn tại trả `422` với header
`X-Error-Code: FILTER_ID_NOT_FOUND`. Nếu không đọc đủ các nguồn cần tra, trả `503`,
không kết luận mã không tồn tại. Yêu cầu có dữ liệu hoặc không truyền mã lọc
không thực hiện bước tra cứu bổ sung này.

ID trên đường dẫn chi tiết của `cargoType`, `cargoCategory`,
`handlingMethodList`, `class`, `origins` dài tối đa 254 ký tự, theo điều kiện
nhỏ hơn 255 trong Excel. `shipDetails` và `customers` giữ tối đa 255 ký tự.

Mỗi yêu cầu sản lượng tối đa 31 ngày, tính cả ngày đầu/cuối. Mặc định `page=1`,
`limit=20`; tối đa `limit=100`. Ở chế độ **live**, giữ nguyên bộ lọc khi chuyển
trang, không truyền `snapshotId`. Mỗi trang đọc nguồn mới; nguồn thay đổi có
thể làm số dòng hoặc số liệu thay đổi giữa các trang. Dùng các header `X-Page`,
`X-Limit`, `X-Total-Count`, `X-Has-Next`, `X-Source-Read-At` để kiểm tra lượt lấy.
Bản sửa 15/10 còn trả `X-Result-Digest` cho cả bốn API sản lượng: mã SHA-256
của toàn bộ kết quả sau lọc, không chỉ trang đang trả. Giữ cùng digest trên
mọi trang; nếu đổi hoặc thiếu, bỏ toàn bộ lượt nhận của API/kỳ đó và lấy lại
từ trang 1 sau khi nguồn ổn định. Không nối các trang của hai lượt khác nhau.
Digest không tính `reportDate`, không giữ cache và không tạo giao dịch SQL
chung giữa các trang, hai nguồn hoặc các API. JSON vẫn đúng ba trường cũ.
Chỉ chế độ **published** mới trả `X-Snapshot-Id` và yêu cầu truyền `snapshotId`
từ trang 2; cũng kiểm tra digest khi lấy sản lượng. Không áp dụng cách phân
trang published cho live. Máy chủ cũ chưa trả digest cần được cập nhật trước
khi dùng bộ kiểm tra đủ kỳ mới; thiếu header không được xác nhận là đã lấy đủ.

Ví dụ cấu trúc phản hồi, sử dụng mã giả lập và danh sách rỗng:

```json
{
  "data": [],
  "code": "1",
  "message": "Lấy dữ liệu thành công"
}
```

Ở chế độ live, `reportDate` là ngày đọc nguồn theo UTC+7; `finishDate` là ngày
sản lượng của phiếu. Header `X-Source-Read-At` là thời điểm đọc nguồn của yêu cầu,
không phải ngày sửa phiếu hoặc thời điểm Tổng công ty chấp nhận số. Ở chế độ
published, header này là mốc đọc cũ nhất của các kỳ được giữ trong phiên công bố.

HTTP 200 với `data=[]` ở chế độ live nghĩa là lần đọc đã qua kiểm tra và không
có dòng phù hợp trong phạm vi được chọn. Không chứng minh các phiếu ngoài phạm
vi hoặc chưa xác định cầu đã được đối soát với MISBI. Lỗi nguồn, trọng lượng,
đơn vị hoặc tham chiếu bắt buộc trả lỗi; không trả số 0 thay dữ liệu thiếu.
Ở chế độ published, kỳ chưa công bố đầy đủ cũng bị chặn.

| HTTP | Một số mã chẩn đoán trong header `X-Error-Code` |
|---|---|
| 401 / 403 | Token sai/hết hạn, tài khoản không có quyền công ty/resource |
| 404 | Không có mã chi tiết trong danh mục |
| 409 | Lỗi chế độ published: `SNAPSHOT_REQUIRED`, `PUBLICATION_CONFLICT`, `RULE_VERSION_CHANGED` |
| 410 | Published: `SNAPSHOT_EXPIRED` — lấy lại từ trang 1 |
| 422 | `INVALID_REQUEST`, `INVALID_PERIOD`, `UNSUPPORTED_FILTER`; live không nhận `snapshotId` |
| 429 | Quá số lần đăng nhập sai; có `Retry-After` |
| 503 | Live: `SOURCE_BUSY`, `SOURCE_TIMEOUT`, `SOURCE_UNAVAILABLE`, `SOURCE_ID_CONFLICT`, `WEIGHT_OR_UNIT_UNAVAILABLE` và các lỗi chất lượng nguồn; published còn có `DATASET_NOT_READY`, `PERIOD_NOT_READY`, `DATASET_STALE` |

## 4. Nguồn và cách tính

Luồng hiện hành (`CORPORATE_READ_MODE=live`):

```text
Tổng công ty GET có Bearer token
  -> SmartTOS + SmartTOS_BenThuy (SELECT mới)
  -> kiểm tra schema, đơn vị, ID, tham chiếu và phạm vi
  -> tổng hợp, lọc, phân trang trong yêu cầu -> JSON data/code/message
```

Không giữ kết quả để dùng lại giữa các yêu cầu và không tự chuyển sang bản cũ
khi nguồn lỗi. GET không chạy báo cáo dashboard hoặc đổi KPI. Kỳ dài cần chia
thành các khoảng liên tiếp không chồng ngày, mỗi khoảng tối đa 31 ngày.

Chế độ `published` tùy chọn dùng luồng trích preview -> kiểm tra -> công bố
nguyên tử vào kho API -> GET phiên đã công bố, không đọc SQL trong GET.
Kỳ chưa trích không coi là đã có dữ liệu. Hai chế độ đều có giới hạn nguồn và
không dựng số liệu mẫu làm phương án dự phòng.

Sản lượng nhóm theo **toàn bộ các chiều đã có trong payload**, rồi cộng Decimal.
Các số đo trong JSON là số, không phải chuỗi số:

- Tấn dùng `TallyShift.weightNetSum` với bằng chứng đơn vị TAN hoặc phép đổi KG
  sang TAN duy nhất, hợp lệ. Nhánh quy đổi bổ sung chỉ hoạt động cho mã hàng được
  cấu hình, khi đủ quan hệ manifest/quy cách và hệ số nguồn được kiểm tra; xem
  [điều kiện quy đổi](API_TRUC_TIEP_SMARTTOS.vi.md#quy-đổi-hàng-rời-theo-danh-mục-nguồn).
  Không lấy số lượng thay khối lượng, nhân hệ số bốc xếp hoặc tự cộng nắp hầm.
- TEU dùng số lượng container của nguồn và quy tắc 20 feet = 1; 40/45 feet = 2
  đang dùng trong dự án. Chưa thay tấn thực tế bằng định mức 30/25/3,88/2,25 tấn.
- Qua cầu với `quay_selection="source_statistics"` yêu cầu phương án thuộc
  `SANLUONG-QUACANG`, hướng xếp/dỡ 1/2, chuyến và tàu vật lý hợp lệ. Chế độ
  `methods` còn yêu cầu allowlist phương án riêng.
- Gate theo `gate_selection="vessel_type"` chọn các loại kho/bãi đã quy định từ `VesselType`, không dùng `gate_method_ids` để lọc. Chế độ `methods` cần allowlist phương án riêng; đây cũng là chế độ tương thích khi profile không khai báo `gate_selection`. Không coi mọi phiếu ngoài qua cầu là sản lượng gate.
- Hàng được khai báo rõ `container`, `bulk`, `roro` hoặc `exclude` theo cargoId.
  RORO bị loại khỏi hai nhóm sản lượng. Không cộng nắp hầm; chỉ loại theo
  phương án/phân loại có bằng chứng, không coi toàn bộ mã hàng “Khác” là nắp hầm.
- `shipId` lấy Vessel.vesselId, không lấy mã chuyến. Các phép nối phải giữ mỗi
  tallyShiftId duy nhất; phát hiện nhân dòng sẽ chặn kết quả.
- Hướng xếp/dỡ dựa vào CargoDirect. Không nhầm CargoClass IMPORT/EXPORT/DOMESTIC
  thành hướng tàu chỉ vì tên bảng giống nhau.
- Nguồn gốc, chủ khai thác tàu/vỏ và đại lý chưa có quan hệ nguồn đã xác minh:
  hiện trả null và cảnh báo. Có danh mục origins không có nghĩa phiếu đã được
  xác định nguồn gốc. Không lấy khách hàng/consignee thay chủ khai thác.
- Không suy ISO/chiều cao/kiểu container chỉ từ tên 20F/40E. `containerSize`
  xuất các thuộc tính đã được cấu hình và kiểm tra.

Phạm vi CNT cần cả hai xí nghiệp. Phạm vi qua cầu đã chọn là
`production_scope="nghe_tinh"`: loại chuyến cập Cầu 5 đầu tiên, kể cả sau đó
chuyển cầu; không giới hạn ngày lịch sử khi tìm cầu đầu tiên. Từ
`initial-berth-v2`, các cầu trùng thời điểm đầu tiên nhưng đều hợp lệ và cùng
phạm vi vẫn xác định được phạm vi sản lượng; không tự chọn ID/tên cầu cụ thể.
Nếu các ứng viên lẫn Cầu 5 với cầu khác, có lịch sử cập thực tế thiếu giờ hoặc
cầu đầu tiên không hợp lệ thì phạm vi vẫn chưa xác định. Các phiếu chưa xác định
phạm vi được đếm riêng nội bộ, chưa chứng minh đủ tổng MISBI.
Gate theo `vessel_type` lấy toàn bộ loại kho/bãi được cấu hình, độc lập
với cầu cập đầu tiên; không tự coi đây là lượt hàng qua cổng vật lý hoặc cộng
quay + gate thành tấn thông qua duy nhất. Cần chốt phạm vi nhận gate và tránh
đếm cùng hàng ở nhiều công đoạn. Hồ sơ mẫu vẫn để phạm vi trống.

Danh mục nguồn có kiểm tra khóa trùng, trường bắt buộc, cờ xóa và cây cha/con.
Khách hàng giữ tombstone `metadata.isDeleted`. Tàu ảo và tàu đã xóa không nằm
trong `shipDetails`. Thiếu tham chiếu danh mục sẽ chặn kết quả sản lượng.

## 5. Cấu hình và vận hành

Các biến sau đọc từ **môi trường tiến trình** hoặc Railway Variables, không tự
được kích hoạt chỉ bằng việc chép vào `.env`:

```text
CORPORATE_API_ENABLED=true
CORPORATE_READ_MODE=live
CORPORATE_SOURCE_PROFILE=/data/corporate-source.json
CORPORATE_SYNC_ENABLED=false
CORPORATE_STATE_PATH=/data/corporate.sqlite3
CORPORATE_EXPORT_PATH=/data/corporate-exports.sqlite3
```

Hai đường dẫn có thể bỏ trống: mặc định cùng thư mục với `DASHBOARD_STATE_PATH`.
Railway cần volume bền vững cho tài khoản và trạng thái. Kho export dùng khi
chọn published; live không cần có dữ liệu đã công bố trong kho này.

Tạo bản sao `profile.example.json` vào `backend/.data/corporate-profile.json`,
điền các ánh xạ có bằng chứng. Thư mục `.data` không đưa vào Git. Các khóa:

| Khóa | Cách điền |
|---|---|
| `approved` | `false` trong lúc làm; `true` sau khi người phụ trách đối chiếu xong hồ sơ ánh xạ |
| `company_id`, `terminals` | CNT và đủ `cua_lo`, `ben_thuy` |
| `source_identity_mode` | Mặc định `native`; chọn `terminal_prefixed_v1` để phân biệt ID công khai bằng `CL:`/`BT:` cho tám danh mục S và bốn API sản lượng. Cần triển khai mã hỗ trợ và chuyển dữ liệu bên nhận đồng bộ trước khi sử dụng. |
| `date_basis` | `shiftDate` chỉ khi ngày hạch toán ca là ngày báo cáo được yêu cầu |
| `production_scope` | `nghe_tinh`, `vietsun` hoặc `all_activity` theo phạm vi thống nhất |
| `quay_method_ids`, `gate_method_ids` | Object theo xí nghiệp, mỗi giá trị là danh sách jobMethodId nguồn; không chồng lấn |
| `quay_selection`, `gate_selection` | Hiện hành: `source_statistics` và `vessel_type`; chỉ chế độ `methods` dùng allowlist tương ứng |
| `cargo_kind_by_cargo` | Theo xí nghiệp rồi cargoId dạng chuỗi; giá trị container/bulk/roro/exclude |
| `cargo_kind_by_group` | Theo xí nghiệp rồi cargoGroupId; ánh xạ mặt hàng ở trên được ưu tiên |
| `container_size_source` | `native_cargo` lấy cargoId và mã 20F/40F/20E/40E đã xác định; chỉ thêm tiền tố vào ID công khai nếu đã chọn chế độ đó, không tự suy ISO |
| `accepted_null_fields` | Cho phép các trường thiếu đã được xác nhận để thử; chưa thay thế xác nhận tiếp nhận chính thức của Tổng công ty |
| `cargo_catalog_source` | `native_groups` lấy CargoGroup/Cargo nguồn; chế độ định danh chỉ phân biệt nguồn của ID công khai, không đổi nhóm hàng |
| `cargo_types`, `cargo_type_by_cargo` | Dành cho chế độ ánh xạ được cấu hình riêng; không thay ID gốc trong cấu hình hiện hành |
| `container_sizes_by_cargo` | Dành cho chế độ `configured`; chỉ điền thuộc tính có bằng chứng, không suy ISO từ mã hàng |
| `source_columns` | Ánh xạ thuộc tính tàu/khách hàng tới cột nguồn có cùng ý nghĩa; tên cột và kiểu được kiểm tra trước SELECT |

Các thuộc tính khách hàng được hỗ trợ trong `source_columns.customers`:
customerNameEN, customerTaxCode, customerPhoneNum, customerAddress, customerEmail,
isCarrier, isAgent, customerStatus và customerType (chỉ dùng lọc nội bộ).
Không nhập thông tin kết nối hoặc mật khẩu vào profile.

Các lệnh preview dưới đây phục vụ đối soát; lệnh publish chỉ cần cho chế độ
`published`. Chúng không phải bước bắt buộc trước khi GET ở live. Ví dụ từ gốc
repository, dùng Python của môi trường đã cài dependencies:

```powershell
python -m backend.corporate_api.manage_exports extract `
  --profile backend/.data/corporate-profile.json `
  --start 2026-08-01 --end 2026-08-31 `
  --output backend/.data/s-preview-202608.json
```

Lệnh chỉ đọc SQL, tạo preview riêng và in số dòng/blockers, không công bố lên
API. Có thể thêm `--resource` nhiều lần để trích nhóm cần kiểm tra. Preview chứa
dữ liệu nghiệp vụ; giữ trong thư mục riêng, không commit và không gửi công khai.
Không ghi đè preview cũ: dùng tên mới khi trích lại.

Đối chiếu tổng theo ngày/tàu/loại hàng/phương án, xác định các null và phạm vi,
sau đó công bố:

```powershell
python -m backend.corporate_api.manage_exports publish `
  --profile backend/.data/corporate-profile.json `
  --input backend/.data/s-preview-202608.json
python -m backend.corporate_api.manage_exports status
```

Lệnh kiểm tra lại schema payload, profile digest, coverage, tham chiếu danh mục
và trạng thái sẵn sàng. Toàn bộ nhóm được chọn công bố thành công hoặc rollback.
Danh mục mới không được loại mã vẫn đang được dữ liệu lịch sử đã công bố tham
chiếu; trường hợp này cần xử lý dữ liệu danh mục lịch sử trước khi công bố lại.
Trích lại một kỳ sẽ thay dữ liệu của đúng kỳ đó, gồm cả sửa/xóa; giữ kỳ khác.
Mỗi resource giữ tối đa 8 phiên. Đổi profile yêu cầu xây dựng lại dữ liệu theo
cùng quy tắc, không trộn hai công thức. `--replace-all` chỉ dùng khi chủ động
thay toàn bộ kỳ đã công bố của những resource được chọn.

Tạo tài khoản máy với quyền rõ ràng; CLI hỏi mật khẩu riêng, không truyền qua
tham số shell hay chat. Ví dụ chỉ cấp một resource:

```powershell
python -m backend.corporate_api.manage_clients create `
  --username tct_reader --company CNT --resource shipDetails
```

Lặp `--resource` để cấp đủ những API cần dùng. `update` thay mật khẩu/quyền và
thu hồi token cũ; `disable` vô hiệu hóa tài khoản. Token được lưu dạng hash,
hết hạn sau tối đa 8 giờ. Có giới hạn đăng nhập sai; không có tài khoản/mật khẩu
mặc định. Chỉ trao thông tin đăng nhập qua kênh nội bộ phù hợp.

Trong container có working directory `/app`, dùng `python -m corporate_api...`
thay cho `python -m backend.corporate_api...`.

Ở live, giữ lịch trích/công bố tắt; lịch GET phía nhận cần được thống nhất sau
đối soát. API không xác nhận Tổng công ty đã nhận/chấp nhận dữ liệu chỉ từ một
lần HTTP 200 hoặc một lần chạy kiểm tra không có lỗi.

## 6. Kiểm tra trước bàn giao

Kiểm tra đúng bản đang triển khai: xác thực/quyền, schema phản hồi, các header,
mọi trang và mọi khoảng ngày cần giao. Kiểm tra những danh mục bên nhận phải
tra cứu bằng `verify_delivery --verify-references`; kiểm tra cả ID và quan hệ
giữa danh mục với sản lượng theo cùng chế độ định danh. Ở chế độ `native`, ID
đã được kiểm tra trong nguồn của phiếu chưa chắc đọc được trên danh mục công
khai gộp hai nguồn vì xung đột cùng ID.

Khối lượng nguồn bằng 0 và số lượng dương có chẩn đoán nội bộ riêng, không làm
thay số đo hoặc payload. Chưa có quy tắc nghiệp vụ được xác nhận thì không
coi các phiếu đó là thiếu cân, phiếu trùng hoặc nguồn tấn bổ sung.

Giữ báo cáo chi tiết ngoài Git, cùng phiên bản nguồn, phạm vi và thời điểm đọc.
Biên bản nghiệm thu phải ghi rõ những kỳ/chỉ tiêu đã kiểm tra và các vấn đề
chưa xử lý; không dùng kết quả kiểm thử hoặc HTTP 200 thay xác nhận nghiệp vụ.
