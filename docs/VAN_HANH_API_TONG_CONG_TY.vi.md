# Vận hành và bàn giao API sản lượng S

## Hướng dẫn vận hành — rà soát tài liệu 21/10/2026

Railway phục vụ chế độ `live`: mỗi GET đọc mới SmartTOS và SmartTOS_BenThuy,
kiểm tra dữ liệu rồi trả kết quả. Không cần nạp/công bố trước, không giữ cache
kết quả giữa các yêu cầu. Chế độ `published` và lịch công bố vẫn có trong mã
nguồn nhưng là lựa chọn riêng, không phải bước bắt buộc của live. Chi tiết:
[API_TRUC_TIEP_SMARTTOS.vi.md](API_TRUC_TIEP_SMARTTOS.vi.md).

Địa chỉ API riêng: **https://apibigdata.up.railway.app**. Hướng dẫn triển khai
và Swagger nằm trong [API_RIENG_RAILWAY.vi.md](API_RIENG_RAILWAY.vi.md).
Máy chủ thiếu header digest được CLI báo chưa kiểm tra đủ, kể cả GET trả HTTP 200.

MISBI là số liệu chuẩn để nghiệm thu; không dùng dữ liệu API cũ trong Lakehouse
làm số đích để chỉnh công thức. Đã nhận MISBI theo tháng cho kỳ đối chiếu,
phạm vi cả hai xí nghiệp và loại Cầu 5. Ngày hạch toán/kỳ chốt vẫn chưa rõ,
số liệu chưa khớp hoàn toàn; chưa xác nhận Lakehouse tiếp nhận đúng.

Phạm vi nhận phải phân biệt:

- Qua cầu: Cảng Nghệ Tĩnh, loại Cầu 5 theo cầu cập ban đầu của cả chuyến;
  chuyển cầu sau đó không thay đổi phạm vi. Các phiếu chưa xác định cầu được
  loại khỏi phạm vi và đếm riêng để đối soát, không tự gán vào Nghệ Tĩnh.
- Qua cổng/bãi: những loại kho/bãi đã cấu hình theo `vesselTypeCode`, độc lập
  với cầu đầu tiên. Chưa mặc nhiên tương đương hàng qua cổng vật lý.
- Không cộng qua cầu và qua cổng/bãi thành tổng thông qua duy nhất: cùng hàng
  có thể xuất hiện ở nhiều công đoạn. Chỉ so từng chỉ tiêu cùng phạm vi MISBI.

### Gọi API và nhận đủ dữ liệu

1. Dùng tài khoản API riêng, POST `/api/login` với `Username`, `Password`.
   Các GET gửi `Authorization: Bearer <accessToken>`; phiên dashboard không
   thay thế token API. Không đưa mật khẩu/token vào URL, nhật ký hoặc Git.
2. Gọi đúng resource với `companyId=CNT`, `startDate`, `endDate` dạng
   `yyyyMMdd`. Sản lượng bắt buộc hai ngày, tối đa 31 ngày tính cả hai đầu.
   Chia năm thành các tháng không chồng ngày. Qua cầu có thể lọc `shipId` và
   `handlingMethodId`; gate chỉ có `handlingMethodId`. Đối soát tổng không
   thêm các bộ lọc con này. Bộ lọc được áp trước kiểm tra phiếu; HTTP 200 của một tập con không chứng minh toàn kỳ đã đủ dữ liệu.
3. Lấy `page=1&limit=100`, sau đó tăng page và giữ nguyên toàn bộ bộ lọc cho
   đến khi `X-Has-Next=false`. Kiểm tra `X-Page`, `X-Limit`, `X-Total-Count`;
   lưu thời điểm `X-Source-Read-At` và `X-Result-Digest` của từng trang.
   Digest là mã SHA-256 gồm 64 ký tự hex cho toàn bộ kết quả sau lọc, bỏ qua
   `reportDate`. **Không truyền snapshotId ở live**, không đợi header
   `X-Snapshot-Id`. Ở published, ghim `X-Snapshot-Id` từ trang 1 vào tham số
   `snapshotId` của các trang sau và vẫn kiểm tra digest.
4. GET thành công có HTTP 200, `code="1"`; JSON chỉ gồm `data`, `code`,
   `message`. Phân trang và chẩn đoán lỗi nằm ở HTTP headers, không có trường
   `sourceDatabase` hoặc metadata quản trị bổ sung trong payload công khai.
   `finishDate` là ngày sản lượng; `reportDate` là ngày đọc nguồn, không dùng
   reportDate làm khóa cộng dồn sản lượng qua nhiều lần lấy.
5. Chỉ cộng tổng khi mọi trang và mọi kỳ cần lấy đều thành công. Kiểm tra số
   dòng nhận đủ, khóa chiều nghiệp vụ trùng, tổng dòng và digest giữa các
   trang. Digest phải giống trang 1, kể cả khi tổng dòng không đổi. Nếu thiếu,
   sai định dạng, thay đổi digest hoặc phiên published đổi: bỏ toàn bộ lượt
   nhận của API/khoảng ngày đó, đợi nguồn ổn định rồi lấy lại từ trang 1.
   Không ghép các trang trước và sau thay đổi. Live vẫn đọc nguồn mới: digest
   giống nhau không chứng minh một giao dịch SQL chung giữa hai nguồn, các
   API hoặc các kỳ. Ưu tiên kỳ đã chốt và đối chiếu lại theo tháng.
6. Bên nhận nên nạp vào vùng tạm, kiểm tra đủ kỳ rồi thay thế đúng kỳ/phạm vi
   hoặc upsert theo các chiều nghiệp vụ. Không nối thêm nguyên kỳ mỗi lần
   gọi lại. Cần thống nhất cách nhận điều chỉnh/xóa trước khi chạy lịch.

Nếu có lỗi, giữ nguyên dữ liệu đã nghiệm thu ở bên nhận; không ghi đè bằng
danh sách rỗng hoặc subtotal. Lỗi trả `code="0"`; đọc `X-Error-Code`.
`SOURCE_BUSY`, `SOURCE_TIMEOUT`, `SOURCE_UNAVAILABLE` có thể thử lại có giới
hạn theo `Retry-After`. Các lỗi khối lượng, mã hàng, đơn vị, ID xung đột hoặc
tham chiếu cần sửa nguồn/cấu hình; retry không tự giải quyết. HTTP 200 của API
chỉ xác nhận yêu cầu đã qua kiểm tra hiện hành, chưa chứng minh MISBI khớp hoặc
Lakehouse đã nhập thành công.

Ở live, mã tàu/phương án không tồn tại trả HTTP 422,
`X-Error-Code: FILTER_ID_NOT_FOUND`; cần sửa mã lọc, không retry cùng mã.
Mã có thật nhưng không phát sinh trong kỳ trả HTTP 200 với `data: []`.
Nếu danh mục nguồn không đọc đủ, API trả 503 thay vì kết luận không phát sinh.

### Chuyển ID bộ S sang phân biệt nguồn

Mã cập nhật ngày 22/10/2026 hỗ trợ profile
`source_identity_mode="terminal_prefixed_v1"`; **chưa xác nhận đã kích hoạt
trên production**. Bỏ khóa hoặc đặt `"native"` giữ cách trả ID cũ. Trong chế
độ mới, `CL:<ID>` thuộc Cửa Lò, `BT:<ID>` thuộc Bến Thủy; ví dụ giả lập
`CL:123` và `BT:123` phải được lưu thành hai khóa khác nhau.

Phạm vi gồm tám danh mục S (`shipDetails`, `customers`, `cargoType`,
`cargoCategory`, `handlingMethodList`, `class`, `origins`, `containerSize`),
quan hệ giữa các danh mục và các khóa tham chiếu có giá trị trong bốn API sản
lượng. `/api/oprt/` giữ nguyên. ID nguồn và ánh xạ profile theo xí nghiệp vẫn
là ID gốc. `companyId=CNT`, tấn/TEU, chính sách null và JSON ba trường không
đổi; không bổ sung `sourceDatabase`. Số dòng tổng hợp có thể tăng khi hai nguồn
được tách đúng định danh, nhưng việc đổi ID không thay tổng số đo.

1. Triển khai bản mã có hỗ trợ chế độ mới trước khi đổi profile. Kiểm tra đúng
   service/bản phát hành; việc commit hoặc cập nhật tài liệu chưa kích hoạt nó.
2. Chuẩn bị bên nhận lưu nguyên chuỗi ID, gồm `CL:`/`BT:`. Không ép sang số,
   cắt tiền tố hoặc tự đổi ID. Khi bật chế độ mới, tra cứu và bộ lọc phải dùng
   ID API đầy đủ; ID trần hoặc sai cú pháp bị từ chối trước khi đọc nguồn.
3. Chọn chế độ trong profile đã kiểm tra, dùng danh mục `native_groups` và
   kích cỡ `native_cargo` hoặc `native_domestic` có quan hệ đã xác minh. Không
   thêm tiền tố vào các allowlist, ánh xạ phân loại hoặc quy đổi nguồn.
4. Tạm dừng lượt nhận đang dở; nạp lại cả danh mục và sản lượng trong phạm vi
   chuyển đổi bằng cùng chế độ. Kiểm tra khóa ngoại, đủ trang và digest; không
   ghép trang trước/sau đổi chế độ, không nối dữ liệu mới vào phân vùng còn ID
   `native`. Thay thế phân vùng sau khi kiểm tra, để tránh ghi nhận hai lần.
5. So tổng tấn/TEU trước/sau trên cùng kỳ và cùng dữ liệu nguồn; kiểm tra tra
   cứu danh mục theo chính ID có trong sản lượng. Chỉ tiếp tục lịch nhận sau
   khi bên nhận xác nhận giữ đúng khóa và quan hệ. Nếu quay về `native`, cũng
   chuyển lại đồng bộ danh mục và sản lượng; không chỉ đổi riêng profile.

Đây là thay đổi định danh đã được người phụ trách chọn, không phải xác nhận
MISBI đã khớp hoặc Tổng công ty đã nghiệm thu chính sách null.

20 API danh mục vận hành Bulk/Container dùng chung cơ chế tài khoản máy, xem
[API_DANH_MUC_VAN_HANH.vi.md](API_DANH_MUC_VAN_HANH.vi.md). Tài khoản cũ cần
được cấp quyền resource mới tường minh. `CORPORATE_SYNC_DOMAIN` chỉ điều khiển
nhóm trích/công bố khi dùng lịch published, không bật thêm quyền tài khoản.

### Lệnh kiểm tra đủ kỳ và đối chiếu MISBI

Từ gốc dự án, dùng Python đã cài dependencies. Thiết lập tài khoản API bằng
hai biến môi trường tiến trình `CORPORATE_API_USERNAME`, `CORPORATE_API_PASSWORD`
qua kênh quản lý bí mật; không gõ mật khẩu vào tham số lệnh hoặc lưu vào báo cáo.
CLI cũng nhận JSON `Username`/`Password` từ stdin khi thêm `--credentials-stdin`.

```powershell
& .\.venv-audit\Scripts\python.exe -m backend.corporate_api.verify_delivery `
  --base-url https://apibigdata.up.railway.app `
  --start 2026-09-01 --end 2026-09-30 `
  --production-scope nghe_tinh `
  --output outputs/api-delivery-20261015/check-september-01.json
```

Lệnh dùng môi trường `.venv-audit` hiện có tại dự án. Nếu chạy trên máy khác,
thay đường dẫn Python bằng môi trường đã cài `backend/requirements.txt`.
Ví dụ kiểm tra một tháng trước; để kiểm tra từ đầu năm đến 15/10, đổi thành
`--start 2026-01-01 --end 2026-10-15` và chọn tên output khác. Không đặt thông
tin đăng nhập trong tài liệu hoặc sao chép mật khẩu vào lệnh mẫu.

Lệnh chỉ đăng nhập, GET cả bốn API sản lượng lần lượt theo tháng/trang 100 dòng và
đăng xuất; không ghi SmartTOS, không công bố hoặc đẩy dữ liệu vào Lakehouse.
Khoảng chạy tối đa 366 ngày; mỗi yêu cầu vẫn tối đa 31 ngày. Chọn tên output mới
mỗi lần vì lệnh từ chối ghi đè. Báo cáo lưu số dòng, tổng, thời điểm đọc và mã
lỗi; không lưu token, mật khẩu hoặc payload từng dòng.
Ngày kết thúc `9999-12-31` bị từ chối bằng `INVALID_PERIOD` trước khi đăng nhập,
vì không thể biểu diễn ngày kế tiếp cần dùng cho ranh giới kỳ.

CLI bắt buộc digest hợp lệ trên mọi trang sản lượng, kể cả kỳ chỉ có một
trang hoặc không có dòng. Với published, CLI tự truyền `snapshotId` nhận ở
trang 1; với live, không truyền tham số này. Không có tự động retry trong
lượt kiểm tra: gặp nguồn thay đổi thì kỳ đó có `totals=null`. Chạy lại bằng
tên output mới sau khi xử lý nguyên nhân, không sửa báo cáo cũ thành thành công.

Bản CLI cập nhật ngày 19/10 giữ mã lỗi công khai trong `pages[].serverErrorCode`,
gồm lỗi dữ liệu cũ và danh mục tham chiếu chưa sẵn sàng. Nếu máy chủ trả
`Retry-After` là số giây nguyên từ 0 đến 86.400, báo cáo ghi
`pages[].retryAfterSeconds` để người vận hành biết thời gian chờ được đề nghị.
Đây chỉ là thông tin xử lý lỗi; CLI không tự gửi lại yêu cầu. Mã lỗi chưa biết,
header tự do và nội dung lỗi máy chủ không được chép vào báo cáo. Tổng kỳ lỗi
vẫn là `null`, kể cả khi máy chủ có gợi ý thử lại.

Để kiểm tra bên nhận có tra được danh mục mà sản lượng tham chiếu hay không,
thêm `--verify-references --reference-limit 200` vào lệnh trên. Tài khoản cần
quyền đọc các danh mục S liên quan, ngoài bốn API sản lượng. Nên bắt đầu bằng
một ngày đã đối chiếu. CLI chỉ kiểm tra các kỳ đã lấy đủ trang và digest;
gom ID dùng chung trong lượt chạy để không gửi lại cùng một yêu cầu. Danh mục
container dùng bộ lọc `containerSizeId`; các danh mục khác dùng đường dẫn tra
một ID. Quan hệ hàng–nhóm hàng và hàng cha cũng được kiểm tra. Phản hồi phải
chứa đúng một ID, đúng kiểu dữ liệu JSON và kết thúc ở trang 1.

Với hai API bulk, CLI còn so `cargoTypeId` của từng cặp
`cargoCategoryId`/`cargoTypeId` trong sản lượng với `cargoTypeId` của danh mục
`cargoCategory` công khai. Cả danh mục hàng và các loại hàng liên quan phải tra
thành công mới kết luận `REFERENCE_RELATION_MISMATCH`; lỗi hoặc giới hạn tra
cứu được giữ là chưa kiểm tra được, không suy thành lệch quan hệ. Không yêu cầu
hàng cha phải có cùng `cargoTypeId` với hàng con. Các số
`checkedRelationCount`, `mismatchedRelationCount`, `uncheckedRelationCount` trong
`windows[].publicReferences` đếm cặp khác nhau đã kiểm, bị lệch và chưa kiểm
được; kỳ chưa lấy đủ sản lượng không thực hiện phép so này.

Xem `windows[].publicReferences.status`: `passed` là tra đủ các tham chiếu
công khai và các cặp hàng/loại hàng trực tiếp khớp, `blocked` là có lỗi hoặc
vượt giới hạn, `skipped_incomplete` là
chưa kiểm tra vì sản lượng kỳ đó chưa lấy đủ. Các số tổng, `retrievalComplete`,
phép so MISBI và `status` cũ giữ nguyên; lỗi danh mục không biến số liệu đã đọc
thành số 0. CLI trả mã thoát 2 nếu bật kiểm tra này mà không đạt, kể cả khi
sản lượng trả HTTP 200. Mẫu lỗi tối đa 10; chỉ giữ mã lỗi an toàn và ID đúng
dạng số hoặc `CL:<ID>`/`BT:<ID>`, không lưu nội dung danh mục, thông tin liên
hệ hay lỗi tự do từ máy chủ.

Giới hạn mặc định là 200 yêu cầu danh mục tuần tự, có thể đặt từ 1 đến 2.000;
mỗi yêu cầu có timeout 60 giây nên không nên tăng giới hạn ngay cho kỳ dài.
Mỗi kỳ đã lấy đủ cũng giữ tối đa `--reference-limit` cặp hàng/loại hàng khác
nhau; giới hạn cặp này độc lập với số yêu cầu GET và không phát sinh thêm GET.
Nếu còn cặp chưa giữ được, `relationCountIsLowerBound=true` và kỳ bị `blocked`,
kể cả khi mọi ID đã tra được. Vượt giới hạn được ghi rõ, không tự bỏ hàng lỗi
hoặc thử lại. Kết quả này
không xác nhận MISBI đã khớp, không phê duyệt trường `null`, không giải quyết
xung đột ID và không đồng nghĩa Tổng công ty đã chấp nhận dữ liệu.

| Lỗi trong báo cáo kiểm tra | Cách xử lý |
|---|---|
| `RESULT_DIGEST_MISSING` | Máy chủ chưa trả header hoặc proxy đã loại header; cập nhật/kiểm tra đường HTTP rồi lấy lại từ trang 1 |
| `RESULT_DIGEST_INVALID` | Header sai định dạng; kiểm tra backend/proxy, không tự bỏ kiểm tra |
| `RESULT_CHANGED_BETWEEN_PAGES` | Kết quả thay đổi trong lượt lấy; bỏ toàn bộ API/kỳ đó và lấy lại từ trang 1 khi nguồn ổn định |
| `SNAPSHOT_CHANGED_BETWEEN_PAGES` | Phiên published thay đổi hoặc chế độ đọc đổi; không ghép phiên, lấy lại từ trang 1 |
| `HTTP_ERROR` | Đọc `serverErrorCode` của trang nếu có; lỗi nguồn bắt buộc cần xử lý trước khi thử lại |
| `REFERENCE_RELATION_MISMATCH` | Các ID đều tra được nhưng loại hàng trong sản lượng khác danh mục hàng; đối chiếu nguồn và cách ánh xạ trước khi bàn giao, không tự đổi ID hoặc bỏ dòng |

Khi có MISBI đã xác nhận, thêm `--benchmark <duong-dan-misbi.json>`. File gồm
`source="MISBI"`, `approved=true`, `referenceDate` dạng `YYYY-MM-DD` và danh
sách `comparisons`. Mỗi phép so nêu `startDate`/`endDate` dạng `yyyyMMdd`,
`scope`, `resources`, `metric` (`tonnes` hoặc `TEU`), `expected`, `tolerance`.
Số có thể ghi bằng chuỗi thập phân dùng dấu chấm. Giá trị, phạm vi, ngày và
dung sai phải theo báo cáo đã xác nhận; không tự dùng tổng trong ảnh để lập
bảng chuẩn tháng 1–8 hoặc cả năm. Không đặt `approved=true` khi chưa có xác nhận.
Các phép so quay và gate phải tách riêng; gate dùng scope `all_yards`, TEU chỉ
áp dụng API container. `--production-scope` là khai báo của người chạy, không
đổi profile Railway và không chứng minh phạm vi qua hợp đồng JSON công khai.

Đọc kết quả:

- `retrievalComplete=true`: đã lấy đủ bốn API trong toàn khoảng kiểm tra;
  các trang của từng API/kỳ có cùng digest hợp lệ. Chưa tự chứng minh MISBI
  khớp, chính sách null được duyệt hoặc bên nhận đã nạp.
- `suppliedComparisonsMatched=true`: những phép so MISBI được cung cấp đều
  khớp. Cờ này độc lập với việc lấy đủ cả bốn API và không chứng minh bảng chuẩn
  bao phủ toàn kỳ hoặc mọi chỉ tiêu. Khi đồng thời lấy đủ, trạng thái là
  `supplied_references_matched`; thiếu dữ liệu vẫn giữ trạng thái `incomplete`.
- `referenceCoverage`: liệt kê các khoảng có/chưa có bảng chuẩn cho từng API,
  chỉ tiêu và phạm vi. `fullPeriodCovered` chỉ nói về độ bao phủ bảng chuẩn,
  không nói các giá trị đã khớp. Phép so tổng tấn gộp nhiều API được ghi riêng
  trong `groupedComparisonIndexes`, không xác nhận từng thành phần đã đúng.
- Không có benchmark: `suppliedComparisonsMatched=false` kể cả GET đều thành công.
  API/kỳ thiếu dữ liệu có `totals=null`; `receivedSubtotal` chỉ để chẩn đoán.
- Mã thoát 0: lấy đủ và nếu có benchmark thì các phép so khớp; mã 2: thiếu dữ liệu
  hoặc lệch; mã 1: lỗi cấu hình, xác thực hoặc file. Không dùng mã 0 riêng
  lẻ làm dấu nghiệm thu chính thức. `consistentSnapshot=false` nghĩa báo cáo
  không khẳng định có một snapshot chung cho toàn bộ API/kỳ; published ghim
  được phiên riêng của từng API/kỳ, live vẫn đọc độc lập từng yêu cầu.

### Ghi nhận bằng chứng kiểm tra

Lưu báo cáo kiểm thử, mã phiên bản, cấu hình phạm vi, thời điểm đọc từng kỳ và
kết quả HTTP vào thư mục nội bộ ngoài Git. Không đưa mã phiếu, dữ liệu khách hàng,
số liệu sản lượng thực tế hoặc thông tin đăng nhập vào tài liệu công khai.

Kiểm thử tự động chỉ xác nhận các trường hợp được kiểm tra. HTTP thành công của
một kỳ hoặc một bộ lọc không chứng minh cả năm đầy đủ, tổng khớp MISBI hay bên
nhận đã nạp đúng. Các kỳ lỗi giữ `totals=null`; không thay bằng số 0.

## Kiểm tra dữ liệu

- Container thường dùng đơn vị số lượng `CONT`. Ngoại lệ hẹp từ bản sửa
  14/10: nhánh kho/bãi chọn bằng `vessel_type`, chế độ kích cỡ `native_cargo`,
  hàng có tên nguồn đúng `Container` và đơn vị `CONT 20E`/`CONT 20F` được đối
  chiếu tới ID hàng `20E`/`20F` đang hoạt động, duy nhất trong cùng nguồn và
  cùng nhóm hàng. Giữ nguyên khối lượng thực ghi, không nhân định mức. Không
  mở rộng ngoại lệ cho hàng đã có tên `20E`/`20F` hoặc nhánh qua cầu. Phiếu
  tính giờ GIO bị loại; đơn vị khác/trống hoặc quan hệ chưa rõ phải đối soát.
- Phiếu thiếu trọng lượng hoặc đơn vị quy đổi không được coi là 0.
- Báo cáo đối soát ghi tổng theo ngày/tàu, số phiếu lỗi, tối đa 50 phiếu mẫu
  mỗi API, ID danh mục xung đột và số dòng còn trường null cần xác nhận.
  Đây là file quản trị riêng, không phải cấu trúc JSON gửi Tổng công ty.
- `accepted_null_fields` trong profile mẫu mặc định rỗng. Chủ dashboard đã
  cho phép một số trường thiếu để thử API; Tổng công ty chưa xác nhận chính
  sách null cho tiếp nhận chính thức. Không dùng cấu hình thử làm bằng chứng
  đã được bên nhận chấp thuận. Chính sách áp dụng
  cho originId/bulkOriginId, containerOperatorId, shipOperatorId, shipAgentId,
  customerCode của sản lượng. Các thuộc tính tùy chọn của danh mục theo hợp đồng
  hiện tại vẫn nullable.
- Sản lượng live kiểm tra mã tham chiếu trong đúng nguồn phát sinh trước khi gộp.
  Khi dùng published, công bố còn kiểm tra các kỳ lịch sử đang giữ.
  Lỗi ở một API trong lô thì toàn bộ lô được hoàn tác, bản cũ vẫn còn.

## Trích xuất và đối soát thủ công

Dùng bảng đối soát nội bộ của lần đọc nguồn cần xử lý. Giữ nguyên các cột nguồn;
nhân viên bổ sung kết luận, chứng từ và người xác nhận. Bảng ghi nhận số liệu ở
thời điểm đọc, không tự cập nhật khi SmartTOS thay đổi. Việc điền bảng chưa sửa
SmartTOS và chưa làm API hết lỗi. Sau khi sửa nguồn hoặc chốt quy tắc nghiệp vụ
có bằng chứng, chạy lại bộ đọc, lấy đủ các trang và đối chiếu với MISBI.
Không tự quy đơn vị đếm, thể tích hoặc số phân bổ công việc thành tấn để bỏ lỗi.

Chạy từ thư mục gốc dự án, dùng Python trong môi trường của dự án:

```powershell
python -m backend.corporate_api.manage_exports extract `
  --profile backend/.data/corporate-profile.json `
  --start 2026-09-16 --end 2026-09-16 `
  --output backend/.data/preview-20260916.json `
  --report backend/.data/reconciliation-20260916.json
```

Không ghi đè các file trên; chọn tên khác khi chạy lại. Báo cáo phân biệt
`extractionReady` (đủ dữ liệu trích xuất) và `deliveryReady` (qua kiểm tra schema,
kỳ dữ liệu, chính sách null và tham chiếu từ các danh mục trong preview). Danh mục
tham chiếu không nằm trong preview được ghi là chưa xác minh, kể cả khi đã có trong
kho công bố. Công bố vẫn kiểm tra lại toàn lô và các danh mục trong kho, không chỉ
tin cờ này. Xem [hướng dẫn đọc trực tiếp](API_TRUC_TIEP_SMARTTOS.vi.md).

## Chế độ published tùy chọn: cập nhật theo lịch

Không bật lịch này chỉ để chạy GET live. Live giữ `CORPORATE_SYNC_ENABLED=false`.
Các lệnh dưới đây chỉ áp dụng khi chủ động trích/công bố vào kho export.

Chạy một lần để kiểm tra 7 ngày gần nhất, chưa công bố:

```powershell
python -m backend.corporate_api.sync --profile backend/.data/corporate-profile.json --days 7 --check-only
```

Bỏ `--check-only` để trích xuất và công bố khi mọi điều kiện đều đạt. Thêm
`--interval 900` để lặp sau mỗi 15 phút kể từ khi lần trước kết thúc. Phạm vi
1–31 ngày, mặc định 7 ngày để bắt các sửa đổi gần đây. Dữ liệu cũ hơn khoảng
này phải được trích xuất lại riêng; lịch không tự rà lại toàn bộ lịch sử.
Có khóa hệ điều hành để ngăn hai tiến trình lịch chạy đồng thời trên cùng kho.
Lỗi/timeout không công bố dữ liệu dở dang. Lần kế tiếp thử lại sau khoảng lịch.

Mỗi lần chỉ lưu một file trạng thái mới nhất cạnh kho export, mặc định
`corporate-exports.sync-status.json`; không tích lũy file chụp nguồn mỗi lần.
Lỗi trích xuất/đối soát được ghi khi còn giữ khóa tác vụ, tránh giữ nhầm trạng thái
thành công cũ hoặc ghi đè tác vụ đang chạy. `actualPublicationValidated=true` cho
biết bước kiểm tra công bố với kho thật đã qua; `checkOnly=true` nghĩa là chưa
công bố: nhánh này thử toàn bộ kiểm tra trong giao dịch rồi rollback, bao gồm
tham chiếu từ dữ liệu đã có. `deliveryReady` của từng mục vẫn phản ánh phạm vi preview, nên có thể
false khi chạy riêng một API mà danh mục cha đã có sẵn trong kho.
Kho export giữ tối đa 8 phiên bản mỗi API theo cấu hình hiện hành. Nhật ký
stdout chỉ có trạng thái và số dòng, không ghi SQL, mật khẩu hay payload.

Container dùng `python -m corporate_api.service`, chạy HTTP và tiến trình cập
nhật riêng trên cùng volume. Tiến trình cập nhật không chặn luồng HTTP. Chỉ bật
khi có profile đã đối soát bằng các biến:

```text
CORPORATE_SYNC_ENABLED=true
CORPORATE_SYNC_PROFILE=/data/corporate-profile.json
CORPORATE_SYNC_INTERVAL_SECONDS=900
CORPORATE_SYNC_LOOKBACK_DAYS=7
CORPORATE_MAX_SOURCE_AGE_SECONDS=86400
```

Mẫu cấu hình giữ `CORPORATE_SYNC_ENABLED=false`. Những lệnh này không tự đổi
biến Railway hoặc tạo lịch trên máy cá nhân. Nếu service có Start Command
ghi đè Docker CMD thì dùng `python -m corporate_api.service` để bật supervisor.
Profile phải đặt trên volume /data theo cấu hình triển khai, không gửi bí mật
qua chat hoặc commit file cấu hình thật vào Git.

## Chế độ published tùy chọn: độ mới và phân trang

Các quy tắc snapshot/tuổi dữ liệu trong mục này chỉ áp dụng published. Ở live
đọc lại nguồn cho mỗi trang và không nhận snapshotId, như hướng dẫn đầu tài liệu.

- HTTP mặc định từ chối dữ liệu quá 24 giờ kể từ lần đọc nguồn, trả HTTP 503,
  code `0`, header `X-Error-Code: DATASET_STALE` và `Retry-After: 300`.
- Theo dõi thời điểm nguồn riêng cho mỗi khoảng ngày. Cập nhật ngày mới không
  làm mới giả thời điểm của kỳ cũ. Kho cũ tự bổ sung metadata, không xóa dữ liệu.
- Với kỳ lịch sử quá hạn, trích xuất lại kỳ đó trước khi cung cấp. Ngưỡng
  `CORPORATE_MAX_SOURCE_AGE_SECONDS` có thể thống nhất lại theo lịch nhận dữ liệu;
  giá trị 0 tắt kiểm tra tuổi dữ liệu, chỉ nên dùng cho bản xem đối soát cố định.
- Lấy `X-Snapshot-Id` ở trang 1 rồi truyền `snapshotId` cho trang 2 trở đi.
  Phiên hết hạn trả 410; bắt đầu lại trang 1. Không trộn các phiên.
- Dùng `X-Has-Next` và `X-Total-Count` để lấy đủ dòng. Header phân trang,
  thời điểm nguồn và mã lỗi đã được expose cho các origin CORS được phép.
- HTTP 200/data rỗng chỉ có nghĩa kỳ đã công bố không có dòng phù hợp.
  Kỳ chưa đủ dữ liệu trả PERIOD_NOT_READY; không giả thành danh sách rỗng.

## Nội dung cần Tổng công ty xác nhận

1. Bên nhận giữ nguyên ID dạng chuỗi `CL:<ID>`/`BT:<ID>` khi chuyển sang
   `terminal_prefixed_v1`, đồng bộ danh mục với sản lượng và thay thế đúng phân
   vùng dữ liệu cũ. Người phụ trách đã chọn cách phân biệt nguồn này; khả năng
   tiếp nhận và lịch chuyển đổi của Tổng công ty vẫn cần được kiểm tra.
2. Những trường nguồn gốc/chủ khai thác/đại lý được phép null và khi nào cần bổ sung.
3. `finishDate` dùng ngày hạch toán ca shiftDate; có phù hợp kỳ báo cáo bên nhận không?
4. “Qua cổng/bãi” nhận toàn bộ tác nghiệp kho/bãi hay chỉ hàng thực sự qua cổng?
5. Bên nhận có đọc đủ header phân trang ở live; lịch lấy dữ liệu, thời điểm chốt
   và cách nhận sửa/xóa kỳ trước. Nếu cần snapshot nhất quán, phải thống nhất
   thêm cơ chế thay vì giả định nhiều lần GET live cùng một bản chốt.
6. Mốc chốt và ngày hạch toán của bảng MISBI theo tháng đã nhận, định nghĩa
   chỉ tiêu tấn/TEU; quy đổi GÀU/M³/M²/CAI và cách xử lý phiếu thiếu mặt hàng/khối lượng.
   Phạm vi đối chiếu hai xí nghiệp và loại Cầu 5 đã được người phụ trách xác nhận.

Sau khi chốt: sửa nguồn hoặc cập nhật profile có bằng chứng → GET lại qua tài
khoản API → đối chiếu đủ kỳ với MISBI → chạy thử bên nhận và kiểm tra dữ liệu
đã nạp → mới bật lịch lấy thật. Không sửa giá trị để ép khớp một tổng báo cáo.
