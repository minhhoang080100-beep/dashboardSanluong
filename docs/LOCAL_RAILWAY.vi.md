# Frontend local dùng API Railway

Chế độ này chạy giao diện trên máy, còn đăng nhập và dữ liệu báo cáo đi qua API Railway. Không cần chạy backend Python hoặc kết nối SQL từ máy local. Chỉ chuyển sang chế độ này sau khi backend Railway đã triển khai phiên bản tương thích với frontend.

Tạo `frontend/.env.railway.local` với nội dung dưới đây. Tệp này đã được Git bỏ qua và không chứa thông tin đăng nhập:

```dotenv
VITE_API_URL=/api
DEV_API_PROXY_TARGET=https://dashboardsanluong-production.up.railway.app
```

Trong cửa sổ đang chạy Vite, bấm `Ctrl+C`. Tại thư mục `frontend`, chạy:

```powershell
npm run dev -- --mode railway --host 127.0.0.1 --port 5173 --strictPort
```

Mở hoặc tải lại **http://127.0.0.1:5173**. Trình duyệt tiếp tục gọi `/api`; Vite chuyển tiếp nguyên đường dẫn tới Railway bằng HTTPS. Không thêm `/api` vào `DEV_API_PROXY_TARGET`, không cần sửa CORS production. Cờ `--strictPort` giúp báo lỗi nếu cổng còn bị chiếm, thay vì tự chuyển sang URL khác.

Đăng nhập bằng tài khoản Railway. Khi tải lại trang, ứng dụng kiểm tra phiên hiện có bằng `/api/auth/me`; nếu Railway trả `401`, ứng dụng xóa phiên trong tab và yêu cầu đăng nhập lại. Không sao chép tài khoản hoặc token local sang Railway. Các thao tác nhập/duyệt kế hoạch, chốt báo cáo và quản trị ở chế độ này làm việc trực tiếp với dữ liệu production.

Để quay lại backend local, dừng Vite rồi chạy:

```powershell
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Chế độ mặc định dùng `http://127.0.0.1:8000`, trừ khi đã cấu hình biến proxy khác trong môi trường hoặc tệp `.env.local`. `DEV_API_PROXY_TARGET` được ưu tiên; tên cũ `VITE_API_PROXY_TARGET` vẫn dùng được. Biến môi trường đã đặt trong cửa sổ PowerShell được Vite ưu tiên hơn giá trị trong tệp; cần bỏ giá trị ghi đè cũ nếu API vẫn đi sai nơi.

`npm run build` vẫn dùng chế độ production và không đọc `.env.railway.local`. Giữ nguyên `VITE_API_URL` trên Vercel. Chế độ Railway local chỉ dành cho lệnh phát triển ở trên.

Tham khảo: [proxy phát triển của Vite](https://vite.dev/config/server-options.html#server-proxy), [biến môi trường theo mode](https://vite.dev/guide/env-and-mode.html).
