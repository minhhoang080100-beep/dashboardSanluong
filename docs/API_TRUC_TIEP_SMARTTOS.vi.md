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
