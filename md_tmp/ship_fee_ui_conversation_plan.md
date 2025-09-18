## Kế hoạch cập nhật UI theo conversation_id và dùng JSON chỉnh sửa tay

Mục tiêu: Cho phép nhập `conversation_id`, lấy đơn hàng gần nhất theo conversation, hiển thị JSON vào một ô textarea để người dùng có thể chỉnh sửa, và dùng chính JSON đó làm nguồn dữ liệu cho tính năng trả lời phí ship (thay vì đọc từ file `examples/orders_priority.json`).

Nguồn API lấy đơn theo conversation:
- Tài liệu: [Get Near By Conversation](http://160.250.216.28:13886/api/v1/docs#/poscake/get_near_by_conversation_api_v1_poscake_orders_near_by_conversation_get)
- Endpoint: `GET {POSCAKE_BASE}/api/v1/poscake/orders/near-by-conversation?conversation_id=<id>`

---

### Thay đổi Frontend (trong `ship_fee/web/index.html`)
1) Khu vực nhập ID và thao tác:
   - Input text: `conversation_id` (prefill từ `localStorage` nếu có; mặc định dùng giá trị cố định hiện tại).
   - Button: "Lấy đơn theo Conversation ID" → gọi backend (proxy) để lấy JSON orders.
   - Button: "Reset đếm" (đang có) giữ nguyên.

2) Vùng hiển thị/chỉnh sửa JSON orders:
   - Thêm `textarea#orders_json` (chiều cao ~220–300px, monospace).
   - Khi bấm "Lấy đơn...": điền JSON trả về (format đẹp `JSON.stringify(obj, null, 2)`).
   - Người dùng có thể sửa trực tiếp JSON này trước khi hỏi.

3) Luồng gửi câu hỏi (nút "Gửi"):
   - Client đọc `textarea#orders_json`. Nếu parse được JSON hợp lệ → gửi vào API `/api/v1/ship-fee/answer` qua trường mới `orders_json` (object). Nếu parse lỗi → cảnh báo trên UI.
   - Vẫn gửi `conversation_id` (để đếm lần hỏi theo TTL 15 phút).

4) Trải nghiệm người dùng:
   - Khi fetch orders thành công → hiển thị badge "Đã tải đơn từ conversation_id ...".
   - Khi parse lỗi → hiển thị lỗi phía dưới textarea (không gửi request).
   - Lưu `conversation_id` vào `localStorage` mỗi khi người dùng thay đổi.

---

### Thay đổi Backend
1) Bổ sung biến môi trường và cấu hình:
   - `POSCAKE_BASE=http://160.250.216.28:13886` (có thể override).

2) Endpoint proxy (tránh CORS từ trình duyệt):
   - `GET /api/v1/orders/by-conversation?conversation_id=...`
   - Server-side gọi `${POSCAKE_BASE}/api/v1/poscake/orders/near-by-conversation?conversation_id=...` → trả body JSON nguyên vẹn.
   - Timeout hợp lý (10–15s), bắt lỗi rõ ràng.

3) Mở rộng API trả lời phí ship:
   - `POST /api/v1/ship-fee/answer` nhận thêm field mới `orders_json` (object). Ưu tiên sử dụng `orders_json` nếu được cung cấp; nếu không thì rơi về `orders_json_path` như trước.

   Ví dụ body:
   ```json
   {
     "user_text": "miễn ship giúp nha",
     "conversation_id": "792129147307154_24089184430742730",
     "orders_json": { "success": true, "orders": [ { "order_info": {"status": 0, "shipping_fee": 35000}, "items": [{"name": "..."}] } ] }
   }
   ```

4) Service: sửa `ShipFeeService.answer(...)` để nhận thêm tham số `orders_data: dict|None`, khi có thì dùng trực tiếp thay cho load file.

---

### Data flow mới
1) User nhập `conversation_id` → bấm "Lấy đơn theo Conversation ID".
2) FE gọi `GET /api/v1/orders/by-conversation?conversation_id=...` → BE proxy gọi POSCAKE API → trả JSON về FE.
3) FE điền JSON vào `textarea#orders_json` cho phép chỉnh.
4) Khi người dùng bấm "Gửi", FE gửi `user_text`, `conversation_id`, và `orders_json` (parse từ textarea) vào `/api/v1/ship-fee/answer`.
5) BE dùng `orders_json` để xác định đơn active mới nhất, `shipping_fee`, và quyết định template/phản hồi như hiện tại.

---

### UX & kiểm lỗi
- Nếu POSCAKE API trả về rỗng/không hợp lệ → hiển thị thông báo và giữ nguyên nội dung textarea.
- Validate JSON trước khi gửi: highlight lỗi parse phía dưới.
- Cho phép nút "Dùng JSON mẫu" để paste nhanh example valid (tùy chọn).

---

### Công việc cụ thể
Frontend:
1) Thêm input `#conv_id`, nút `#btnFetchOrders`, textarea `#orders_json`.
2) Viết hàm `fetchOrdersByConvId()` gọi `/api/v1/orders/by-conversation` và set textarea.
3) Sửa `sendMsg()` để đọc `orders_json` từ textarea, parse và gửi kèm.

Backend:
4) `config.py`: thêm `get_poscake_base()` đọc `POSCAKE_BASE` (default như trên).
5) `api.py`:
   - Thêm model `AskRequest` có `orders_json: dict | None`.
   - Thêm route `GET /api/v1/orders/by-conversation` (proxy).
6) `service.py`: `answer(..., orders_data: dict | None = None)` → ưu tiên `orders_data`.

---

### Tiêu chí chấp nhận
- Nhập conversation id và bấm "Lấy đơn..." sẽ điền JSON orders hợp lệ vào textarea.
- Chỉnh sửa JSON trong textarea và bấm "Gửi" → API dùng đúng JSON đã sửa để ra câu trả lời.
- Không còn phụ thuộc vào file `examples/orders_priority.json` khi textarea có nội dung hợp lệ.
- Reset đếm vẫn hoạt động bình thường.

---

### Ghi chú
- Nếu sau này POSCAKE yêu cầu auth header/token, thêm biến `POSCAKE_TOKEN` vào `.env` và proxy đính kèm header ở backend.


