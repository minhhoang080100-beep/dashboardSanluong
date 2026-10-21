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

Backend và frontend phải cùng hỗ trợ chế độ đọc được chọn. Kiểm tra cấu hình
service trước khi chuyển chế độ; việc đổi chế độ hoặc khởi động lại không xóa tài khoản.
Sau khi đổi bộ đọc, tải lại danh sách API để màn hình dùng đúng cách phân trang.

### Quy đổi hàng rời theo danh mục nguồn

Mặc định không bật quy đổi bổ sung. Khóa `bulk_unit_conversion_cargo_ids`
chỉ cho phép kiểm tra đường quy đổi của từng mã hàng trong đúng xí nghiệp.
Ví dụ **giả lập** dưới đây minh họa cấu trúc, không phải cấu hình sản xuất
và không thể dùng làm căn cứ phê duyệt mã hàng:

```json
{
  "bulk_unit_conversion_cargo_ids": {
    "cua_lo": ["1001", "1002"]
  }
}
```

Với mặt hàng bổ sung, cần kiểm tra nguồn và phạm vi nghiệp vụ trước khi
bật. Không thêm `ben_thuy` chỉ vì trùng ID với
Cửa Lò. Giá trị phải là danh sách ID nguồn dạng chuỗi số nguyên dương, không
trùng, tối đa 32 mã mỗi xí nghiệp. Bỏ khóa này hoặc dùng `{}` sẽ giữ hành vi
cũ; hồ sơ vẫn phải thỏa các điều kiện phê duyệt, ngày và phạm vi chung.

Quy tắc bổ sung chỉ được xét cho phiếu đã phân loại là hàng rời, thuộc
endpoint/phạm vi đang yêu cầu, có `weightNetSum` hợp lệ ghi theo đơn vị `M3`
hoặc `VỎ BỊCH` và chưa có `tonne_factor`. Không lấy số lượng thay khối lượng, không thay
những phiếu đã có đơn vị tấn hợp lệ. Các bước kiểm tra gồm:

- Đọc lại đúng `tallyShiftId`; đối chiếu ngày, mã hàng, mã chuyến và số đo với
  lần đọc phiếu. Phiếu phải liên kết tới `CargoManifest` cùng hàng/chuyến và
  `GoodsSpecification` hợp lệ; các bản ghi này không được đánh dấu xóa.
- Với **gỗ ghi M3**, tìm duy nhất ba cạnh đang dùng trong `ConversionUnit`: `M3 → KG` theo đúng
  mã hàng, `KG → TAN` với mã hàng dùng chung `0`, và `TAN → M3` theo đúng mã
  hàng. Cả ba phải cùng quy cách với manifest, nối đúng ID đơn vị, không bị
  xóa/ẩn; đơn vị nguồn/đích không bị xóa. Đơn vị tấn phải có `TONE=1`.
- Hệ số M3 phải hữu hạn, dương và duy nhất. Tích hai cạnh thuận nhân cạnh
  nghịch phải đúng bằng 1. Thiếu cạnh, cạnh trùng hoặc mâu thuẫn không được
  tự chọn một bản ghi để tính. Không dùng cạnh M3 chung của mặt hàng khác.
- Với **Kaly ghi VỎ BỊCH**, cần một cạnh trực tiếp duy nhất `VỎ BỊCH → TAN`
  đúng mã hàng và quy cách manifest. Cạnh phải có hệ số đúng bằng `1`, không
  bị xóa/ẩn; cả hai đơn vị không bị xóa và đều có `TONE=1`. ID đơn vị nguồn
  phải trùng đơn vị trên phiếu, khác ID đơn vị đích. Nhánh này không yêu cầu
  cạnh nghịch vì danh mục đơn vị và cạnh quy đổi trực tiếp cùng xác nhận số
  đo tấn; điều đó không nới điều kiện cạnh nghịch của nhánh M3.
- `createTime` và `updateTime` của mỗi cạnh phải hợp lệ; thời điểm thay đổi
  gần nhất phải trước ngày làm hàng. Thay đổi ngay trong ngày đó cũng bị
  chặn vì `shiftDate` không xác định giờ tác nghiệp. Đây là kiểm tra bảo thủ
  trên danh mục hiện tại, không tái dựng lịch sử hệ số hoặc chứng minh thời
  gian hiệu lực/phê duyệt nghiệp vụ.

Khi đủ các điều kiện, tấn của phiếu M3 bằng `weightNetSum` nhân hai hệ số
thuận; phiếu VỎ BỊCH đủ bằng chứng dùng hệ số `1`. Không làm tròn để khớp báo cáo.
Không suy ra hệ số chung cho mọi mặt hàng từ một đường quy đổi đã kiểm tra.
Quan hệ phiếu–manifest, quy cách và danh mục đơn vị phải cùng xác nhận phép đổi;
đây không phải cách lấy số lượng bao hoặc số lần tác nghiệp nhân định mức.
Mã hàng ở nguồn khác không được tự dùng cùng hệ số. GÀU, M2, các đơn vị đếm
khác, mặt hàng/quy cách không đủ bằng chứng và phiếu thiếu số đo vẫn bị chặn.

Nguồn hỗ trợ được đọc theo lô, tối đa 1.000 phiếu cần quy đổi và 1.000 cạnh
mỗi xí nghiệp/yêu cầu, nằm trong ngân sách đọc SQL hiện có. Không đọc phần
này nếu không bật hoặc không có phiếu phù hợp. Chỉ tái sử dụng dữ liệu hỗ trợ
trong chính yêu cầu đó, không cache cho lần gọi sau, không ghi vào SmartTOS.
Lỗi đọc, thiếu schema hoặc vượt giới hạn vẫn chặn endpoint bulk liên quan;
phiếu chưa chứng minh được đơn vị tiếp tục có `WEIGHT_OR_UNIT_UNAVAILABLE`.
Không công bố subtotal như tổng đầy đủ và không thay lỗi bằng số 0.

Việc bật quy đổi không thay bộ lọc Cầu 5, ngày `shiftDate`, phạm vi phương
án hoặc công thức container. Cần đối chiếu phần được bổ sung với phiếu nguồn
và tổng lũy kế MISBI; không suy ngược hệ số từ phần chênh lệch, thêm số bù,
loại phiếu hợp lệ hoặc đổi kỳ để ép khớp. Kiểm thử mã và kiểm tra nguồn của
bản sửa không đồng nghĩa nghiệm thu MISBI hay được Lakehouse chấp nhận.

## Truy vấn và phân trang

- Sản lượng: tối đa 31 ngày, tính cả ngày đầu và cuối, mỗi yêu cầu. Kỳ nhiều
  tháng cần gọi từng khoảng; API không tự coi dữ liệu một tháng là đủ cả năm.
- GET sản lượng bắt buộc `companyId=CNT`, `startDate`, `endDate` dạng
  `yyyyMMdd`. Mặc định `page=1`, `limit=20`, tối đa `limit=100`. Qua cầu nhận
  thêm `shipId`, `handlingMethodId`; qua cổng/bãi chỉ nhận `handlingMethodId`.
  Không thêm bộ lọc con khi cần đối soát tổng của toàn kỳ.
- Danh mục: giữ hợp đồng lọc ngày hiện có; giới hạn 31 ngày chỉ áp dụng sản lượng.
- Chỉ trích xuất danh mục được yêu cầu và những danh mục tham chiếu thực sự cần.
- Mỗi yêu cầu đọc lại nguồn và trả `X-Source-Read-At` của lần đọc hiện tại.
  Kết quả chỉ được xử lý trong yêu cầu đó, không giữ lại để dùng cho yêu cầu sau.
- Phân trang dùng `page`, `limit` cùng các bộ lọc. Không truyền `snapshotId` và
  không trả header `X-Snapshot-Id`. Những header số trang, số dòng, tổng dòng và
  còn trang sau vẫn giữ nguyên. Dữ liệu có thể thay đổi giữa các trang nếu nguồn
  được cập nhật; không bảo đảm các trang thuộc cùng một thời điểm dữ liệu.
  Đọc `X-Page`, `X-Limit`, `X-Total-Count`, `X-Has-Next` cho đến trang cuối.
  JSON GET công khai chỉ gồm `data`, `code`, `message`; không thêm thông tin
  phân trang, nguồn database hoặc chẩn đoán quản trị vào các dòng dữ liệu.
- Kết quả mỗi yêu cầu chịu giới hạn bộ nhớ; tối đa 2 tác vụ đọc nguồn cùng lúc.
  Khi bận trả 503 kèm `Retry-After`, không tạo hàng đợi vô hạn.
- Có ngân sách 35 giây cho thao tác SQL; connect/query timeout giảm theo thời
  gian còn lại, kiểm tra giữa các đợt lấy dòng. Đây không phải cam kết toàn bộ
  thời gian HTTP, vì còn xử lý/kiểm tra dữ liệu và truyền kết quả.

Cấu hình Railway hiện tại vẫn giữ một replica/một worker vì tài khoản và kế hoạch
dùng volume SQLite. Bộ đọc live không phụ thuộc phiên phân trang hoặc cache trên
một tiến trình. Bộ đọc `published` vẫn giữ cách phân trang bằng snapshot cũ.

### Lọc đúng phạm vi và kiểm tra các trang

Cơ chế lọc và kiểm tra tính đầy đủ không thay công thức tấn/TEU, bộ lọc Cầu 5
hoặc chính sách dữ liệu thiếu. Khi phát hành phải kiểm tra đúng phiên bản đang chạy.

- Khi gọi sản lượng có `shipId`/`handlingMethodId`, áp bộ lọc trước kiểm tra
  từng phiếu, tổng hợp và xác định danh mục tham chiếu. Một phiếu lỗi thuộc
  tàu/phương án khác không còn chặn riêng tập con được yêu cầu. Phiếu thuộc
  tập con vẫn phải qua đủ kiểm tra; không truyền bộ lọc thì vẫn kiểm tra toàn kỳ.
  Qua cổng/bãi chỉ hỗ trợ phương án. ID so chính xác, không đổi mã nguồn.
- Bộ lọc mã tàu/phương án được truyền bằng tham số và áp trước giới hạn dòng
  trong SQL đọc phiếu. Lịch sử cầu của những chuyến được chọn vẫn xét toàn bộ
  lịch sử. Giới hạn dòng tiếp tục áp dụng cho chính tập con được yêu cầu; thay
  đổi này không phải cam kết thời gian đáp ứng trong mọi kỳ.
- Cả bốn API sản lượng trả thêm header `X-Result-Digest`, gồm 64 ký tự hex
  của SHA-256 cho **toàn bộ kết quả sau lọc**. Digest không phụ thuộc page/limit,
  không tính `reportDate`, nhưng phản ánh thay đổi số liệu hoặc chiều nghiệp vụ
  ngay cả khi số dòng giữ nguyên. JSON `data/code/message` không đổi.
- Bên nhận phải giữ digest của trang 1 và so với từng trang sau. Nếu khác,
  thiếu hoặc không hợp lệ, bỏ toàn bộ lượt nhận của API/khoảng ngày đó; không
  nạp subtotal. Sau khi nguồn ổn định, chạy lại từ trang 1 với cùng bộ lọc.
  `verify_delivery` ghi lỗi và không tự thử lại để che việc nguồn đã thay đổi.
- Digest chỉ kiểm tra kết quả trả về, không tạo cache hoặc snapshot SQL chung.
  Mỗi GET vẫn đọc SmartTOS mới; digest giống nhau không chứng minh hai nguồn,
  nhiều API hay nhiều tháng thuộc cùng một giao dịch hoặc cùng mốc chốt.
  Chế độ `published` cũng có digest cho sản lượng và vẫn phải ghim `snapshotId`.

Bộ kiểm tra mới không đánh dấu kỳ đầy đủ khi gọi máy chủ cũ thiếu header
(`RESULT_DIGEST_MISSING`). Cần triển khai backend có header trước khi kiểm tra
đủ kỳ bằng phiên bản CLI này; không bỏ kiểm tra để vượt lỗi. Lệnh và cách đọc
báo cáo ở [hướng dẫn bàn giao](VAN_HANH_API_TONG_CONG_TY.vi.md#lệnh-kiểm-tra-đủ-kỳ-và-đối-chiếu-misbi).

## Kiểm tra dữ liệu

Truy vấn trực tiếp thay cách lấy dữ liệu, không thay định nghĩa chỉ tiêu. Vẫn
kiểm tra nguồn đủ hai xí nghiệp, schema, đơn vị, ID, tham chiếu và các trường null
chưa có xác nhận. Không tự chọn một nguồn khi ID trùng khác nội dung, không coi
trường thiếu là 0 và không trả một phần sản lượng thành kết quả đầy đủ.

Mục Kiểm tra API dùng nhãn **Truy vấn SmartTOS** khi bật live. Nhãn này chỉ cách
đọc, không xác nhận từng danh mục đủ dữ liệu. Chế độ Tài khoản API và Dữ liệu
nội bộ dùng cùng bộ đọc; quyền của từng chế độ vẫn kiểm tra trước truy vấn nguồn.

## Đối soát và tiếp nhận tại Lakehouse

**MISBI là số chuẩn.** Lakehouse trong ảnh người dùng cung cấp là kết quả API
cũ, cần tìm nguyên nhân sai lệch. Đã nhận bảng MISBI theo tháng cho kỳ đối
chiếu và xác nhận phạm vi hai xí nghiệp, loại Cầu 5; ngày hạch toán/kỳ chốt
chưa rõ và số liệu chưa khớp hoàn toàn. Chưa thể nghiệm thu hoặc thay số liệu
để ép khớp báo cáo. Lần kiểm tra GET thành công chỉ xác nhận các điều kiện
kiểm tra của API, không xác nhận bên nhận đã nạp/chấp nhận dữ liệu.

Phạm vi qua cầu đang chọn `production_scope="nghe_tinh"`, loại Cầu 5 theo cầu
cập đầu tiên của cả chuyến, từ đầu lịch sử. Quy tắc `initial-berth-v2` phân biệt
cầu cụ thể với phạm vi sản lượng: nếu các cầu cùng thời điểm đầu tiên đều hợp lệ
và cùng phạm vi thì xác định được phạm vi, dù ID/tên cầu cụ thể vẫn null và trạng
thái cầu là `ambiguous`. Trường hợp lẫn Cầu 5 và cầu khác, thiếu giờ cập thực tế
hoặc cầu đầu tiên không hợp lệ vẫn bị loại khỏi phạm vi và ghi bộ đếm nội bộ;
cần xử lý trong đối soát độ đầy đủ. Không lấy cầu chuyển đến sau để thay cầu đầu.
Qua cổng/bãi theo `gate_selection="vessel_type"` lấy toàn bộ loại kho/bãi đã
cấu hình, không áp cầu đầu tiên. Không cộng tổng gate + quay thành sản lượng
thông qua duy nhất hoặc tự gọi gate là hàng đi qua cổng vật lý.

Lượt lấy năm phải đủ mọi kỳ, mọi trang. Nếu bất kỳ kỳ/API nào lỗi, phần đã nhận
chỉ là subtotal, không phải tổng năm hoàn chỉnh; không dùng số 0 cho kỳ lỗi.
Kiểm tra số dòng, khóa chiều trùng, thay đổi `X-Total-Count`, `X-Result-Digest`
(từ bản sửa 15/10) và ghi thời điểm đọc từng trang. Các kiểm tra này vẫn không
tạo snapshot nhất quán; ưu tiên lấy kỳ
đã chốt và so MISBI theo tháng. Chỉ số đích, phạm vi và ngưỡng sai số phải có
xác nhận, không suy từ tổng Lakehouse hoặc đặt dung sai để bỏ qua chênh lệch.

Hệ thống nhận nên kiểm tra trong vùng tạm trước khi thay thế đúng kỳ/phạm vi
hoặc upsert theo các chiều nghiệp vụ đã thống nhất, có xử lý sửa/xóa. Không nối
thêm nguyên kỳ mỗi lần gọi lại hoặc dùng `reportDate` để cộng lặp cùng sản
lượng. `finishDate` là ngày sản lượng; ngày gọi và thời điểm nguồn chỉ phục vụ
theo dõi lượt đọc. Đây là hướng dẫn tích hợp, chưa phải kiểm chứng quy trình nạp
hiện tại của Lakehouse.

Tài khoản thử được phép có một số trường nullable chưa có nguồn; xác nhận này
không thay thế chấp thuận chính thức của Tổng công ty. Thiếu trọng lượng, mã
bắt buộc, đơn vị hoặc tham chiếu vẫn bị chặn. Retry có giới hạn phù hợp với
`SOURCE_BUSY`, `SOURCE_TIMEOUT`, `SOURCE_UNAVAILABLE` và `Retry-After`; lỗi chất
lượng nguồn phải được sửa trước khi chạy lại. Hướng dẫn thao tác và các điểm
cần chốt: [VAN_HANH_API_TONG_CONG_TY.vi.md](VAN_HANH_API_TONG_CONG_TY.vi.md).

Lệnh `python -m backend.corporate_api.verify_delivery` hỗ trợ lấy đủ bốn API
theo tháng/trang và so bảng MISBI được cung cấp riêng. Xem mục “Lệnh kiểm tra
đủ kỳ và đối chiếu MISBI” trong hướng dẫn trên. Không có bảng chuẩn thì báo
cáo giữ `suppliedComparisonsMatched=false`; thiếu kỳ giữ tổng đầy đủ là null.
`referenceCoverage` chỉ rõ những khoảng và chỉ tiêu chưa có số chuẩn. Cờ khớp
chỉ áp dụng các phép so được cung cấp, không chứng nhận toàn kỳ hoặc tất cả
chỉ tiêu. Lệnh không gửi vào Lakehouse và không xác nhận thay người phụ trách.

## Bổ sung xử lý truy vấn và mã container

Theo lựa chọn nguồn đã xác nhận để thử API, chế độ
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
- Nhánh qua cầu đọc phiếu và cầu ban đầu riêng rồi ghép theo mã chuyến.
  Chỉ xét các chuyến phát sinh trong kỳ, nhưng lịch sử cầu của mỗi chuyến
  vẫn không bị cắt theo ngày báo cáo. Nhánh chỉ đọc gate bằng `vessel_type`
  không đọc metadata/lịch sử cầu vì không dùng điều kiện này; yêu cầu hỗn hợp
  hoặc chế độ `methods` giữ kiểm tra tương ứng.
- Đọc danh mục theo các ID thực sự được tham chiếu tại từng xí nghiệp, kiểm tra
  đúng nguồn và toàn bộ quan hệ nhóm hàng cha. Bản ghi cùng ID ở nguồn không
  phát sinh không chặn kết quả. Nếu cả hai nguồn thực sự dùng cùng ID nhưng
  khác nội dung nghiệp vụ thì vẫn chặn; thiếu ID không được lấy nguồn khác bù.
- Tái sử dụng kết nối SQL trong một yêu cầu và đóng khi yêu cầu kết thúc.
  Mỗi SELECT vẫn đọc mới; không giữ kết quả cho yêu cầu tiếp theo.

Phiếu container chưa có hoạt động được loại khi số lượng bằng 0 và khối lượng
nguồn bằng 0; nếu khối lượng null thì còn phải có đơn vị CONT. Phiếu có số lượng
dương nhưng thiếu khối lượng vẫn bị chặn. Bộ đếm loại bỏ chỉ nằm trong chẩn đoán
nội bộ. Sản lượng chưa xác định được cầu đầu tiên không được gán vào Cảng Nghệ
Tĩnh; quy tắc chọn cầu đầu tiên và loại Cầu 5 được giữ nguyên.

## Giới hạn kiểm chứng và dữ liệu nội bộ

Rà soát tài liệu ngày 21/10/2026. Nhật ký kiểm thử, kết quả HTTP, mã phiếu,
mã chuyến và số liệu đối soát thực tế được giữ trong hồ sơ nội bộ ngoài Git.
Tài liệu công khai mô tả hợp đồng và cách vận hành, không thay thế biên bản
nghiệm thu hoặc xác nhận rằng mọi API và mọi kỳ đều sẵn sàng.

Phiếu có khối lượng nguồn bằng 0 và số lượng dương được ghi nhận bằng chẩn đoán
nội bộ có giới hạn. Khi số 0 và đơn vị hợp lệ, bộ đọc giữ nguyên số đo nguồn;
không tự kết luận thiếu cân, nhân số lượng với định mức hoặc cộng tấn bổ sung.
Chẩn đoán này không thay payload công khai và không tự chặn toàn kỳ.

Danh mục đầy đủ vẫn có thể vướng ID trùng khác nội dung; một tập con sản lượng
đọc thành công không chứng minh mọi danh mục công khai đều đọc được. Dùng
`verify_delivery --verify-references` khi cần kiểm tra cả những mã bên nhận
phải tra cứu, theo hướng dẫn bàn giao. Kiểm thử mã, kiểm tra HTTP và đối chiếu
MISBI là các bước khác nhau; chỉ ghi nhận những bước đã thực hiện cho đúng bản phát hành.
