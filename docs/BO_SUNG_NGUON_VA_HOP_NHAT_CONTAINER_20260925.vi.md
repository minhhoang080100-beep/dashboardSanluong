# Bổ sung nguồn và xử lý danh mục container — 25/09/2026

Phạm vi là các danh mục vận hành Bulk/Container còn vướng sau [lần rà soát 20 API](RA_SOAT_TUNG_API_VAN_HANH_20260925.vi.md). Truy vấn SQL chỉ đọc, chạy qua Railway; các bảng được đọc tại những thời điểm khác nhau, không phải một ảnh chụp giao dịch đồng thời. Không sửa dữ liệu SmartTOS, thay công thức sản lượng hoặc triển khai production.

## 1. Kích cỡ và loại container

Nguồn `ContainerSizeType` có 740 dòng tại Cửa Lò và 411 dòng tại Bến Thủy, gồm cả xóa mềm. Có 411 ID trùng: các trường nghiệp vụ đang xuất giống nhau, ngoại trừ Bến Thủy thiếu TEU; ngày sửa cũng khác. Quy tắc mặc định vẫn chặn 411 trường hợp này.

Đã viết cơ chế tùy chọn `matching_native_teu_v1` cho riêng `oprt.contSizeType`:

- Chỉ bổ sung trường `teu` bị null bằng giá trị thực có ở nguồn còn lại khi mọi trường nghiệp vụ khác trùng khớp, kể cả ID, mã, tên và cờ trạng thái.
- Nếu cả hai có TEU và khác nhau, tiếp tục chặn. Không suy từ mã ISO, tên, hệ số tính cước hay công thức tấn.
- Nếu ngày khác, cần thêm quy tắc `source_date_variants_v1`; giữ cả hai cặp ngày nguồn để lọc kỳ không mất bản ghi.
- Không sửa các đối tượng đầu vào hoặc dữ liệu SQL. Quy tắc mặc định tắt và không áp dụng cho danh mục khác.

Tính thử trên dữ liệu thật cho kết quả **740 ID duy nhất, bổ sung TEU cho 411 ID**, tất cả bằng đúng giá trị có trong nguồn Cửa Lò. Đây là kết quả kỹ thuật của quy tắc đề xuất, chưa chứng minh hai ID ở hai hệ thống là cùng đối tượng ngoài phạm vi các trường đã so sánh. **Chưa bật cấu hình thật hoặc công bố kết quả này lên API local/production; đang chờ xác nhận áp dụng trên local.**

Cấu hình minh họa chỉ dùng trong bài thử, không phải cấu hình đang phục vụ:

```json
{
  "approved": true,
  "operation_attribute_completion": {
    "oprt.contSizeType": "matching_native_teu_v1"
  },
  "operation_metadata_merge": {
    "oprt.contSizeType": "source_date_variants_v1"
  }
}
```

## 2. Những nguồn bổ sung đã kiểm tra

Đã đọc trực tiếp `ContainerType` (11 dòng mỗi nguồn) và `vwContainerSizeTypeDomestic` (814/35 dòng), đối chiếu với `ContainerTypeGroup` và `ContainerSizeType`.

| Kết quả | Cửa Lò | Bến Thủy |
|---|---:|---:|
| ID kích cỡ có chiều dài duy nhất từ view nội địa | 725/740 | 12/411 |
| ID kích cỡ chưa có chiều dài từ view đó | 15 | 399 |
| Dòng kích cỡ tham chiếu nhóm không tồn tại | 7 | 0 |
| Dòng view nội địa tham chiếu kích cỡ không tồn tại | 0 | 0 |

Cả hai nguồn có nhóm ID 4, mã `22P1`, mô tả `20 Flat Rack` nhưng tham chiếu loại ID 2, mã `RF`, mô tả `REEFER CONTAINER`. Quan hệ này liên quan đến 1 dòng kích cỡ Cửa Lò và 18 dòng Bến Thủy. Cần đối chiếu danh mục nguồn; chưa tự sửa sang loại khác hoặc dùng tên/mã ISO để ghi đè.

View nội địa cho thêm bằng chứng chiều dài nhưng chưa đủ độ phủ và xác nhận ý nghĩa để thay trường kích thước vật lý. TEU tính cước cũng chưa được dùng thay TEU vật lý.

Đối với tổ đội và thiết bị, đã kiểm tra các liên kết `JobResource`, `Equipment`, `Crane`, `ReachVehicle`, `Vehicle` và `Organization` bằng thống kê tổng hợp:

- Có 22 đơn vị kiểu tổ/đội mỗi nguồn nhưng cả 22 thiếu ngày tạo/sửa. Chưa tìm thấy nguồn ngày thay thế đủ căn cứ.
- Có thể nối 40 thiết bị mỗi nguồn với `JobResource` bằng ID gốc và loại; tuy nhiên ngày và cờ xóa không nhất quán tại Cửa Lò. Không dùng ngày/cờ của tài nguyên để lấp cho thiết bị.
- Quan hệ thiết bị không đồng nghĩa đã đủ số sê-ri, ngày đăng kiểm, trạng thái thuê hay thuộc tính kỹ thuật bắt buộc.
- Không dùng bảng phân công `Gang` làm danh mục đội, không xuất tên hoặc hồ sơ nhân viên trong các khảo sát tổng hợp.

Các truy vấn điểm và thống kê quan hệ thành công; lần quét metadata rộng có timeout nên không kết luận đã tìm hết mọi bảng/view trong database.

## 3. Sửa kiểm tra độ mới của danh mục liên quan

Trước đây, một bản dữ liệu còn mới có thể được trả về dù danh mục mà nó tham chiếu đã hết hạn cập nhật. Đã bổ sung kiểm tra khi công bố và đọc API vận hành:

- Kiểm tra các danh mục có khóa tham chiếu thực sự được sử dụng, trong cùng giao dịch đọc SQLite.
- Thiếu danh mục, quá hạn hoặc thời điểm đọc không hợp lệ đều trả lỗi có kiểm soát; không lộ ID nguồn hay thông tin nội bộ trong lỗi công khai.
- Phạm vi kiểm tra là toàn bộ phiên dữ liệu, không chỉ các dòng của trang đang xem.
- Lưu sẵn danh sách tài nguyên được tham chiếu khi công bố; cập nhật các phiên cũ một lần. GET không quét và giải mã lại toàn bộ payload hay truy vấn SQL Server.
- Dùng ngưỡng độ mới hiện có. Nếu cấu hình tắt kiểm tra độ mới (`None`/`0`), kiểm tra liên quan cũng tắt theo; không tự thay chính sách vận hành.

Đã bổ sung xử lý phiên phân trang cũ: khi phiên đó không còn là phiên hiện tại và danh mục nó tham chiếu đã được công bố lại sau đó, API trả HTTP 409 (`SNAPSHOT_REFERENCES_CHANGED`), yêu cầu lấy lại từ trang 1. Đây là cách vô hiệu hóa thận trọng, kể cả khi nội dung lần công bố lại không đổi; tránh trả khóa của phiên cũ mà danh mục hiện tại đã bỏ. Kiểm tra này độc lập với ngưỡng độ mới, chỉ tra metadata; các tài nguyên cùng một lần công bố nguyên tử dùng chung thời điểm công bố.

JSON công khai vẫn chỉ có `data`, `code`, `message`. Thông tin chẩn đoán giữ trong báo cáo nội bộ.

## 4. Bằng chứng và kiểm tra

Các tệp dưới `outputs/oprt-complete-20260925/` là bằng chứng local, không đưa vào Git:

- `container-native-live.json`: SELECT mới của loại container và view nội địa.
- `container-lineage-review.json`: độ phủ và mâu thuẫn quan hệ danh mục.
- `source-candidates.json`: kết quả kiểm tra quan hệ tổ đội/thiết bị.
- `container-completion-proposal.json`: kết quả thử 740 ID, ghi rõ `PROPOSAL_ONLY_NOT_ENABLED`.
- `candidate-verification.json`: đã đọc đủ 740 dòng qua 8 trang và kiểm tra cả 8 ngày nguồn khác nhau trong kho SQLite tạm; TEU không đổi và không lộ trường nội bộ. Kho tạm đã được dọn sau khi thử, không thay kho API đang phục vụ.

Kiểm thử hồi quy bao gồm mặc định không gộp, giữ ID/giá trị nguồn, chặn khác biệt nghiệp vụ, bảo toàn ngày hai nguồn, lọc từng ngày sau công bố vào kho thử, migration và độ mới của danh mục liên quan. Sau thay đổi cuối cùng, `python -m pytest tests -q -k corporate` đạt **537 passed**, 603 bài ngoài phạm vi không chạy, 2 cảnh báo deprecation của thư viện; thời gian 192,57 giây.

Đã khởi động lại server local tại `http://127.0.0.1:8765/` và gọi đủ **20 đường dẫn**: `serviceType` trả 200 với 10 bản ghi; 19 đường dẫn chưa công bố vẫn trả 503. Có 9 mục xem được bản đối chiếu riêng, không đồng nghĩa đủ điều kiện gửi toàn công ty. Kiểm tra HTTP xác nhận body đúng 3 trường và không lộ trường nội bộ. Kết quả ở `outputs/oprt-deep-20260925/http-results.json`; không có thay đổi giao diện cần kiểm tra hình ảnh trong lượt này.

## 5. Bước xử lý tiếp theo

1. Khi được xác nhận, áp dụng quy tắc container trên local để đối chiếu 740 dòng trước khi tính đến production.
2. Xác minh quan hệ nhóm container `22P1`/`RF` và 7 khóa nhóm thiếu trong dữ liệu nguồn.
3. Chốt cách xử lý danh mục không có ngày nguồn với bên nhận, thay vì tự dùng ngày chạy API.
4. Giải quyết các ID thực sự khác đối tượng và cờ xóa khác nhau trước khi mở tiếp nhóm hàng, đơn vị tính, phương án và các danh mục phụ thuộc.
