# Tích hợp có chọn lọc file API tham khảo — 23/09/2026

File `API Vận Hành/main.py` được giữ nguyên. Không thay ứng dụng dashboard,
không đổi công thức tấn/TEU hiện hành, không triển khai lên production trong bước này.

## Đã tích hợp

Thêm nguồn danh mục `container_size_source: "native_domestic"` trong bộ trích xuất
API S. Dữ liệu lấy từ `dbo.vwContainerSizeTypeDomestic`, có kiểm tra schema,
giới hạn số dòng, lọc bản ghi xóa/ẩn, kiểm tra khóa trùng và lỗi nguồn.

| Trường API | Cột nguồn |
|---|---|
| containerSizeId | containerSizeTypeDomesticId |
| localSzTp | containerSizeTypeDomesticCode |
| isoSzTp | containerSizeTypeCode |
| sizeCode | containerSize |
| createdDate / modifiedDate | createTime / updateTime |

Không lấy `containerSizeTypeId` làm khóa dòng mã nội bộ: nhiều mã nội bộ cùng
tham chiếu một kiểu ISO. Không suy chiều dài từ hai ký tự đầu của ISO.
`heightCode` và `containerTypeCode` giữ null khi chưa xác minh cách mã hóa theo
đặc tả; không cắt chuỗi mã nội bộ hoặc nhân chiều cao với 10 để tự tạo mã.

Ở chế độ mới, sản lượng chỉ dùng `container_size_ids_by_cargo` đã đối chiếu:
`{ "cua_lo": { "<cargoId>": <containerSizeTypeDomesticId> } }`.
Mã đích phải tồn tại trong danh mục đang hoạt động và kích cỡ nguồn phải phù
hợp kích cỡ mặt hàng. Thiếu quan hệ sẽ chặn phần container với
`CONTAINER_SIZE_RELATION_UNCONFIRMED`, không dùng cargoId thay cho mã kích cỡ.
Lỗi danh mục container không làm mất nhánh sản lượng hàng rời.

File profile mẫu chọn chế độ mới nhưng để bảng quan hệ rỗng và `approved=false`.
Profile cũ không khai báo chế độ vẫn giữ cách xử lý cũ để không tự đổi tham chiếu
của các bản xem trước. Chưa chuyển bản xem local cũ sang mã kích cỡ mới vì quan
hệ mặt hàng → kiểu container chưa được xác minh. Khi chuyển phải trích xuất lại
cả danh mục và sản lượng; công bố kiểm tra cả tham chiếu dữ liệu lịch sử.

## Bằng chứng SQL thật

Đọc SELECT qua Railway từ SmartTOS và SmartTOS_BenThuy ngày 23/09/2026:

- Danh mục tương ứng 814 và 35 dòng đang hoạt động. Bộ đọc mới đã đối chiếu từng
  ID, mã nội bộ, mã ISO và kích cỡ với bản chụp SQL, không dùng dữ liệu mẫu.
- Kiểu ISO ID 321 có code `45G0`, nhưng `containerSize = '40'`, `containerTeu = 2`.
  Gán mọi mặt hàng 45F/45E vào 321 như file tham khảo là không có căn cứ.
- Ví dụ tại Cửa Lò, mã nội bộ `20GP` (ID 60) và `22G0` (ID 184) cùng trỏ kiểu
  ISO ID 73. Dùng 73 làm khóa cho cả hai sẽ tạo khóa trùng trong danh mục.
- ID mã nội bộ cũng trùng khác nội dung giữa hai database. Việc gộp danh mục
  chính thức bị chặn bằng `SOURCE_ID_CONFLICT`; không thêm tiền tố hay tự chọn
  một nguồn để giải quyết xung đột.
- Schema Cargo và TallyShift đã đọc không có cột containerSizeTypeId hoặc
  containerSizeTypeDomesticId. Không có mã nội bộ khớp đúng `20F/40F/20E/40E/45F/45E`
  trong hai danh mục vừa đọc. Chưa đủ căn cứ gán quan hệ tự động.
- Tại Cửa Lò ngày 16/09, 288 dòng thuộc nhóm container có containerTeuSum trống;
  không thể dùng trường này thay thế cách tính TEU hiện tại.

Các truy vấn metadata tham chiếu diện rộng đã timeout; không kết luận rằng toàn
database không có quan hệ kích cỡ. Phải kiểm tra tiếp quan hệ chứng từ/chi tiết
container trước khi thiết lập ánh xạ sản lượng chính thức.

Bằng chứng riêng (không đưa dữ liệu nguồn vào Git):
`outputs/api-plan-20260921/size-schema.json`, `sizes.json`,
`native-sizes-verified.json`; script đối chiếu `verify_native_sizes.py`.

## Những phần không sao chép từ file tham khảo

- Không dùng vesselId cho originId/bulkOriginId; nguồn gốc hàng vẫn lấy CargoOrigin.
- Không dùng consigneeFullName cho containerOperatorId.
- Không ép TEU về int, không đưa hệ số 45 feet chưa được duyệt vào công thức.
- Không ép cargoParentId về 0; giữ quan hệ cha–con thật.
- Không trả nội dung lỗi SQL/driver ra JSON công khai.
- Không chạy SQL đồng bộ trực tiếp trong mỗi async endpoint. Giữ kiến trúc
  trích xuất/kiểm tra/công bố bản dữ liệu và API đọc bản đã công bố.

Kiểm thử có dữ liệu giả lập bao phủ quan hệ khóa, nhiều mã nội bộ cùng ISO,
mã 45G0 có kích cỡ 40, bản ghi ẩn/xóa, thiếu schema, khóa trùng, xung đột nguồn,
thiếu ánh xạ và trích xuất → công bố → đọc dữ liệu. Việc đối chiếu SQL thật là
bước riêng; không coi kiểm thử giả lập là bằng chứng đã triển khai production.

Kết quả: 175 kiểm thử API S đạt (catalog, production source, native sizes,
publication, end-to-end, HTTP, store, authentication). Hai cảnh báo deprecation
từ thư viện TestClient không làm thất bại kiểm thử. Bản chụp SQL thật đã đối
chiếu riêng đủ 814/35 dòng; bài kiểm tra gộp nguồn cũng xác nhận chặn đúng
xung đột ID thay vì âm thầm ghi đè.
