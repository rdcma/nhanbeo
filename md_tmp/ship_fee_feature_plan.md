## Kế hoạch tính năng Q&A phí vận chuyển (Ship fee) — độc lập, dễ tích hợp

### Mục tiêu
- **Xây dựng REST API** bằng Python (FastAPI) để trả lời câu hỏi về phí vận chuyển theo các kịch bản đã mô tả.
- **Web chat đơn giản** để test nhanh (static HTML + fetch API).
- **Đếm số lần hỏi trong 15 phút** theo `conversation_id` bằng Redis.
- **Đọc dữ liệu đơn hàng** từ JSON: `examples/orders_priority.json` (mặc định), chọn đơn mới nhất và suy luận `shipping_fee`.
- **LLM** chỉ dùng cho nhận diện intent (người dùng có đang hỏi về ship fee hay không) và nhận diện yêu cầu "miễn ship/hủy nếu không miễn". Model/Key lấy từ `.env` (giống các file kế hoạch khác trong repo).

---

### Dữ liệu đầu vào và quy tắc nghiệp vụ
- File JSON: `examples/orders_priority.json`
  - Cấu trúc chính: `{ success: bool, orders: [ { order_info: { status, shipping_fee, ... }, items: [...] } ] }`
  - Đơn mới nhất: phần tử đầu tiên của mảng `orders[0]` (càng gần `success` càng mới).
  - "Không có đơn hàng": khi `orders` rỗng, hoặc không có đơn nào có `status ∈ {0,1}`, hoặc `items` rỗng.
  - Trạng thái đơn:
    - `0`: mới, `1`: đã xác nhận, `2`: đã gửi hàng, `3`: đã nhận, `8`: đang đóng hàng, `9`: chờ chuyển hàng, `6`: đã hủy.
  - Xác định phí vận chuyển: dùng `shipping_fee` trong `order_info` của đơn hợp lệ mới nhất.
  - Freeship khi `shipping_fee == 0`.

- Đếm số lần hỏi về ship fee (trong 15 phút) theo `conversation_id`:
  - Redis key: `shipfee:{conversation_id}`.
  - Mặc định `conversation_id` cố định: `792129147307154_24089184430742730` (có thể override qua request).
  - Mỗi lần phát hiện intent hỏi ship fee → `INCR` và set `EXPIRE 900s` nếu là lần đầu.

- Phân loại kịch bản trả lời (ưu tiên từ trên xuống):
  1) Không có đơn hàng (như định nghĩa trên).
  2) Có đơn hàng freeship (`shipping_fee == 0`).
  3) Có đơn hàng có ship; hỏi lần đầu (đếm lần hỏi = 1 trong TTL).
  4) Khách muốn được miễn ship — coi là lần 2 (đếm lần hỏi = 2) hoặc có từ khóa đề nghị miễn phí rõ ràng.
  5) Khách yêu cầu miễn ship nhiều lần/"không miễn thì hủy" — coi là lần thứ 3 trở lên (đếm lần hỏi ≥ 3) hoặc có tín hiệu đe dọa hủy (bất kể đếm).

- Nhận diện intent/keywords (LLM + regex fallback):
  - Intent: "hỏi ship fee" nếu text chứa từ khóa: `ship`, `shipping`, `phí ship`, `vận chuyển`, `free ship`, `miễn ship`…
  - Yêu cầu miễn ship: từ khóa như "miễn ship", "free ship", "free vận chuyển", "giảm ship".
  - Đe dọa hủy: `hủy`, `cancel`, `không miễn thì hủy`, `không free thì hủy`…
  - LLM (Gemini, model lấy từ `.env`) dùng để chuẩn hóa/khẳng định intent trong case mơ hồ; nếu lỗi LLM thì fallback regex.

---

### Template trả lời (tiếng Việt)
Theo ảnh yêu cầu (được chuẩn hóa nhẹ, có thể tinh chỉnh theo tone thương hiệu):

1) Khách không có đơn hàng:
> "Để em kiểm tra chương trình của cửa hàng xem có freeship cho mình không nha chị/anh."

2) Khách có đơn hàng Freeship (`shipping_fee == 0`):
> "Dạ đơn hàng của mình đang được hưởng ưu đãi freeship đó ạ. Em gửi hàng ngay nha chị/anh."

3) Khách có đơn hàng có ship, hỏi lần đầu (đếm = 1):
> "Dạ chị/anh ơi, hiện bên em chưa chạy chương trình miễn ship. Nhưng đang có chương trình giảm giá sâu cực kỳ ưu đãi chỉ trong hôm nay, mình đặt ngay kẻo lỡ nhé ạ!"

4) Khách muốn được miễn ship (có từ khóa đề nghị miễn):
> "Dạ mong chị/anh thông cảm giúp em nha. Phí ship của các đơn hàng khá cao, bên em đã hỗ trợ một phần ship và giá sản phẩm tốt nhất có thể rồi ạ. Nhờ mình hỗ trợ phần ship này giúp em nhé, em cảm ơn nhiều!"

5) Khách yêu cầu miễn ship nhiều lần / "Không miễn ship thì hủy đơn giúp":
> "Dạ phí ship toàn quốc nhà em là khoảng 35k ạ. Vì đây là đơn đầu/khách thân, bên em xin hỗ trợ miễn phí ship đơn này cho chị/anh nhé. Nếu tiện mình cân thêm sản phẩm nào thì ủng hộ em với nha."

- Thao tác hệ thống cho (5): `apply_free_shipping = true` (bật miễn phí vận chuyển cho đơn hiện tại) và trả thêm trường `action = "freeship"` trong JSON. Các case còn lại không tự động đổi phí (chỉ trả lời).

Lưu ý: Trong câu trả lời có thể chèn biến động (`{{shipping_fee}}`) nếu cần minh bạch số tiền.

---

### Thiết kế API (FastAPI)
- Base URL: `/api/v1`

- `POST /api/v1/ship-fee/answer`
  - Body (JSON):
    - `user_text: string` — câu người dùng.
    - `conversation_id: string` — mặc định dùng giá trị cố định; có thể đổi.
    - `orders_json_path: string|null` — đường dẫn file JSON; mặc định dùng env `ORDERS_JSON` hoặc `examples/orders_priority.json`.
  - Trả về (JSON):
    - `case: "no_order" | "freeship" | "has_ship_first_time" | "ask_free_ship" | "ask_free_ship_many_times"`
    - `reply_text: string`
    - `action: "freeship" | null` — khi escalate (case 5) set `"freeship"`, ngược lại `null`.
    - `actions: { apply_free_shipping: boolean }` — song song `action`, để hệ thống khác đọc dễ dàng.
    - `diagnostic: { asked_count: number, shipping_fee: number|null, order_id: number|null, status: number|null, picked_reason: string }`

- `POST /api/v1/ship-fee/reset`
  - Body: `{ conversation_id: string }`
  - Mục đích: reset đếm lần hỏi (xóa key Redis).

- `GET /healthz` → `{"ok": true}`

---

### Kiến trúc mã nguồn (độc lập, dễ tái sử dụng)
Thư mục mới: `ship_fee/`

- `config.py`
  - Đọc `.env`: `GOOGLE_API_KEY`, `GOOGLE_MODEL` (vd: `gemini-1.5-flash`), `REDIS_URL` (vd: `redis://localhost:6379/0`), `ORDERS_JSON`, `CONVERSATION_ID_DEFAULT`.

- `orders.py`
  - `load_orders(path) -> dict`
  - `pick_latest_active_order(raw) -> dict|None` (lọc `status ∈ {0,1}`, chọn phần tử đầu tiên trong `orders`).
  - `extract_shipping_fee(order) -> int` (hoặc `None`).

- `counter.py`
  - Redis client (kết nối từ `REDIS_URL`).
  - `increase_and_get(conversation_id, ttl_seconds=900) -> int` (INCR + set EXPIRE lần đầu).
  - `reset(conversation_id)`.

- `intent.py`
  - Regex nhanh để phát hiện intent và các từ khóa đặc biệt.
  - Adapter LLM (Gemini) cho case mơ hồ: `classify_intent(user_text) -> { intent: "ship_fee"|"other", wants_free: bool, cancel_threat: bool }`.
  - Tận dụng util gọi LLM tương tự trong `product_qa.pipeline` (hàm `load_api_key`, `call_llm_json`).

- `templates.py`
  - Hằng số câu trả lời theo 5 case ở trên, có thể format bằng `.format()` với biến `shipping_fee`.

- `service.py`
  - `answer_ship_fee(user_text, conversation_id, orders_path) -> ResponseDTO`:
    1) Phát hiện intent (regex → nếu mơ hồ, dùng LLM; nếu không phải ship fee → trả về `case: "other"`, `reply_text: "Câu này không phải về ship fee."`).
    2) Tăng đếm Redis.
    3) Đọc đơn, chọn đơn mới nhất hợp lệ, rút `shipping_fee`.
    4) Chọn case theo thứ tự ưu tiên và render `reply_text` + `actions`.

- `api.py`
  - Tạo FastAPI app và route các endpoint.
  - Mount static cho web chat (`/` phục vụ `web/index.html`).

- `web/`
  - `index.html` (một file) chứa: ô nhập, lịch sử chat, gọi `fetch('/api/v1/ship-fee/answer')`.

---

### Giao diện web chat (MVP)
- Single-file `web/index.html` với CSS inline đơn giản.
- Form gửi `user_text` và dùng `localStorage` để lưu `conversation_id` (mặc định từ server nếu thiếu).
- Hiển thị `reply_text` + badge trường hợp (case) + `asked_count`.

---

### Biến môi trường & phụ thuộc
- `.env` (ví dụ):
  - `GOOGLE_API_KEY=...`
  - `GOOGLE_MODEL=gemini-1.5-flash` (hoặc model bạn đang dùng)
  - `REDIS_URL=redis://localhost:6379/0`
  - `ORDERS_JSON=examples/orders_priority.json`
  - `CONVERSATION_ID_DEFAULT=792129147307154_24089184430742730`

- `requirements.txt` cần bổ sung:
  - `fastapi`
  - `uvicorn`
  - `redis`
  - `python-dotenv`
  - `google-generativeai` (đã dùng ở repo)

#### Sử dụng môi trường Python có sẵn `.venv`
- Kích hoạt (macOS zsh):
  ```bash
  source .venv/bin/activate
  ```
- Cài dependency:
  ```bash
  pip install -r requirements.txt
  ```
- Chạy dịch vụ dev:
  ```bash
  uvicorn ship_fee.api:app --reload --port 8000
  ```
- Tắt môi trường:
  ```bash
  deactivate
  ```

---

### Mô tả chi tiết logic chọn case
Pseudocode tóm tắt:

```text
asked = counter.increase_and_get(conversation_id)
order = pick_latest_active_order(orders)
if not order: return case=no_order, reply=TEMPLATE_1
fee = extract_shipping_fee(order)
if fee == 0: return case=freeship, reply=TEMPLATE_2

signals = detect(user_text)  # wants_free, cancel_threat
if asked == 1 and not signals.wants_free:
    return case=has_ship_first_time, reply=TEMPLATE_3
if signals.cancel_threat or asked >= 3:
    return case=ask_free_ship_many_times, reply=TEMPLATE_5, action="freeship", actions.apply_free_shipping=true
if asked == 2 or signals.wants_free:
    return case=ask_free_ship, reply=TEMPLATE_4
return case=has_ship_first_time, reply=TEMPLATE_3
```

---

### Test cases (thủ công và cURL)
- Không có đơn hàng hợp lệ → `no_order`.
- Đơn mới nhất hợp lệ có `shipping_fee = 0` → `freeship`.
- Đơn có `shipping_fee > 0`, lần hỏi đầu → `has_ship_first_time`.
- Người dùng đề nghị miễn ship (keyword/intent) hoặc lần hỏi thứ 2 → `ask_free_ship`.
- Người dùng nhắc lại lần 3 trở lên hoặc đe dọa hủy → `ask_free_ship_many_times` (và `action = "freeship"`, `apply_free_shipping = true`).

Ví dụ cURL:

```bash
curl -X POST http://localhost:8000/api/v1/ship-fee/answer \
  -H 'Content-Type: application/json' \
  -d '{
    "user_text": "cho mình xin freeship được không?",
    "conversation_id": "792129147307154_24089184430742730"
  }'
```

---

### Kế hoạch triển khai
1) Tạo module `ship_fee/` với các file nêu trên, thêm dependency vào `requirements.txt`.
2) Viết `orders.py`, `templates.py`, `counter.py` (Redis), `intent.py` (regex + adapter Gemini), `service.py`.
3) Tạo `api.py` (FastAPI) và mount static `web/index.html`.
4) Tạo `run_ship_fee.py` để chạy dev: `uvicorn ship_fee.api:app --reload`.
5) Viết README ngắn cách chạy, biến môi trường, và dàn cURL.
6) Kiểm thử 5 case với dữ liệu mẫu và log `diagnostic` để dễ debug.

---

### Rủi ro & phương án
- Người dùng nói mơ hồ → fallback regex + hỏi lại, hoặc trả lời lịch sự mời mô tả rõ hơn.
- Không có Redis → cho phép sử dụng in-memory counter (chỉ dành cho dev; ghi rõ không dùng production).
- Dữ liệu `orders_priority.json` thay đổi schema → validate JSON và log cảnh báo.

---

### Ghi chú tích hợp
- Tính năng độc lập; có thể mount dưới `run_demo.py` hiện có hoặc chạy riêng bằng `uvicorn`.
- Trả `actions.apply_free_shipping` cho hệ thống bên ngoài quyết định thực thi (không tự ý sửa đơn trong MVP).


