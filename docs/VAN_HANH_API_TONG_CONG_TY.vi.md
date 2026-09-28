# Vận hành và bàn giao API sản lượng S

Bổ sung ngày 25/09/2026: 20 API danh mục vận hành Bulk/Container dùng chung
cơ chế tài khoản máy và kho export, xem [API_DANH_MUC_VAN_HANH.vi.md](API_DANH_MUC_VAN_HANH.vi.md).
Đặt `CORPORATE_SYNC_DOMAIN=operations` để chọn nhóm mới, `all` để chọn cả hai;
mặc định `production` giữ bộ S như trước. Tài khoản cũ cần được cấp quyền mới
tường minh. Phần trạng thái và số liệu ngày 23/09 dưới đây thuộc bộ S.

## Trạng thái ngày 23/09/2026

Đã có 4 API sản lượng, 8 API danh mục và đăng nhập riêng cho máy gọi API.
Không triển khai RORO. API chính thức chỉ đọc dữ liệu đã công bố; không chạy
truy vấn SmartTOS trong mỗi yêu cầu HTTP. JSON vẫn chỉ gồm data/code/message.

Hiện chưa đủ điều kiện bật cập nhật/công bố tự động cho toàn bộ API thật:
người phụ trách xác nhận Tổng công ty chưa chấp nhận trường null và chưa có
quy định xử lý ID trùng giữa Cửa Lò/Bến Thủy. Bộ mã kích cỡ cũng chưa có quan
hệ đủ rõ với mặt hàng 20F/40F. Không tự sửa ID nguồn hoặc điền giá trị giả.

## Kiểm tra dữ liệu

- Container cả qua cầu và qua bãi phải có đơn vị số lượng CONT. Phiếu tính
  giờ GIO được loại khỏi sản lượng container. Đơn vị khác/trống phải đối soát.
- Phiếu thiếu trọng lượng hoặc đơn vị quy đổi không được coi là 0.
- Báo cáo đối soát ghi tổng theo ngày/tàu, số phiếu lỗi, tối đa 50 phiếu mẫu
  mỗi API, ID danh mục xung đột và số dòng còn trường null cần xác nhận.
  Đây là file quản trị riêng, không phải cấu trúc JSON gửi Tổng công ty.
- `accepted_null_fields` trong profile mặc định rỗng. Chỉ bổ sung tên trường
  sau khi bên nhận xác nhận; không thêm để làm mất cảnh báo. Chính sách áp dụng
  cho originId/bulkOriginId, containerOperatorId, shipOperatorId, shipAgentId,
  customerCode của sản lượng. Các thuộc tính tùy chọn của danh mục theo hợp đồng
  hiện tại vẫn nullable.
- Khi công bố, kiểm tra toàn bộ khóa tham chiếu và các kỳ lịch sử đang giữ.
  Lỗi ở một API trong lô thì toàn bộ lô được hoàn tác, bản cũ vẫn còn.

Đối chiếu bản chụp SQL thật ngày 16/09/2026 (không phải lần đọc SQL mới):

| Chỉ tiêu trong các dòng đủ điều kiện tính | Tấn | TEU |
|---|---:|---:|
| Container qua cầu | 8.011,65 | 525 |
| Container qua cổng/bãi | 453,25 | 44 |
| Hàng ngoài container qua cầu | 4.962,80 | — |
| Hàng ngoài container qua cổng/bãi | 1.642,46 | — |

Đây là số để đối soát, chưa khẳng định toàn bộ bản xuất đầy đủ: còn 16 phiếu
container qua cầu thiếu trọng lượng/đơn vị; qua bãi có 5 phiếu thiếu trọng lượng/
đơn vị và 1 phiếu chưa rõ đơn vị số lượng. Phạm vi kho/bãi có thể gồm gom bãi,
rút container và dịch vụ tính phí, chưa tương đương cổng vật lý.

Bằng chứng riêng: `outputs/api-plan-20260921/reconciliation-20260916.json`.
File này và các bản chụp nguồn nằm ngoài Git.

## Trích xuất và đối soát thủ công

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
tin cờ này. Xem [rà soát ngày 27/09](RA_SOAT_API_20260927.vi.md).

## Cập nhật theo lịch

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

Hiện mẫu cấu hình giữ `CORPORATE_SYNC_ENABLED=false`. Chưa đổi biến Railway,
chưa bật lịch trên máy cá nhân, chưa push/deploy. Nếu service có Start Command
ghi đè Docker CMD thì dùng `python -m corporate_api.service` để bật supervisor.
Profile phải đặt trên volume /data theo cấu hình triển khai, không gửi bí mật
qua chat hoặc commit file cấu hình thật vào Git.

## Độ mới và phân trang

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

1. Quy định nhận dạng đối tượng khi hai database có cùng ID nhưng khác nội dung.
   Có thể Tổng công ty cấp bảng mã thống nhất hoặc quy định thêm chiều nhận dạng;
   chưa tự chọn giải pháp nào khi chưa có đặc tả.
2. Những trường nguồn gốc/chủ khai thác/đại lý được phép null và khi nào cần bổ sung.
3. `finishDate` dùng ngày hạch toán ca shiftDate; có phù hợp kỳ báo cáo bên nhận không?
4. “Qua cổng/bãi” nhận toàn bộ tác nghiệp kho/bãi hay chỉ hàng thực sự qua cổng?
5. Phân trang snapshot và các header có được bên nhận hỗ trợ; lịch lấy dữ liệu,
   thời gian tối đa chấp nhận dữ liệu cũ và cách nhận điều chỉnh kỳ trước.

Sau khi chốt: cập nhật profile → trích xuất lại → đối chiếu tổng/ngày/tàu và
tham chiếu → công bố thử có xác thực → đối chiếu bên nhận → mới bật lịch thật.

## Kiểm tra đã thực hiện

188 kiểm thử API đạt, gồm bảo mật/HTTP, dữ liệu nguồn, danh mục, tham chiếu,
công bố, đọc kỳ mới trong kho chứa kỳ cũ, từ chối dữ liệu quá hạn, khóa lịch,
giữ bản cũ khi lỗi và dừng worker khi HTTP kết thúc. 16 kiểm thử runtime
entrypoint cũng đạt. Có hai cảnh báo deprecation từ thư viện kiểm thử.
Bản chụp SQL thật được chạy lại để so sánh tổng; chưa chạy lịch production,
chưa kiểm thử kết nối với hệ thống nhận của Tổng công ty.
