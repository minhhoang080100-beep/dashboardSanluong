# API đọc trực tiếp SmartTOS

Chế độ `live` nhận yêu cầu có token, đọc SQL Server SmartTOS, kiểm tra dữ liệu
và trả JSON `data`, `code`, `message`. **Mỗi lần gọi API sẽ truy vấn lại SmartTOS**,
kể cả cùng bộ lọc hoặc chuyển trang. Không dùng cache kết quả giữa các yêu cầu;
không cần trích xuất trước, công bố trước hoặc lưu bản sao dữ liệu sản lượng
vào SQLite trên Railway.

Kho tài khoản API, tài khoản dashboard và kế hoạch vẫn dùng volume hiện có.
Không xóa volume khi chuyển chế độ đọc.

## Cấu hình

```env
CORPORATE_API_ENABLED=true
CORPORATE_READ_MODE=live
CORPORATE_SOURCE_PROFILE=/data/corporate-source.json
CORPORATE_SYNC_ENABLED=false
```

`CORPORATE_SOURCE_PROFILE` là file ánh xạ bảng/cột và quy tắc nguồn, không phải
file dữ liệu sản lượng. Có thể dùng `CORPORATE_SYNC_PROFILE` làm đường dẫn dự phòng.
File phải hợp lệ, được rà soát, chỉ rõ cả Cửa Lò và Bến Thủy. Không lấy nguyên
profile cũ có ID tự đặt hoặc tự chấp nhận trường thiếu để bật API.

Giá trị mặc định `CORPORATE_READ_MODE=published` giữ hành vi cũ. Không tự chuyển
sang dữ liệu cũ nếu nguồn live gặp lỗi. Biến chưa đúng giá trị trả lỗi cấu hình.

Khi triển khai, giữ `published` cho đến khi cả backend và frontend mới đã
sẵn sàng; sau đó mới đổi sang `live`. Frontend cũ không hiểu trạng thái
catalog live. Việc đổi chế độ hoặc khởi động lại không xóa tài khoản.
Sau khi đổi bộ đọc, tải lại danh sách API để màn hình dùng đúng cách phân trang.

## Truy vấn và phân trang

- Sản lượng: tối đa 31 ngày, tính cả ngày đầu và cuối, mỗi yêu cầu. Kỳ nhiều
  tháng cần gọi từng khoảng; API không tự coi dữ liệu một tháng là đủ cả năm.
- Danh mục: giữ hợp đồng lọc ngày hiện có; giới hạn 31 ngày chỉ áp dụng sản lượng.
- Chỉ trích xuất danh mục được yêu cầu và những danh mục tham chiếu thực sự cần.
- Mỗi yêu cầu đọc lại nguồn và trả `X-Source-Read-At` của lần đọc hiện tại.
  Kết quả chỉ được xử lý trong yêu cầu đó, không giữ lại để dùng cho yêu cầu sau.
- Phân trang dùng `page`, `limit` cùng các bộ lọc. Không truyền `snapshotId` và
  không trả header `X-Snapshot-Id`. Những header số trang, số dòng, tổng dòng và
  còn trang sau vẫn giữ nguyên. Dữ liệu có thể thay đổi giữa các trang nếu nguồn
  được cập nhật; không bảo đảm các trang thuộc cùng một thời điểm dữ liệu.
- Kết quả mỗi yêu cầu chịu giới hạn bộ nhớ; tối đa 2 tác vụ đọc nguồn cùng lúc.
  Khi bận trả 503 kèm `Retry-After`, không tạo hàng đợi vô hạn.
- Có ngân sách 35 giây cho thao tác SQL; connect/query timeout giảm theo thời
  gian còn lại, kiểm tra giữa các đợt lấy dòng. Đây không phải cam kết toàn bộ
  thời gian HTTP, vì còn xử lý/kiểm tra dữ liệu và truyền kết quả.

Cấu hình Railway hiện tại vẫn giữ một replica/một worker vì tài khoản và kế hoạch
dùng volume SQLite. Bộ đọc live không phụ thuộc phiên phân trang hoặc cache trên
một tiến trình. Bộ đọc `published` vẫn giữ cách phân trang bằng snapshot cũ.

## Kiểm tra dữ liệu

Truy vấn trực tiếp thay cách lấy dữ liệu, không thay định nghĩa chỉ tiêu. Vẫn
kiểm tra nguồn đủ hai xí nghiệp, schema, đơn vị, ID, tham chiếu và các trường null
chưa có xác nhận. Không tự chọn một nguồn khi ID trùng khác nội dung, không coi
trường thiếu là 0 và không trả một phần sản lượng thành kết quả đầy đủ.

Mục Kiểm tra API dùng nhãn **Truy vấn SmartTOS** khi bật live. Nhãn này chỉ cách
đọc, không xác nhận từng danh mục đủ dữ liệu. Chế độ Tài khoản API và Dữ liệu
nội bộ dùng cùng bộ đọc; quyền của từng chế độ vẫn kiểm tra trước truy vấn nguồn.

## Kiểm tra ngày 29/09/2026

Lần kiểm tra trước khi bỏ cơ chế dùng lại kết quả: 998 kiểm thử nhóm API/SQL đạt;
53 kiểm thử bổ sung cho bộ đọc, phân quyền và ngân sách SQL đạt sau thay đổi fetch
theo lô. 192 kiểm thử frontend, lint và build đạt. Đây là kết quả của bản trước,
không xác nhận cơ chế mỗi yêu cầu đọc mới. Các bài kiểm thử dùng nguồn giả lập và
kho tạm, không chứng minh dữ liệu SmartTOS hiện tại đã đủ điều kiện trả cho bên nhận.

Sau khi bỏ dùng lại kết quả: 75 kiểm thử HTTP backend đạt, gồm gọi lặp lại và
trang 2 đọc dữ liệu mới, không có header snapshot. 194 kiểm thử frontend đạt;
lint và build đạt. Giao diện không gửi snapshot ở chế độ live, vẫn kiểm tra
phân trang bằng snapshot cho bộ đọc công bố.

Đã thử mã mới trong thư mục tạm trên Railway, không thay mã service đang chạy
và không ghi kho export. Hai danh mục `origins`, `class` chưa đọc được dữ liệu:
máy chủ không phân giải được tên miền SQL đang cấu hình (`EAI_AGAIN`), trong khi
phân giải `railway.com` thành công. Lệnh kết nối SQL tối thiểu lỗi `HYT00` sau
khoảng 6 giây. Kết quả này không phải bằng chứng mật khẩu sai hoặc dữ liệu rỗng.

Chưa bật `live` trên production. Cần khôi phục phân giải/kết nối máy chủ SQL,
rà soát profile đang áp dụng, rồi thử dữ liệu thật trước khi chuyển chế độ.

## Xử lý cấu hình và lỗi 503 ngày 30/09/2026

Kiểm tra mới trên Railway xác nhận mã `e6c651b` đã có, nhưng bộ đọc vẫn là
`published` và chưa có `CORPORATE_SOURCE_PROFILE`. Kết nối `SELECT 1` đến cả
hai database đã thành công; một số lần kết nối với giới hạn 5 giây vẫn timeout.
Thử riêng bộ đọc live với giới hạn kết nối 10 giây trả được 2 nguồn gốc hàng
và 6 hướng hàng. Đây là kiểm tra dữ liệu thật, không phải dữ liệu mẫu.

Cấu hình chuyển sang đọc trực tiếp dùng `CORPORATE_READ_MODE=live`,
`CORPORATE_SOURCE_PROFILE=/data/corporate-source.json` và
`DB_CONNECT_TIMEOUT_SECONDS=10`. File profile chỉ có các ánh xạ nguồn đã xác minh;
không dùng lại cấu hình thử có mã `CNT-*` tự đặt. Theo xác nhận ngày 30/09,
phạm vi API sản lượng là `nghe_tinh`: loại Cầu 5 theo cầu cập ban đầu.

Bộ đọc danh mục chỉ kiểm tra metadata của các bảng liên quan đến API đang gọi.
Ví dụ, nguồn gốc hàng chỉ cần `CargoOrigin`, hướng hàng chỉ cần `CargoDirect`.
Mỗi yêu cầu vẫn đọc lại SmartTOS, không giữ cache kết quả.

Các lỗi còn lại được phân biệt để xử lý đúng nguyên nhân:

- `SOURCE_MAPPING_REQUIRED`: cấu hình sản lượng còn thiếu cơ sở ngày hoặc cách
  chọn tác nghiệp; dừng trước khi truy vấn SQL.
- `SOURCE_NULL_POLICY_REQUIRED`: còn trường thiếu nguồn và profile chưa cho phép
  để trống. Quyết định cho phép thử API không thay thế xác nhận của bên nhận.
- `SOURCE_ID_CONFLICT`: ID gốc trùng nhưng khác nội dung giữa hai database.
- Các lỗi đơn vị, khối lượng và quan hệ kích cỡ container giữ mã riêng, không
  gộp thành thông báo chung hoặc trả dữ liệu thiếu như một kết quả đầy đủ.

Chuyển chế độ đọc không đồng nghĩa cả 32 API đã đủ dữ liệu. API sản lượng vẫn
cần hoàn thiện các ánh xạ còn thiếu; không tự duyệt trường null hoặc đổi ID nguồn.
263 kiểm thử backend liên quan đến live, HTTP, inspector, nguồn sản lượng và
danh mục đã đạt sau thay đổi này.

## Bổ sung xử lý truy vấn và mã container

Theo xác nhận của chủ dashboard ngày 30/09/2026, chế độ
`container_size_source: "native_cargo"` dùng chính `Cargo.cargoId` và mã hàng
container báo cáo (`20F`, `40F`, `20E`, `40E`, các mã đã hỗ trợ) làm ID và
`localSzTp`. Không gán chúng vào một mã ISO bất kỳ. `isoSzTp`, `heightCode`,
`containerTypeCode` giữ null. Chế độ `native_domestic` vẫn giữ yêu cầu quan hệ
đã xác minh tới `vwContainerSizeTypeDomestic` khi được chọn.

Profile có thể cho phép các trường nullable về nguồn gốc/chủ khai thác/đại lý
để kiểm tra API theo xác nhận này. Tấn, TEU, ngày, mã bắt buộc và các tham chiếu
có giá trị vẫn được kiểm tra. Đây chưa phải xác nhận Tổng công ty chấp nhận
trường thiếu, và không cho phép thay khối lượng chưa có bằng 0.

`quay_selection: "source_statistics"` dùng nhóm thống kê
`SANLUONG-QUACANG` cùng hướng xếp/dỡ từ SmartTOS, không cần duy trì một danh
sách ID phương án trùng lặp trong profile. Phân loại theo nhóm hàng có thể
cấu hình bằng `cargo_kind_by_group`; ánh xạ từng mặt hàng được ưu tiên hơn.
Mặt hàng chưa phân loại vẫn bị chặn, không tự gán tất cả vào hàng rời.

Các thay đổi giảm công việc SQL cho mỗi yêu cầu:

- Chỉ trích xuất endpoint sản lượng được yêu cầu. API hàng rời không đọc
  danh mục kích cỡ container.
- Đọc phiếu và cầu ban đầu bằng hai SELECT riêng rồi ghép theo mã chuyến.
  Chỉ xét các chuyến phát sinh trong kỳ, nhưng lịch sử cầu của mỗi chuyến
  vẫn không bị cắt theo ngày báo cáo.
- Đọc danh mục theo các ID thực sự được tham chiếu tại từng xí nghiệp, kiểm tra
  đúng nguồn và toàn bộ quan hệ nhóm hàng cha. Bản ghi cùng ID ở nguồn không
  phát sinh không chặn kết quả. Nếu cả hai nguồn thực sự dùng cùng ID nhưng
  khác nội dung nghiệp vụ thì vẫn chặn; thiếu ID không được lấy nguồn khác bù.
- Tái sử dụng kết nối SQL trong một yêu cầu và đóng khi yêu cầu kết thúc.
  Mỗi SELECT vẫn đọc mới; không giữ kết quả cho yêu cầu tiếp theo.

Đối chiếu trực tiếp ngày 16/09: 288 phiếu container tại Cửa Lò liên quan 9
`cargoManifestId`, nhưng không có chi tiết tương ứng trong `ContainerManifest`.
Không dùng quan hệ này để tự gán mã ISO hoặc phân bổ khối lượng phiếu.

Phiếu container chưa có hoạt động được loại khi số lượng bằng 0 và khối lượng
nguồn bằng 0; nếu khối lượng null thì còn phải có đơn vị CONT. Phiếu có số lượng
dương nhưng thiếu khối lượng vẫn bị chặn. Bộ đếm loại bỏ chỉ nằm trong chẩn đoán
nội bộ. Sản lượng chưa xác định được cầu đầu tiên không được gán vào Cảng Nghệ
Tĩnh; quy tắc chọn cầu đầu tiên và loại Cầu 5 được giữ nguyên.

Bản ứng viên đã đọc SQL thật từ Railway qua đầy đủ bộ đọc live: container qua
cầu tháng 09/2026 trả 118 dòng tổng hợp; ngày 16/09 có 10 dòng container qua cầu,
8 dòng hàng rời qua cầu và 9 dòng hàng rời qua cổng/bãi. Thời gian từng yêu cầu
khoảng 14–17 giây. Đây là kiểm tra trước triển khai, chưa phải xác nhận HTTP của
service sau cập nhật. 446 kiểm thử liên quan đạt sau các thay đổi cuối cùng.

Không suy ra mọi API hoặc mọi kỳ đều sẵn sàng: danh mục đầy đủ vẫn có thể vướng
ID trùng khác đối tượng; container qua cổng/bãi tháng 9 còn phiếu có số lượng
dương nhưng chưa có khối lượng/kích cỡ xác định. Không tự điền các giá trị này.

## Kiểm tra HTTP sau triển khai ngày 30/09/2026

Đã triển khai `60ec476`, cập nhật profile tại volume và đăng nhập bằng tài khoản
API. Bốn endpoint ngày 16/09/2026 trả HTTP 200 qua domain production:

| Endpoint | Dòng tổng hợp | Tấn | TEU |
|---|---:|---:|---:|
| contQuayVolumesCB | 10 | 8.011,65 | 525 |
| bulkQuayVolumesCB | 8 | 4.962,80 | — |
| bulkGateVolumesCB | 9 | 1.642,46 | — |
| contGateVolumesCB | 3 | 453,25 | 44 |

`containerSize` trả HTTP 200 với 6 mã nguồn. Phản hồi chỉ gồm `data`, `code`,
`message`; không có trường nguồn nội bộ. Phiên kiểm tra đã đăng xuất.

Khi thử cả tháng, SmartTOS có thêm phiếu Bến Thủy `89236` ngày 30/09 chưa chọn
hàng (`cargoId=0`), số lượng 0 và khối lượng 0, chưa được kiểm tra. Phiếu trống
này không được coi là sản lượng thiếu phân loại. Quy tắc bỏ qua phải kiểm tra
đúng cả ba giá trị 0, ngày và phương án hợp lệ; không áp dụng cho phiếu có lượng
dương, khối lượng null, hoặc ID hàng dương nhưng mất bản ghi danh mục.

Các vấn đề nguồn còn phải đối chiếu khi lấy tháng 09:

- Container cổng/bãi: phiếu Cửa Lò `310813`, `310820` ngày 11/09 có số lượng
  container nhưng thiếu khối lượng và mã kích cỡ xác định.
- Cổng/bãi: phiếu Bến Thủy `88166`, `88178` ngày 24/09 ghi 180 ở trường khối
  lượng nhưng `cargoId=0`. Không thể tự xác định đây là container hay hàng rời.
- Hàng rời: có phiếu thiếu khối lượng/đơn vị quy đổi hợp lệ và có phương án chưa
  được chọn. Cần sửa hoặc xác nhận tại nguồn; quyền để null cho trường tùy chọn
  không áp dụng cho tấn, mã hàng, ngày hay phương án bắt buộc.

## Rà soát nguồn và bản ứng viên ngày 12/10/2026

Đã đọc lại hai nguồn SmartTOS cho tháng 09/2026 và khoảng 01–12/10/2026,
rồi đối chiếu bộ trích xuất hiện có với bản ứng viên. Đây là kiểm tra nguồn
và mã ứng viên, **chưa phải xác nhận đã triển khai các thay đổi ngày 12/10**
hoặc mọi API đều sẵn sàng. Các mục ngày 29–30/09 phía trên ghi nhận những lần
kiểm tra trước; số liệu mới có thể khác vì mỗi lần đọc lấy lại dữ liệu nguồn.

Bằng chứng của lần rà soát này:

Các tệp bên dưới chỉ lưu ở máy rà soát trong thư mục `outputs/`, không đưa dữ
liệu nguồn vào Git. Liên kết này không có tệp đính kèm trong repository công khai.

- [Nguồn trước thay đổi](../outputs/api-review-20261012/source_review.result.json).
- [Kết quả bản ứng viên](../outputs/api-review-20261012/candidate_source_review.result.json).
- [Phiếu, đơn vị và hệ số nguồn](../outputs/api-review-20261012/unit_evidence.result.json).
- [Đối chiếu manifest](../outputs/api-review-20261012/manifest_evidence.result.json).

Bản ứng viên bổ sung hai quy tắc hẹp:

- Phiếu đã xác định là hàng rời, có mã hàng dương hợp lệ, chỉ được loại là
  phiếu trống khi **cả số lượng và khối lượng nguồn đều đúng bằng 0**. Trước
  khi loại vẫn kiểm tra phương án, ngày và điều kiện tàu/hướng hàng qua cầu.
  Không cần suy ra đơn vị tấn cho phiếu không có lượng; có bộ đếm riêng
  `excluded_empty_bulk_rows`. Phiếu có số đo null, sai kiểu, âm hoặc khác 0
  không được loại bằng quy tắc này.
- Một số tác nghiệp kho/bãi ghi giờ vào `weightNetSum`, thay vì trường số
  lượng. Ví dụ phiếu Cửa Lò `308824`, `317711`, `318419` có đơn vị trọng lượng
  thực tế `GIO`, số lượng 0 và chưa có đơn vị số lượng. Bản ứng viên đọc thêm
  mã đơn vị của `weightUnitId`; chỉ loại trường hợp này khi mã hàng hợp lệ,
  thuộc kho/bãi, số lượng đúng bằng 0, đơn vị số lượng trống và số giờ hữu hạn,
  không âm. Không áp dụng cho số lượng dương/null, đơn vị khác, hoặc tác nghiệp
  qua cầu. Quy tắc cũ về đơn vị số lượng `GIO` giữ nguyên.

Hai quy tắc không thay công thức tấn, không sửa những khối lượng dương đã có
đơn vị quy đổi hợp lệ và không biến khối lượng thiếu thành 0. Số dòng tổng hợp
giảm khi loại các dòng 0; không được dùng việc giảm số dòng để kết luận đã xử
lý hết dữ liệu thiếu. Kiểm thử hồi quy giữ các trường hợp số đo dương, null,
âm, boolean, NaN, ngày/phương án không hợp lệ và thiếu thông tin tàu trong
nhánh kiểm tra tương ứng, thay vì cho đi qua nhánh loại phiếu trống.

Số phiếu bị chặn bởi `WEIGHT_OR_UNIT_UNAVAILABLE` trong hai lần đọc:

| Kỳ | Endpoint | Trước | Bản ứng viên |
|---|---|---:|---:|
| 01–30/09/2026 | bulkGateVolumesCB | 33 | 14 |
| 01–12/10/2026 | bulkGateVolumesCB | 13 | 7 |
| 01–30/09/2026 | bulkQuayVolumesCB | 24 | 24 |
| 01–12/10/2026 | bulkQuayVolumesCB | 11 | 11 |

Mức giảm lỗi đơn vị tại cổng/bãi đến từ nhận diện giờ ở trường trọng lượng:
19 phiếu tháng 9 và 6 phiếu trong kỳ tháng 10. Bản ứng viên vẫn chưa sẵn sàng
cho hai API hàng rời ở các kỳ trên. Tháng 9 còn 3 phiếu cổng/bãi thiếu phương
án và 8 phiếu chưa phân loại hàng; các lỗi này không được xóa bởi sửa lỗi
đơn vị. Container qua cầu đủ điều kiện ở cấp bộ trích xuất trong cả hai kỳ;
container cổng/bãi tháng 9 vẫn bị chặn. Các kết quả này không thay thế kiểm
tra tham chiếu và HTTP của toàn bộ bộ đọc live sau triển khai.

Những vấn đề còn cần xác nhận nghiệp vụ hoặc bổ sung tại nguồn:

- **GÀU của cát/than tại Bến Thủy:** số 500 của phiếu `87042`, `89249` và 570
  của phiếu `89265` có đơn vị `GÀU`, không phải tấn. Số lượng bằng 0 không đủ
  để loại các phiếu này vì giá trị nguồn dương. Cần quy tắc quy đổi được duyệt
  cho đúng hàng, thiết bị và phạm vi áp dụng, hoặc khối lượng cân thực tế.
- **M3 của gỗ, M2 của cọc bê tông, CAI của pallet:** phiếu `317946`, `309602`,
  `313291` chưa có cơ sở đã duyệt để đưa vào chỉ tiêu tấn. Có bản ghi chuyển
  đổi M3→KG theo hàng gỗ trong danh mục nguồn, nhưng sự tồn tại của một hệ số
  chưa xác nhận được phạm vi áp dụng nghiệp vụ. Không lấy `BaseUnit.TONE=1`
  ở đơn vị `CAI` để mặc định một cái bằng một tấn; không lấy hệ số của mặt
  hàng khác bù vào pallet. Cần duyệt quan hệ nguồn–đích, mặt hàng, giá trị hệ
  số và thời gian áp dụng trước khi mở rộng quy đổi.
- **Hai phiếu thu phí chưa có hàng:** Bến Thủy `88166`, `88178` thuộc phương
  án `TRUC THU PHI`, có `cargoId=0` nhưng ghi 180 ở trường trọng lượng, đơn vị
  `TAN`. Tên phương án gợi ý hoạt động dịch vụ, chưa đủ để tự loại khỏi phạm
  vi sản lượng hoặc gán thành hàng rời. Cần xác nhận phạm vi nghiệp vụ; khi
  chưa phân loại, các phiếu này vẫn có thể chặn cả API container và hàng rời
  cổng/bãi. Chúng chỉ là hai phiếu trong nhóm chưa phân loại, không phải toàn
  bộ 8 phiếu của tháng 9.
- **Container `310813`, `310820`:** mỗi phiếu có số lượng 1, hàng `Khác`,
  thiếu khối lượng và chưa xác định được kích cỡ. Manifest `8166`, `8165`
  cũng thiếu các trường khối lượng cần thiết; không có dòng `ContainerManifest`
  tương ứng trong kết quả đối chiếu. Danh sách mã hàng con của manifest không
  xác định kích cỡ của từng phiếu. Không tự lấy khối lượng định mức, chia
  khối lượng tổng hoặc chọn một kích cỡ để làm API hết lỗi.

Chẩn đoán nội bộ dành cho quản trị viên bổ sung mã lỗi, số lượng lỗi và tối đa
50 mẫu tham chiếu nguồn để tìm đúng phiếu cần đối chiếu. Số đo và mã đơn vị
được kiểm tra theo danh sách/kiểu cho phép trước khi trả qua công cụ Kiểm tra
API; không đưa thông báo driver, SQL hoặc dữ liệu tùy ý vào phản hồi. Chẩn đoán
dùng chính lần đọc phát sinh lỗi, không truy vấn lặp để dựng lại mẫu và không
lưu cache giữa các yêu cầu. Hợp đồng phản hồi API bên ngoài vẫn là `data`,
`code`, `message`; không thêm các mẫu chẩn đoán vào schema công khai.

Trong chế độ **Tài khoản API**, sau lỗi 503 quản trị viên có thể bấm **Xem
phiếu cần kiểm tra** để chủ động gửi một yêu cầu nội bộ mới với đúng bộ lọc
của lần lỗi. Đây là một lần đọc SQL mới; kết quả API/JSON ban đầu vẫn giữ
nguyên. Trong chế độ **Dữ liệu nội bộ**, chi tiết đã đi kèm cùng lần đọc nên
không cần gửi thêm yêu cầu. Đổi bộ lọc hoặc API sẽ hủy chi tiết cũ đang tải.

Kiểm tra tiếp toàn bộ bộ đọc ứng viên trong tiến trình riêng trên Railway,
không thay service đang chạy: `contGateVolumesCB` kỳ 01–12/10 trả 42 dòng,
8.496,84 tấn và 750 TEU sau kiểm tra tham chiếu. `bulkGateVolumesCB` cùng kỳ
giữ lỗi 503 với đúng 7 phiếu Bến Thủy dùng đơn vị GÀU; chẩn đoán trả được
mã phiếu và số đo nguồn. Bằng chứng nội bộ:
`outputs/api-review-20261012/candidate_live_review.result.json`.

1.484 kiểm thử nhóm API backend và 200 kiểm thử frontend đạt; lint và build
frontend đạt. Kiểm tra trình duyệt bằng dữ liệu giả lập ở kích thước 1280px
và 375px xác nhận đọc chi tiết theo thao tác, giữ JSON gốc, hủy yêu cầu cũ
khi đổi bộ lọc và không tràn ngang. Đây chưa phải kiểm thử đăng nhập/giao
diện production với tài khoản quản trị thật.

## Kiểm tra sau triển khai ngày 12/10/2026

Railway đã triển khai thành công mã `e837b28`. Đăng nhập và đăng xuất tài khoản
API qua domain production đều trả 200. Kết quả HTTP đọc mới:

| Endpoint | Kỳ | HTTP | Dòng tổng hợp | Tấn | TEU |
|---|---|---:|---:|---:|---:|
| contQuayVolumesCB | 01–30/09/2026 | 200 | 118 | Chưa cộng toàn bộ trang | Chưa cộng toàn bộ trang |
| contQuayVolumesCB | 01–12/10/2026 | 200 | 22 | 16.107,52 | 1.047 |
| contGateVolumesCB | 01–12/10/2026 | 200 | 42 | 8.496,84 | 750 |
| bulkGateVolumesCB | 01–12/10/2026 | 503 | — | — | — |
| bulkGateVolumesCB | 01–30/09/2026 | 503 | — | — | — |

Hàng rời cổng/bãi kỳ tháng 10 giữ mã `WEIGHT_OR_UNIT_UNAVAILABLE`; kỳ tháng 9
giữ mã `CARGO_KIND_UNMAPPED`. Không trả các dòng đã tính được như một kết quả
đầy đủ. Bốn endpoint ngày 16/09 đều trả 200; tấn và TEU khớp bảng kiểm tra
ngày 30/09 phía trên. Phản hồi công khai vẫn chỉ có `data`, `code`, `message`.
Các lượt gọi trên mất khoảng 9–14 giây; đây là số đo lần kiểm tra, không phải
cam kết thời gian phản hồi.

Vercel đã phục vụ JavaScript/CSS có nút và bảng chẩn đoán mới. Đây là kiểm
tra tài nguyên triển khai, chưa phải đăng nhập giao diện production bằng
tài khoản quản trị. Bằng chứng HTTP và tài nguyên frontend nằm trong
`outputs/api-review-20261012/production-http.result.json` và
`outputs/api-review-20261012/frontend-deployment.json` ở máy rà soát.
