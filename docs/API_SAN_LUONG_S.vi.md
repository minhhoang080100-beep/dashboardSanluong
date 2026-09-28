# API sản lượng S — triển khai CNT

Nguồn hợp đồng: `API Vận Hành/API Domain SLG_CB (Spec)_v3.xlsx`.
Phạm vi triển khai ngày 21/09/2026: **13 API chính, không triển khai RORO**.
API phục vụ cách Tổng công ty chủ động gọi GET; không tự gửi POST dữ liệu sang hệ thống khác.

Ngày 25/09/2026 bổ sung danh mục B/C dưới `/api/oprt/` thành hợp đồng riêng;
xem [API_DANH_MUC_VAN_HANH.vi.md](API_DANH_MUC_VAN_HANH.vi.md). Các endpoint S và
lựa chọn đồng bộ mặc định không thay đổi khi thêm các danh mục vận hành.

## 1. Trạng thái và giới hạn kiểm chứng

Đã có mã nguồn, hợp đồng phản hồi, xác thực tài khoản máy, kho dữ liệu công bố,
adapter đọc nguồn, lệnh đối chiếu/công bố và kiểm thử tự động. Chưa kích hoạt trên
Railway, chưa tạo tài khoản thật, chưa công bố số liệu thật và chưa nghiệm thu với
Tổng công ty.

Phiên kiểm tra ngày 21/09 chưa đọc được SQL trực tiếp; kết nối cục bộ báo SQLSTATE 08001,
còn các lần Railway SSH không trả được kết quả trong thời gian giới hạn. Điều này
**không chứng minh SQL Server đang ngừng hoạt động**. Adapter tham khảo bằng chứng
cấu trúc nguồn đã có trong các lần kiểm tra 14–18/09 và luôn kiểm tra lại cột
bắt buộc; danh mục còn kiểm tra kiểu cột, sản lượng kiểm tra giá trị/đơn vị trước
khi tổng hợp. Các bài kiểm thử dùng dữ liệu giả lập và SQLite
tạm, không phải bằng chứng đối soát số liệu thực tế.

`CORPORATE_API_ENABLED` mặc định tắt. Các thiết lập ánh xạ còn trống trong
`backend/corporate_api/profile.example.json` là thông tin cần xác định, không phải
danh mục rỗng của công ty. Không chuyển `approved` sang `true` chỉ để bỏ qua lỗi.

## 2. Đường dẫn

| Method | Path | Dữ liệu |
|---|---|---|
| POST | `/api/login` | Tài khoản máy, lấy Bearer token |
| GET | `/api/contQuayVolumesCB` | Tấn và TEU container qua cầu |
| GET | `/api/contGateVolumesCB` | Tấn và TEU container tại cổng/kho bãi theo loại địa điểm hoặc phương án được cấu hình |
| GET | `/api/bulkQuayVolumesCB` | Tấn các loại hàng thuộc phạm vi Bulk qua cầu |
| GET | `/api/bulkGateVolumesCB` | Tấn Bulk tại cổng/kho bãi theo loại địa điểm hoặc phương án được cấu hình |
| GET | `/api/shipDetails` | Tàu vật lý |
| GET | `/api/customers` | Khách hàng và trạng thái xóa |
| GET | `/api/cargoType` | Loại hàng đã ánh xạ |
| GET | `/api/cargoCategory` | Cây danh mục hàng đã ánh xạ |
| GET | `/api/handlingMethodList` | Phương án tác nghiệp nguồn |
| GET | `/api/class` | Hướng hàng từ CargoDirect |
| GET | `/api/origins` | Nguồn gốc hàng đã cấu hình |
| GET | `/api/containerSize` | Kích cỡ container đã ánh xạ |

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
| ID cùng số có thể nằm ở hai xí nghiệp | Giữ ID gốc dưới dạng chuỗi, không thêm tiền tố. Danh mục trùng ID và khác nội dung bị chặn khi gộp; trang đối soát chọn riêng database nguồn. |
| Mẫu chỉ có `data`, `code`, `message` | JSON giữ đúng ba trường; thông tin phân trang chuyển sang HTTP headers, không thêm thuộc tính vào payload |
| Các trường phản hồi không đánh dấu bắt buộc rõ | Trường chưa có nguồn được phép null theo schema hiện tại và được nêu trong cảnh báo; cần bên nhận xác nhận tính tương thích |

Tất cả GET yêu cầu `companyId=CNT`. Tài khoản phải được cấp quyền công ty và đúng
resource. Tên trường và URL phân biệt hoa/thường.
Các cờ `isCarrier`, `isAgent`, `metadata.isDeleted` xuất dạng số 0/1; hai cờ vai
trò khách hàng giữ null nếu chưa xác định được từ nguồn.

Production bắt buộc `startDate`, `endDate` dạng `yyyyMMdd`, gồm cả hai ngày.
Quay nhận thêm `shipId`, `handlingMethodId`; gate chỉ nhận `handlingMethodId`.
`cargoCategory` nhận `cargoTypeId`; `containerSize` nhận `containerSizeId`.
Customers nhận cặp ngày tùy chọn, `customerTaxCode`, `customerType`. Bộ lọc không
áp dụng cho resource bị từ chối, không bị âm thầm bỏ qua.

Mặc định `page=1`, `limit=20`; tối đa 100 dòng/trang. Từ trang 2 phải truyền
`snapshotId` của trang 1 để bảo đảm không lặp/mất dòng khi có lần công bố mới.
Đây là phần mở rộng cần đưa vào tài liệu kết nối cho bên nhận.

Ví dụ cấu trúc phản hồi, sử dụng mã giả lập và danh sách rỗng:

```json
{
  "data": [],
  "code": "1",
  "message": "Lấy dữ liệu thành công"
}
```

`reportDate` là ngày gọi API theo UTC+7. `sourceReadAt` là thời điểm đọc dữ liệu,
không phải thời điểm sửa phiếu và không phải thời điểm Tổng công ty chấp nhận số.
Với các kỳ tích lũy từ nhiều lần đọc, thời điểm này là mốc đọc cũ nhất còn được
giữ trong phiên công bố, để không tạo cảm giác toàn bộ lịch sử vừa được làm mới.

HTTP 200 với `data=[]` chỉ dùng khi kỳ đã được công bố đầy đủ và bộ lọc không có
dòng phù hợp. Chưa có dữ liệu công bố, thiếu ngày trong kỳ, lỗi nguồn hoặc thiếu
khả năng lọc trả lỗi; không trả số 0 thay dữ liệu thiếu.

| HTTP | Một số mã chẩn đoán trong header `X-Error-Code` |
|---|---|
| 401 / 403 | Token sai/hết hạn, tài khoản không có quyền công ty/resource |
| 404 | Không có mã chi tiết trong danh mục |
| 409 | `SNAPSHOT_REQUIRED`, `PUBLICATION_CONFLICT`, `RULE_VERSION_CHANGED` |
| 410 | `SNAPSHOT_EXPIRED` — lấy lại từ trang 1 |
| 422 | `INVALID_REQUEST`, `INVALID_PERIOD`, `UNSUPPORTED_FILTER` |
| 429 | Quá số lần đăng nhập sai; có `Retry-After` |
| 503 | `CORPORATE_API_DISABLED`, `DATASET_NOT_READY`, `PERIOD_NOT_READY`, `FILTER_NOT_READY`, `STORAGE_UNAVAILABLE` |

## 4. Nguồn và cách tính

Luồng dữ liệu:

```text
SmartTOS + SmartTOS_BenThuy (SELECT)
  -> preview: schema, đơn vị, ID, ngày, ánh xạ, tổng đối chiếu
  -> công bố nguyên tử vào kho API trên volume
  -> Tổng công ty GET các phiên dữ liệu đã công bố
```

GET không gọi SQL Server, không chạy báo cáo dashboard và không đổi KPI hiện có.
Mỗi lần trích nguồn tối đa 31 ngày; có giới hạn dòng và timeout SQL. Khi cần lịch
sử dài, trích/công bố từng kỳ liên tiếp. Kỳ chưa được trích không coi là đã có dữ
liệu. Không dựng số liệu mẫu làm phương án dự phòng.

Sản lượng nhóm theo **toàn bộ các chiều đã có trong payload**, rồi cộng Decimal:

- Tấn dùng `TallyShift.weightNetSum` với bằng chứng đơn vị TAN hoặc phép đổi KG
  sang TAN duy nhất, hợp lệ. Không nhân thêm hệ số bốc xếp, không tự cộng nắp hầm.
- TEU dùng số lượng container của nguồn và quy tắc 20 feet = 1; 40/45 feet = 2
  đang dùng trong dự án. Chưa thay tấn thực tế bằng định mức 30/25/3,88/2,25 tấn.
- Qua cầu yêu cầu phương án thuộc `SANLUONG-QUACANG`, hướng xếp/dỡ 1/2, chuyến
  và tàu vật lý hợp lệ, đồng thời nằm trong allowlist phương án đã xác định.
- Gate theo `gate_selection="vessel_type"` chọn các loại kho/bãi đã quy định từ `VesselType`, không dùng `gate_method_ids` để lọc. Chế độ `methods` cần allowlist phương án riêng; đây cũng là chế độ tương thích khi profile không khai báo `gate_selection`. Không coi mọi phiếu ngoài qua cầu là sản lượng gate.
- Hàng được khai báo rõ `container`, `bulk`, `roro` hoặc `exclude` theo cargoId.
  RORO bị loại khỏi hai nhóm sản lượng; nắp hầm cần ánh xạ `exclude`.
- `shipId` lấy Vessel.vesselId, không lấy mã chuyến. Các phép nối phải giữ mỗi
  tallyShiftId duy nhất; phát hiện nhân dòng sẽ chặn công bố.
- Hướng xếp/dỡ dựa vào CargoDirect. Không nhầm CargoClass IMPORT/EXPORT/DOMESTIC
  thành hướng tàu chỉ vì tên bảng giống nhau.
- Nguồn gốc, chủ khai thác tàu/vỏ và đại lý chưa có quan hệ nguồn đã xác minh:
  hiện trả null và cảnh báo. Có danh mục origins không có nghĩa phiếu đã được
  xác định nguồn gốc. Không lấy khách hàng/consignee thay chủ khai thác.
- Không suy ISO/chiều cao/kiểu container chỉ từ tên 20F/40E. `containerSize`
  xuất các thuộc tính đã được cấu hình và kiểm tra.

Phạm vi CNT cần cả hai xí nghiệp. Quy tắc Cầu 5 của API phải được chốt trong
`production_scope`. Khi dùng `nghe_tinh`, áp dụng cầu cập đầu tiên của cả chuyến
như dashboard; nguồn chưa xác định được cầu chặn việc khẳng định đủ kỳ. Với
gate không có chuyến/cầu, cần thống nhất phạm vi riêng trước khi công bố, không
tự quy về Cảng Nghệ Tĩnh. Hồ sơ mẫu để phạm vi trống để tránh quyết định thay.

Danh mục nguồn có kiểm tra khóa trùng, trường bắt buộc, cờ xóa và cây cha/con.
Khách hàng giữ tombstone `metadata.isDeleted`. Tàu ảo và tàu đã xóa không nằm
trong `shipDetails`. Thiếu tham chiếu danh mục sẽ chặn việc công bố sản lượng.

## 5. Cấu hình và vận hành

Các biến sau đọc từ **môi trường tiến trình** hoặc Railway Variables, không tự
được kích hoạt chỉ bằng việc chép vào `.env`:

```text
CORPORATE_API_ENABLED=true
CORPORATE_STATE_PATH=/data/corporate.sqlite3
CORPORATE_EXPORT_PATH=/data/corporate-exports.sqlite3
```

Hai đường dẫn có thể bỏ trống: mặc định cùng thư mục với `DASHBOARD_STATE_PATH`.
Railway cần volume bền vững hiện dùng. Docker đã copy package mới và chuẩn bị
quyền file dữ liệu khi khởi động; đây chưa phải bằng chứng đã build/deploy ảnh mới.

Tạo bản sao `profile.example.json` vào `backend/.data/corporate-profile.json`,
điền các ánh xạ có bằng chứng. Thư mục `.data` không đưa vào Git. Các khóa:

| Khóa | Cách điền |
|---|---|
| `approved` | `false` trong lúc làm; `true` sau khi người phụ trách đối chiếu xong hồ sơ ánh xạ |
| `company_id`, `terminals` | CNT và đủ `cua_lo`, `ben_thuy` |
| `date_basis` | `shiftDate` chỉ khi ngày hạch toán ca là ngày báo cáo được yêu cầu |
| `production_scope` | `nghe_tinh`, `vietsun` hoặc `all_activity` theo phạm vi thống nhất |
| `quay_method_ids`, `gate_method_ids` | Object theo xí nghiệp, mỗi giá trị là danh sách jobMethodId nguồn; không chồng lấn |
| `cargo_kind_by_cargo` | Theo xí nghiệp rồi cargoId dạng chuỗi; giá trị container/bulk/roro/exclude |
| `cargo_types` | Mã loại hàng tích hợp → tên loại đã thống nhất |
| `cargo_type_by_cargo` | Theo xí nghiệp rồi cargoId → mã có trong cargo_types |
| `origins` | Mã nguồn gốc → tên; không tự tạo quan hệ tới phiếu |
| `container_sizes_by_cargo` | Theo xí nghiệp rồi cargoId → localSzTp, isoSzTp, sizeCode, heightCode, containerTypeCode |
| `source_columns` | Ánh xạ thuộc tính tàu/khách hàng tới cột nguồn có cùng ý nghĩa; tên cột và kiểu được kiểm tra trước SELECT |

Các thuộc tính khách hàng được hỗ trợ trong `source_columns.customers`:
customerNameEN, customerTaxCode, customerPhoneNum, customerAddress, customerEmail,
isCarrier, isAgent, customerStatus và customerType (chỉ dùng lọc nội bộ).
Không nhập thông tin kết nối hoặc mật khẩu vào profile.

Ví dụ lệnh từ gốc repository, dùng Python của môi trường đã cài dependencies:

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

Chưa cài lịch tự động trích/công bố. Bước tự động hóa chỉ nên thực hiện sau khi
preview thật đã đối soát và hồ sơ nguồn hoàn chỉnh. API không xác nhận Tổng công
ty đã nhận/chấp nhận dữ liệu chỉ từ một lần HTTP 200.

## 6. Dữ liệu vận hành và kiểm thử

Kho tài khoản máy và kho export là hai SQLite riêng, không sửa control.sqlite3.
Backup tự động hiện có của dashboard chưa bao gồm hai file mới này; cần bổ sung
phạm vi sao lưu khi chính thức kích hoạt API. Chưa tạo thêm lịch backup trên máy
cá nhân trong thay đổi này. Dung lượng phải đo sau khi có dữ liệu thật.

Kiểm thử mới nằm trong `tests/test_corporate_*.py`, gồm hợp đồng HTTP, tài khoản
máy, phân quyền, mốc ngày, phân trang khi công bố đồng thời, nguồn lỗi, dữ liệu
thiếu, loại RORO, tấn/TEU, tham chiếu danh mục và rollback công bố. Chạy:

```powershell
python -m pytest -q tests
```

Trước khi nghiệm thu: kết nối SQL thành công, điền/đối chiếu profile, chốt các
quy ước ở mục 3 với bên nhận, chạy preview thật cho một ngày và một tháng, kiểm
tra cổng riêng với nguồn tác nghiệp, rồi mới bật endpoint cho tài khoản thật.

Kết quả kiểm tra ngày 21/09/2026: lượt chạy toàn bộ backend đạt 738 ca tại thời
điểm đó; sau các sửa lỗi cuối, chạy lại toàn bộ nhóm `test_corporate_*.py` đạt
142 ca, gồm kiểm thử xuyên suốt adapter → công bố → HTTP có đăng nhập. Hai tập
kiểm thử có phần trùng nhau, không cộng hai con số này. `git diff --check` đạt;
ba file Excel nguồn giữ nguyên SHA256. Chưa build container vì Docker daemon
không chạy; kiểm tra import với working directory như container đã đạt.

### Bản xem dữ liệu thật ngày 22/09/2026

Đã đọc SQL qua Railway bằng truy vấn SELECT: 334 dòng TallyShift Cửa Lò và
18 dòng Bến Thủy cho ngày 16/09/2026, cùng schema/danh mục hai nguồn. Bản đọc
cục bộ nằm trong các thư mục bị Git bỏ qua; không đưa dữ liệu khách hàng vào Git.
Trang `http://127.0.0.1:8765/` sử dụng bản đọc thật này, không tự cập nhật SQL.

Trong kho export riêng phục vụ xem cục bộ, 8 resource đã qua kiểm tra hợp đồng
và tham chiếu: shipDetails (1.293), customers (894, gồm bản ghi đã xóa với cờ
metadata), cargoType (3), cargoCategory (103), handlingMethodList (1.091),
class (12), containerSize (12), bulkQuayVolumesCB (8 dòng tổng hợp).
Các mã phân loại trong profile cục bộ vẫn là đề xuất tích hợp, chưa thể coi là
bộ mã đã được Tổng công ty chấp nhận.

- Cột khách hàng thực tế là `Partner.partnerFullName`; nếu thiếu, đọc
  `partnerShortName`. Thiếu cả hai thì giữ null và cảnh báo, không đặt tên giả.
- Chưa xuất DWT trong profile cục bộ vì nguồn có vesselId=1030 ghi DWT âm
  (-227); không sửa nguồn hoặc tự đổi dấu giá trị.
- `contQuayVolumesCB` chính thức vẫn trả 503 vì 16 dòng container “Khác” có
  số lượng 0 và tấn null, chưa xác nhận là dòng mẫu không phát sinh.
  `/local-preview/contQuayVolumesCB` chỉ dành cho đối soát tại máy này: 24 dòng
  tổng hợp đã xác định, tổng 8.011,65 tấn của PHÚC HƯNG, có cờ
  `publishable=false`. Không coi số thiếu là 0, không cộng nắp hầm.
- Hai resource cổng/bãi và origins chưa công bố vì thiếu ánh xạ được xác nhận.

Đã kiểm tra HTTP với dữ liệu thật: 8 resource trả 200 đúng số dòng, 4 resource
chưa đủ điều kiện trả 503; phân trang, 401 khi thiếu token, chặn origin ngoài,
kỳ chưa đọc và RORO hoạt động đúng. Chưa có kiểm tra trực quan bằng trình duyệt
và chưa triển khai cấu hình/bản đọc này lên production.

### Cập nhật ID gốc theo yêu cầu ngày 22/09/2026

Đã bỏ tiền tố do ứng dụng tạo trong tất cả ID nguồn. Ví dụ `shipId="636"`,
`handlingMethodId="751"`, `containerSizeId="2125"`. Kiểu chuỗi theo hợp đồng
API được giữ nguyên; không sửa khóa hoặc dữ liệu trong SQL Server.

Hai database thực tế có ID trùng nhưng thuộc tính khác nhau. Adapter chỉ gộp
các dòng danh mục giống hệt nhau; trường hợp khác nội dung trả
`SOURCE_ID_CONFLICT`, không lấy tùy ý một bản ghi. Trang xem cục bộ hiện dùng
`/local-preview/{resource}` và bộ chọn Cửa Lò/Bến Thủy để hiển thị đúng từng
nguồn, với `sourceDatabase` và `publishable=false`. Bản đọc mới nằm tại
`backend/.data/corporate-real-local/native-previews.json`; các ID loại hàng ở
trang này cũng lấy từ CargoGroup của nguồn, không dùng mã CNT-BULK tự đặt.

Kho công bố cũ có tiền tố được giữ lại để đối chiếu nhưng không còn được trang
xem sử dụng. `/api/*` ở server cục bộ dùng kho mới chưa công bố, không phục vụ
ID cũ hoặc coi dữ liệu riêng một xí nghiệp là dữ liệu toàn công ty.
Đã kiểm tra 24 cặp resource/nguồn qua HTTP, phân trang và cách ly nguồn;
112 kiểm thử liên quan đạt. Tổng container PHÚC HƯNG giữ nguyên 8.011,65 tấn.

### Trang đối soát gộp hai nguồn

Theo yêu cầu tiếp theo, trang cục bộ đã bỏ bộ chọn database và hiển thị chung
Cửa Lò/Bến Thủy. `/local-preview/{resource}` không còn yêu cầu `terminal`.
Mỗi dòng giữ nguyên ID gốc và có `sourceDatabase` để phân biệt: ví dụ vesselId
942 là HIỆP HƯNG 89 tại SmartTOS nhưng KHÁNH MINH 69 tại SmartTOS_BenThuy.
Không loại một dòng chỉ vì ID trùng, không tự cộng sản lượng của hai thực thể
có ID bằng nhau. Các dòng tổng hợp trong mỗi nguồn được giữ nguyên.

Đây là hình thức xem chung dữ liệu đối soát, không thay đổi hợp đồng `/api/*`;
`sourceDatabase` là trường bổ sung của bản xem cục bộ. Cơ chế chặn công bố danh
mục trùng ID khác nội dung vẫn được giữ cho đến khi xác định khóa liên kết
phù hợp với bên nhận. Đã kiểm tra HTTP toàn bộ 12 resource, phân trang toàn
bộ bản ghi, bảo toàn hai tàu ID 942 và tổng tấn không thay đổi.

### Phản hồi JSON theo mẫu Excel — cập nhật mới nhất

Đã bỏ các trường tự bổ sung khỏi JSON cả trên router `/api/*` lẫn trang xem:
`sourceDatabase`, `sourceDatabases`, `publishable`, `incomplete`, `blockers`,
`pagination`, `errorCode` và giá trị code `PREVIEW_ONLY`. Phản hồi GET thành
công chỉ có `data`, `code="1"`, `message="Lấy dữ liệu thành công"`.
Các trường của mỗi dòng được kiểm tra bằng DTO; không xuất thuộc tính nội bộ.
Đăng nhập vẫn sử dụng hợp đồng token riêng.

Trạng thái/nguồn được giữ nội bộ hoặc trên giao diện đối soát. Phân trang dùng
headers `X-Page`, `X-Limit`, `X-Total-Count`, `X-Has-Next`, `X-Snapshot-Id`;
thời điểm đọc dùng `X-Source-Read-At`. Lỗi giữ HTTP status và body ba trường,
với `code="0"`, không chuyển dữ liệu chưa sẵn sàng thành phản hồi thành công.
Bản xem cục bộ vẫn ghi rõ dữ liệu container chưa đầy đủ bên ngoài JSON.
Kiểm tra HTTP 12 resource xác nhận cấu trúc rút gọn, kiểu số, phân trang và
không có trường nguồn tự bổ sung trong từng dòng.

### Nhánh cổng/bãi theo loại địa điểm

Profile hỗ trợ `gate_selection="vessel_type"`: chọn Container Yard, Bulk Yard,
Equipment Yard, Warehouse, Boned Warehouse và CFS từ quan hệ
TallyShift → VesselVoyage → Vessel → VesselType. Không chọn Ro-Ro Yard.
Trong chế độ này `gate_method_ids` không dùng để lọc; jobMethodId vẫn bắt buộc
và trả nguyên ID trong handlingMethodId. Nếu không cấu hình gate_selection,
adapter giữ chế độ methods cũ để tránh âm thầm đổi các profile đang sử dụng.

Kho/bãi được lấy trong phạm vi toàn bộ địa điểm đã chọn, độc lập với cầu cập
đầu tiên của tàu. Điều này có thể bao gồm tác nghiệp gom bãi, rút cont và tính
phí có ghi nhận hàng; không gọi đây là chỉ tiêu hàng thực đi qua cổng vật lý.
Quy tắc Cầu 5 của nhánh qua cầu/dashboard giữ nguyên.

Các dòng đơn vị GIO bị loại khỏi tấn/TEU kho/bãi. Container chỉ quy đổi TEU
khi đơn vị số lượng là CONT, có loại/kích cỡ đã xác định. Dòng dịch vụ được
loại theo danh mục hàng đã phân loại, không đoán từ tên phương án. Trọng lượng
trống vẫn chặn tính đầy đủ, kể cả số lượng bằng 0. Loại địa điểm hoặc đơn vị
container chưa xác định sẽ báo thiếu dữ liệu thay vì biến thành danh sách rỗng.

Đọc SQL vẫn có giới hạn tối đa 31 ngày/lần và 50.000 dòng/nguồn; API chính
thức đọc kho snapshot thay vì truy vấn trực tiếp SQL mỗi lần người dùng gọi.
Đã kiểm thử phương án ngoài danh sách, không có cầu, giờ công, đơn vị chưa rõ,
dịch vụ, RORO, địa điểm thiếu và tránh ghi đồng thời một phiếu vào qua cầu/bãi.

### Origins — nguồn thật ngày 23/09/2026

Adapter đã chuyển origins từ cấu hình tự điền sang bảng dbo.CargoOrigin của
cả hai database. cargoOriginId → originId, cargoOriginName → originName,
createTime/updateTime → createdDate/modifiedDate; giữ ID dạng chuỗi theo DTO.
Bản ghi xóa bị loại, null rowDeleted được xử lý như nguồn hiện hành.

Danh mục đọc được: 1 — Hàng nội, 2 — Hàng ngoại. Tại Bến Thủy cargoOriginName
trống nhưng cargoOriginCode chứa chính tên đó: dùng nguyên giá trị code làm
tên, có ghi nhận nội bộ. Khi hai nguồn có cùng ID và tên, giữ trọn bản ghi
có thời điểm cập nhật/tạo mới hơn để tránh lặp danh mục; nếu tên khác nhau thì
chặn công bố, không tự chọn tên hoặc tạo ID mới.

Đã công bố riêng origins vào kho local dùng bởi router thật:
GET /api/origins và GET /api/origins/{originId}. Trang xem sử dụng endpoint này,
JSON chỉ có data/code/message và đúng các trường danh mục trong đặc tả.
Không push/deploy Railway trong bước này.

Kiểm tra metadata chưa tìm thấy cột cargoOriginId liên kết trên TallyShift
hoặc VesselVoyage, cũng chưa thấy cột tham chiếu tên này ở bảng nghiệp vụ khác.
VesselVoyage có cargoClassId nhưng không tự suy Hàng nội/ngoại từ CargoClass.
Các trường originId/bulkOriginId của sản lượng vì thế vẫn null, chờ quan hệ
nguồn được xác minh. Việc có danh mục không chứng minh đã phân loại được phiếu.

Bằng chứng SQL riêng: outputs/api-plan-20260921/origins-meta.json và
origins-data.json. 81 kiểm thử liên quan đạt. Kiểm tra HTTP dữ liệu thật:
hai dòng danh mục, tra cứu 1/2, 404 với ID không tồn tại, 401 thiếu token,
phân trang giữ snapshot và JSON không có các trường tự bổ sung.

### CargoType/CargoCategory — xác minh ngày 23/09/2026

Đọc lại ba danh mục ở cả hai database cho thấy dbo.CargoType là Import,
Export, Domestic (Hàng nhập khẩu, Hàng xuất khẩu, Hàng nội địa). Đây không
phải loại hàng Container/RORO/bách hóa/xá/LNG minh họa trong đặc tả. Cargo và
CargoGroup không có cột cargoTypeId; Cargo có cargoGroupId và cargoParentId.
Không chuyển sang bảng trùng tên vì sẽ thay đổi ý nghĩa phân loại.

Người dùng xác nhận chưa có bộ mã chuẩn Tổng công ty và yêu cầu giữ ID,
danh mục nguồn. Profile `cargo_catalog_source="native_groups"` thực hiện:

- cargoType: CargoGroup.cargoGroupId/Name, lấy cả createTime/updateTime nguồn.
- cargoCategory: Cargo.cargoId/Name; cargoTypeId từ Cargo.cargoGroupId,
  cargoParentId giữ từ nguồn, null/0 là gốc. Kiểm tra nhóm tồn tại, nhóm cha
  tồn tại và không tạo vòng lặp.
- Không giới hạn danh mục theo tập mặt hàng đã có trong profile tính sản lượng.
  Chỉ loại bản ghi xóa; quy tắc loại dịch vụ/RORO khi tính sản lượng giữ riêng.
- Nhánh sản lượng ở chế độ này lấy cargoTypeId trực tiếp từ cargoGroupId đã
  đọc qua SQL, thay vì bảng gán mã mặt hàng → mã loại tùy ý.

Bản đọc thật: Cửa Lò 22 CargoGroup, 110 Cargo; Bến Thủy 6 CargoGroup, 29 Cargo.
Bản xem cũ chỉ có 16/84 và 3/19 tương ứng. Không có nhóm hoặc nhóm cha thiếu
trong tập đang hoạt động đã kiểm tra. Trang local hiển thị chung 28 dòng loại
và 139 dòng hàng; đây là số dòng hai nguồn, không khẳng định tất cả ID là duy
nhất toàn công ty. Xung đột ID khác nội dung vẫn bị chặn khi công bố chính thức.

Kiểm tra: 74 test catalog/source/end-to-end/publication đạt; HTTP dữ liệu thật
đối chiếu đủ từng ID/tên/quan hệ cha, tham chiếu của sản lượng và phân trang.
JSON vẫn chỉ data/code/message, không thêm sourceDatabase. Các tổng tấn/TEU
qua cầu và cổng/bãi không đổi. Chưa push/deploy trong bước này.

Bằng chứng riêng: outputs/api-plan-20260921/cargo-masters.json (6 truy vấn
đều thành công). Truy vấn metadata tham chiếu diện rộng trong lần kiểm tra
trước đó có lỗi/timeout; không dùng kết quả đó để kết luận toàn database không
có tham chiếu. Kết luận quan hệ Cargo/CargoGroup dựa trên schema các bảng đã
đọc thành công và đối chiếu từng bản ghi.

### Nguồn kích cỡ container bổ sung — 23/09/2026

Đã bổ sung `container_size_source: "native_domestic"` để đọc danh mục thật từ
`vwContainerSizeTypeDomestic`. Khóa API là `containerSizeTypeDomesticId`;
giữ nguyên mã nội bộ/ISO, lấy kích cỡ từ `containerSize`, không suy từ tiền tố ISO.
Trong chế độ này, sản lượng cần `container_size_ids_by_cargo` đã đối chiếu với
danh mục; thiếu quan hệ sẽ báo `CONTAINER_SIZE_RELATION_UNCONFIRMED`.

Chế độ mới đã kiểm tra với 814 dòng Cửa Lò và 35 dòng Bến Thủy. Chưa chuyển
profile local cũ hoặc production vì chưa xác minh ánh xạ 20F/40F sang từng loại
container và còn ID trùng khác nội dung giữa hai nguồn. Không thay công thức
tấn/TEU. Chi tiết: [Rà soát file API tham khảo](RA_SOAT_MAIN_API_THAM_KHAO.vi.md).

### Hoàn thiện vận hành — 23/09/2026

Đã áp dụng kiểm tra đơn vị CONT cho cả qua cầu và qua bãi, loại phiếu GIO khỏi
container, thêm mẫu phiếu lỗi và báo cáo đối soát theo ngày/tàu. Người phụ trách
xác nhận bên nhận chưa chốt trường null và ID trùng; công bố sản lượng có trường
null chưa được chấp nhận trong `accepted_null_fields` sẽ bị chặn.

HTTP kiểm tra tuổi dữ liệu mặc định 24 giờ, riêng từng kỳ; bổ sung header CORS
cho phân trang. Có worker trích xuất/công bố theo lịch, khóa chống chạy trùng,
ghi file trạng thái có giới hạn và giữ bản công bố cũ khi lỗi nguồn. Docker CMD
dùng supervisor, nhưng worker vẫn mặc định tắt. Chưa thay đổi Railway hoặc
bật lịch thật. Chi tiết cấu hình, lệnh đối soát và nội dung cần bên nhận chốt:
[Vận hành API Tổng công ty](VAN_HANH_API_TONG_CONG_TY.vi.md).
