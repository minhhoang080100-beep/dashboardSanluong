# API Tổng công ty trên service Railway riêng

Rà soát tài liệu ngày **21/10/2026**. Service cung cấp địa chỉ riêng cho hệ thống
nhận dữ liệu của Tổng công ty. Dashboard tiếp tục dùng backend của dashboard;
giao diện, tài khoản dashboard và dữ liệu kế hoạch không chuyển sang service API.

| Thành phần | Giá trị |
|---|---|
| API riêng | `https://apibigdata.up.railway.app` |
| Backend dashboard | `https://dashboardsanluong-production-1761.up.railway.app` |
| Service | `api-tong-cong-ty`; dùng đúng project/environment/service ID được cấp nội bộ |
| Volume riêng | Mount `/data`, lưu tài khoản API và profile nguồn |
| Ứng dụng | `corporate_api.app:app` |
| Cổng / healthcheck | `8000` / `/health` |
| Quy mô | Một instance, một worker với SQLite trên volume riêng |

Service riêng không thay công thức, phạm vi hoặc điều kiện chất lượng dữ liệu.
HTTP thành công của một kỳ không chứng minh toàn năm đầy đủ, khớp MISBI hoặc
được Lakehouse chấp thuận. Nhật ký triển khai, hash và số liệu kiểm chứng chi
tiết được giữ riêng ngoài Git. Khi phát hành mới phải kiểm tra đúng bản đang chạy.

## Đường dẫn và cách đăng nhập

Các path và định dạng JSON giữ nguyên. Thay origin cũ bằng domain API riêng, ví dụ:

```text
POST https://apibigdata.up.railway.app/api/login
GET  https://apibigdata.up.railway.app/api/contQuayVolumesCB?companyId=CNT&startDate=20260901&endDate=20260930&page=1&limit=100
POST https://apibigdata.up.railway.app/api/logout
```

`/api/login` nhận `Username` và `Password` như API hiện tại. Sau khi đăng nhập, gửi `Authorization: Bearer <accessToken>` trong header của yêu cầu lấy dữ liệu. Mở URL bằng thanh địa chỉ trình duyệt không tự gửi header này. Không đưa mật khẩu hoặc token vào URL.

Ứng dụng riêng cung cấp corporate router, `/health`, tài liệu Swagger `/docs` và mô tả OpenAPI `/openapi.json`; không có route báo cáo dashboard, kế hoạch, quản trị người dùng hoặc API inspector. Các phản hồi API giữ envelope và các header phân trang/kiểm tra dữ liệu hiện có. Hướng dẫn contract, trường dữ liệu và kiểm tra toàn kỳ nằm trong [hướng dẫn vận hành API](VAN_HANH_API_TONG_CONG_TY.vi.md).

## Xem và thử API bằng Swagger

Mở **https://apibigdata.up.railway.app/docs**. Trang này công khai cấu trúc API; yêu cầu lấy dữ liệu vẫn cần tài khoản API và Bearer token.

1. Mở `POST /api/login`, chọn **Try it out**, nhập `Username` và `Password` của tài khoản API rồi chọn **Execute**.
2. Sao chép giá trị `accessToken` trong phản hồi thành công.
3. Chọn **Authorize**, dán **chỉ giá trị token** (không thêm chữ `Bearer`), rồi chọn **Authorize** và **Close**.
4. Mở API cần thử, chọn **Try it out**, điền tham số và chọn **Execute**. Ngày truy vấn dùng dạng `YYYYMMDD`, ví dụ `20260916`; `companyId` là `CNT`.
5. Khi xong, gọi `POST /api/logout` để thu hồi token trên máy chủ, rồi xóa token trong hộp **Authorize**. Nút Logout trong hộp Swagger chỉ xóa token khỏi giao diện.

**Example Value** trong Swagger là mẫu sinh từ schema, không phải dữ liệu nguồn.
Xem kết quả thật ở **Server response → Response body** sau khi chọn Execute.
`page` là số thứ tự trang, bắt đầu từ `1`; `limit` là số dòng mỗi trang, tối đa
`100`. Mỗi yêu cầu sản lượng tối đa 31 ngày, tính cả ngày đầu/cuối; chia kỳ dài
thành các tháng không chồng ngày.

Swagger không giữ token sau khi tải lại trang. Dữ liệu nguồn thiếu thông tin bắt buộc vẫn trả lỗi như khi gọi API từ hệ thống Tổng công ty; trang tài liệu không bỏ qua kiểm tra chất lượng dữ liệu.

## Tài khoản, profile và dữ liệu nguồn

Service có kho xác thực **`/data/corporate.sqlite3` riêng**. Tạo hoặc chuyển
tài khoản theo quy trình quản trị đã xác nhận; không chép token, phiên dashboard,
kế hoạch, cache hoặc toàn bộ kho dashboard sang service API. Không đưa tên tài
khoản thật, hash mật khẩu hoặc thông tin xác thực vào tài liệu công khai.

Nếu tài khoản đã được chuyển có kiểm soát, có thể dùng thông tin được cấp trên service mới. Hai kho độc lập: token cũ không có hiệu lực trên service mới; đăng xuất, đổi mật khẩu hoặc khóa tài khoản ở một service không tự thực hiện trên service còn lại. Quản trị tài khoản của API riêng phải nhắm đúng service và `/data/corporate.sqlite3`. Không chạy lại bootstrap hoặc thay file SQLite trong lần triển khai mã thông thường.

Các thiết lập cho service riêng:

| Biến / file | Vai trò |
|---|---|
| `CORPORATE_API_ENABLED=true` | Bật API máy |
| `CORPORATE_READ_MODE=live` | Mỗi yêu cầu đọc mới SmartTOS |
| `CORPORATE_SOURCE_PROFILE=/data/corporate-source.json` | Profile nguồn đã được kiểm tra; nằm trên volume, không trong image |
| `CORPORATE_STATE_PATH=/data/corporate.sqlite3` | Kho tài khoản/token riêng |
| `DASHBOARD_STATE_PATH=/data/control.sqlite3` | Đường dẫn chuẩn để entrypoint kiểm tra mount; app riêng không khởi tạo kho dashboard |
| `CORPORATE_SYNC_ENABLED=false` | Không chạy quy trình đồng bộ nền/published export |
| `PORT=8000`, `RAILWAY_RUN_UID=0` | Khớp start command; entrypoint chuẩn bị quyền volume rồi hạ quyền chạy ứng dụng |
| `DB_QUERY_TIMEOUT_SECONDS=20` | Hạn chờ truy vấn mặc định của service riêng |

Các biến kết nối `DB_*` và cấu hình origin frontend dùng reference tới biến của service dashboard, trừ timeout truy vấn nêu trên. Reference giúp dùng cùng cấu hình nguồn, nhưng service vẫn có tiến trình và volume riêng. Khi thay biến được tham chiếu, cần kiểm tra lại cả service liên quan; không sao chép giá trị bí mật vào tài liệu/Git.

Profile `/data/corporate-source.json` được chuyển riêng từ cấu hình đã dùng trên backend dashboard. Phạm vi sản lượng và quy tắc nguồn giữ nguyên, gồm loại Cầu 5 theo cầu cập đầu tiên cho sản lượng qua cầu. Không dùng việc đổi domain để sửa số liệu hoặc bỏ các lỗi thiếu dữ liệu nguồn.

### Chuyển chế độ ID của bộ S

Bản mã cập nhật ngày 22/10/2026 hỗ trợ
`source_identity_mode="terminal_prefixed_v1"` trong profile; **chưa xác nhận
đã kích hoạt trên service production**. Mặc định `native` giữ ID cũ.
Chế độ mới trả `CL:<ID>` cho Cửa Lò và `BT:<ID>` cho Bến Thủy, ví dụ giả lập
`CL:123` và `BT:123`, đồng bộ trong tám danh mục S, quan hệ của chúng và khóa
tham chiếu của bốn API sản lượng. Bộ `/api/oprt/` không đổi.

Triển khai mã hỗ trợ tới đúng service trước khi đặt chế độ trong profile trên
volume. Dùng `cargo_catalog_source="native_groups"` và kích cỡ `native_cargo`
hoặc `native_domestic` có quan hệ đã xác minh. ID database và các ánh xạ
profile theo xí nghiệp vẫn là ID gốc; không thêm tiền tố vào allowlist. Việc
đổi ID không thay `companyId=CNT`, tấn/TEU, phạm vi hoặc chính sách null và
không thêm `sourceDatabase` vào JSON.

Bên nhận cần giữ nguyên chuỗi có tiền tố trong khóa, tra cứu và bộ lọc; không
ép thành số hoặc bỏ tiền tố. Khi bật chế độ mới, ID trần/sai cú pháp bị từ chối
trước truy vấn. Nạp lại danh mục và sản lượng cùng chế độ, thay thế đúng phân
vùng sau kiểm tra; không nối vào dữ liệu ID cũ hoặc ghép trang trước/sau đổi
profile. Xem [quy trình chuyển định danh](VAN_HANH_API_TONG_CONG_TY.vi.md#chuyển-id-bộ-s-sang-phân-biệt-nguồn).

## Triển khai bản tiếp theo

Service hiện được triển khai bằng **Railway CLI từ gói mã local**, chưa nối GitHub để tự triển khai. Đẩy commit lên Git không tự cập nhật service này. Các thiết lập sau được lưu trực tiếp trên service API riêng trong Railway:

| Thiết lập service | Giá trị |
|---|---|
| Build | Dockerfile ở gốc gói đã chuẩn bị |
| Start Command | `python runtime_entrypoint.py python -m uvicorn corporate_api.app:app --host 0.0.0.0 --port 8000 --no-access-log` |
| Healthcheck Path / Timeout | `/health` / `120` giây |
| Restart Policy / Max Retries | `ON_FAILURE` / `3` |
| Replicas | `1` |

Trong lần cấu hình ngày 15/10/2026, Railway từ chối thiết lập `railwayConfigFile` vì `railway.json`/`railway.toml` đã bị ngừng hỗ trợ theo phản hồi của nền tảng, và hướng sang `.railway/railway.ts`. Vì vậy gói API hiện không dựa vào file TOML. Start command và healthcheck được đặt trực tiếp trên service; gói Docker cũng có CMD riêng cho API để không quay về dashboard app khi tạo lại deployment. Dự án chưa triển khai Infrastructure as Code mới của Railway.

Railway Start Command đã quan sát được là thay thế cả Docker ENTRYPOINT. Vì vậy phải giữ tiền tố **`python runtime_entrypoint.py`** trong thiết lập service, không chỉ chạy `uvicorn` trực tiếp. Với `RAILWAY_RUN_UID=0`, entrypoint kiểm tra mount `/data`, chuẩn bị quyền file rồi hạ tiến trình ứng dụng xuống UID/GID `10001`; bỏ tiền tố này có thể khiến ứng dụng chạy bằng root. Dockerfile trong gói vẫn giữ ENTRYPOINT gốc để dùng đúng khi chạy image không có Start Command override.

Thực hiện tại thư mục gốc dự án, sau khi đã kiểm tra thay đổi và đăng nhập Railway CLI:

```powershell
# Thư mục đầu ra phải mới, không dùng lại gói cũ.
.\.venv-audit\Scripts\python.exe scripts/prepare_corporate_api_deploy.py --output outputs/api-deploy-YYYYMMDD-HHMM

npx --yes @railway/cli up outputs/api-deploy-YYYYMMDD-HHMM --path-as-root --project "<PROJECT_ID>" --service "<API_SERVICE_ID>" --environment "<ENVIRONMENT_ID>" --detach --message 'Corporate API release' --json
```

Thay `YYYYMMDD-HHMM` bằng dấu thời gian mới ở cả hai lệnh và thay các ID minh họa bằng đúng cấu hình nội bộ của service API. Script đóng gói chỉ lấy các file mã được liệt kê trong Dockerfile, `corporate_api/*.py` và các file build cần thiết. Trong **bản sao Dockerfile của gói**, script thay đúng CMD chạy `corporate_api.service` bằng CMD chạy `uvicorn corporate_api.app:app` ở cổng `8000`; Dockerfile gốc của dashboard giữ nguyên. Script từ chối nếu CMD gốc đã thay đổi hoặc thư mục đầu ra đã tồn tại. Gói không có file cấu hình Railway TOML và không bao gồm `.env`, SQLite, profile nguồn, kết quả đối soát hoặc dữ liệu trong `API cũ/`.

Phải giữ đúng `--service`, `--project`, `--environment` và `--path-as-root` trong lệnh. Không chạy `railway up` với cả repository hoặc thư mục `backend` chưa chuẩn bị, vì có thể chọn sai start command hoặc tải file nội bộ không cần thiết. Không áp dụng Start Command/healthcheck riêng ở trên cho service backend dashboard.

`--detach` chỉ trả lại quyền điều khiển sau khi gửi gói; cần theo dõi deployment tới `SUCCESS` và kiểm tra HTTP trước khi coi triển khai hoàn tất. Volume `/data` được giữ qua các lần triển khai. Nâng cấp mã không cần nhập lại profile, tài khoản hay token.

Nếu vừa đổi Start Command hoặc cấu hình service, tạo deployment mới bằng lệnh `up` ở trên và kiểm tra cấu hình trong deployment mới. Trong lần triển khai này, thao tác `service redeploy` đã dùng lại Start Command của deployment cũ, dù thiết lập service đã đổi; không dùng riêng trạng thái `SUCCESS` để kết luận cấu hình mới đã được áp dụng.

Nếu về sau nối GitHub, cần chuẩn bị Dockerfile hoặc quy trình build riêng cho API và kiểm tra lại Start Command/healthcheck lưu trên service; `backend/Dockerfile` gốc vẫn mặc định chạy dashboard. Xác minh một deployment thử vẫn dùng `corporate_api.app:app` và các route dashboard trả `404`. Không coi GitHub autodeploy đã được bật chỉ vì service dashboard có sẵn liên kết repo.

## Kiểm tra sau triển khai

1. `GET /health` trả `200` và `{"status":"ok"}`: xác nhận tiến trình chạy, không đọc SQL và không chứng minh kỳ báo cáo đủ dữ liệu.
2. Đăng nhập API riêng, gọi một danh mục và một kỳ sản lượng có dữ liệu đã kiểm tra; xác nhận Bearer, quyền và envelope đúng. Kiểm tra toàn bộ trang, không chỉ trang đầu.
3. Kiểm tra `X-Result-Digest`, `X-Total-Count`, `X-Has-Next` và `X-Source-Read-At`. Nếu dữ liệu nguồn thay đổi giữa các trang, chạy lại kỳ theo hướng dẫn thay vì ghép hai lần đọc khác nhau.
4. Gọi `/api/dashboard` và `/api/admin/corporate-api/catalog` trên domain mới: phải trả `404`. Service mới không cung cấp các chức năng này.
5. Đăng xuất trên domain mới rồi thử token vừa thu hồi: phải trả `401`; token hoặc phiên dashboard hiện hữu không bị thay đổi.

Lỗi `401` là vấn đề xác thực; `403` là thiếu quyền; `503` cần xem `X-Error-Code` và thông báo để phân biệt cấu hình, nguồn không kết nối được, hay phiếu thiếu dữ liệu bắt buộc. Việc có service riêng không tự xử lý các phiếu GÀU/M3/M2/VỎ BỊCH chưa xác minh hoặc các mã còn thiếu. Không chuyển những trường hợp này thành tổng 0 hay trả tổng thiếu với trạng thái thành công.

Frontend và mục **Kiểm tra API** hiện vẫn dùng backend dashboard cũ. Không đổi `VITE_API_URL` sang API riêng vì biến này còn điều khiển các yêu cầu báo cáo/kế hoạch. Nếu cần inspector dùng host mới, phải bổ sung base URL riêng cho corporate client trong một thay đổi khác; CORS đã dùng danh sách origin được cấu hình, không mở wildcard.

Các kiểm tra nguồn và số liệu hiện có không thay thế xác nhận MISBI hay việc Tổng công ty chấp thuận các trường có giá trị `null`. Chỉ bàn giao URL cùng phạm vi/kỳ đã được xác minh thực tế.
