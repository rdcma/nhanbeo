## Kế hoạch chuẩn hóa địa chỉ (VN) – sửa các case failed

### Mục tiêu
- Chuẩn hóa chuỗi địa chỉ tiếng Việt (có thể kèm số điện thoại) thành cấu trúc ổn định và xác thực theo dữ liệu hành chính VN.
- Xuất kết quả theo đúng JSON format như file ví dụ đính kèm: gồm `raw`, `top1`, `variants[]`, `top1_result`.
- Dùng Gemini (model từ `.env`) để trích xuất trường, sau đó xác thực/chuẩn hóa bằng API dữ liệu hành chính (hoặc dataset nội bộ).

### Biến môi trường cần dùng
- `GOOGLE_API_KEY` (bắt buộc): khóa gọi Gemini.
- `GOOGLE_MODEL` (tùy chọn, mặc định `gemini-2.5-flash`).
- Tùy chọn cho dữ liệu hành chính:
  - `ADMIN_API_BASE` (ví dụ: `https://<your-admin-api>`), hoặc
  - `ADMIN_DATA_PATH` (đường dẫn JSON/CSV dữ liệu tỉnh/huyện/xã offline).

### Định dạng JSON output (phải follow như ví dụ)
```json
{
  "raw": "<chuỗi gốc>",
  "top1": {
    "phone_number": "<string|null>",
    "address": "<dòng địa chỉ chi tiết: số nhà, ngõ, đường, thôn/xóm...>",
    "commune_name": "<xã/phường/thị trấn>",
    "district_name": "<quận/huyện/thành phố thuộc tỉnh/thị xã>",
    "province_name": "<tỉnh/thành phố trực thuộc trung ương>",
    "full_address": "<address + commune + district + province>"
  },
  "variants": [
    {
      "phone_number": "<string|null>",
      "address": "<...>",
      "commune_name": "<...>",
      "district_name": "<...>",
      "province_name": "<...>",
      "full_address": "<...>"
    }
  ],
  "top1_result": {
    "success": false,
    "province_id": "<string|null>",
    "district_id": "<string|null>",
    "commune_id": "<string|null>",
    "province_name": "<...>",
    "district_name": "<...>",
    "commune_name": "<...>",
    "found_items": ["Tỉnh: ... -> ID: ...", "Huyện: ... -> ID: ..."],
    "errors": ["<chuỗi mô tả lỗi nếu có>"]
  }
}
```

Lưu ý:
- Không bịa tên địa danh. Nếu không chắc, để `null` ở cấp không xác định và ghi lỗi vào `errors`.
- `full_address` = `address` + `commune_name` + `district_name` + `province_name` (cách nhau bởi khoảng trắng, bỏ `null`).

### Luồng xử lý đề xuất
1) Tiền xử lý `raw`
   - Chuẩn hóa khoảng trắng, lower-case cho matching (giữ dấu đầy đủ cho xuất kết quả).
   - Trích số điện thoại VN (10–11 số, có thể bắt đầu bằng `0`, `84`, bỏ ký tự không số): lưu vào `phone_number` nếu có.
   - Loại bỏ/chuẩn hóa tiền tố thường gặp: `xã`, `phường`, `thị trấn`, `huyện`, `quận`, `thành phố`, `thị xã`, viết tắt (`tp.`, `q.`, `h.`, `tx.`) để tăng recall.
   - Bóc tách phần địa chỉ đường/phố/thôn/xóm về `address` (không chứa tên xã/huyện/tỉnh nếu đã nhận ra).

2) Trích xuất trường bằng LLM (Gemini)
   - Gọi Gemini với System Prompt (bên dưới) để dự đoán 5 trường: `address`, `commune_name`, `district_name`, `province_name`, `phone_number`.
   - Trả về một bản ghi chuẩn hóa (ứng viên `top1`) và có thể đề xuất biến thể `variants` (khác biệt nhỏ về chính tả, thêm dấu, thêm/bớt tiền tố cấp hành chính) nếu tự tin.

3) Xác thực với dữ liệu hành chính VN (API/dataset)
   - Tìm `province` theo fuzzy (ưu tiên khớp có dấu; fallback bỏ dấu). Nếu có nhiều ứng viên, chọn tên chính tắc nhất trong API.
   - Bên trong `province_id`, tìm `district` theo fuzzy (xử lý biến thể: "thành phố", "thị xã", "quận", "huyện").
   - Bên trong `district_id`, tìm `commune` theo fuzzy (xử lý biến thể: "xã", "phường", "thị trấn").
   - Nếu không tìm thấy ở cấp nào, set `success=false`, điền `found_items` cho các cấp tìm được, và thêm mô tả lỗi rõ ràng vào `errors` đúng như ví dụ.

4) Sinh `variants`
   - Biến thể thêm/bớt tiền tố cấp hành chính: ví dụ `Phường Văn Khê` ↔ `Văn Khê`; `Thành phố Đông Triều` ↔ `Đông Triều`.
   - Chuẩn hóa dấu/chính tả: thêm dấu đúng tiếng Việt nếu thiếu/nhầm (`van khe` → `Văn Khê`).
   - Chuẩn hóa cấu trúc đường/phố/ngõ/thôn/xóm: gom số nhà, ngõ, tổ, thôn/xóm về `address` để `full_address` mạch lạc.
   - Chỉ xuất biến thể có khả năng tăng tỷ lệ tìm thấy.

5) Hoàn thiện `top1_result`
   - Ghi `success=true` nếu có đủ `province_id`, `district_id`, `commune_id`.
   - Nếu thiếu cấp nào: `success=false`, liệt kê `found_items` cho cấp đã xác thực được và `errors` cho cấp không thấy (bám sát thông điệp như ví dụ: "Không tìm thấy xã: <tên> trong huyện <tên huyện>").

### System Prompt cho Gemini (để trích xuất trường)
```text
Bạn là trợ lý chuẩn hóa địa chỉ tiếng Việt. Nhiệm vụ: từ chuỗi “raw” (có thể chứa số điện thoại), hãy TRẢ VỀ DUY NHẤT một JSON theo schema:
{
  "phone_number": "<string|null>",
  "address": "<số nhà/ngõ/đường/thôn/xóm...>",
  "commune_name": "<xã/phường/thị trấn>",
  "district_name": "<quận/huyện/thành phố/thị xã>",
  "province_name": "<tỉnh/thành phố trực thuộc TW>",
  "full_address": "<address + commune + district + province>"
}

YÊU CẦU:
- Không bịa địa danh. Nếu không chắc, để null trường tương ứng.
- Giữ đúng chính tả, thêm dấu tiếng Việt nếu có thể suy ra chắc chắn.
- Đưa các thành phần chi tiết (số nhà, ngõ, ngách, hẻm, khu, thôn/xóm) vào "address".
- Không thêm cấp hành chính nếu chuỗi không nói tới. Không trả lời bằng văn bản ngoài JSON.

Đầu vào (raw): "{RAW}"
```

Gợi ý gọi từ code (tận dụng `product_qa.pipeline.call_llm_json`):
```python
from product_qa.pipeline import load_api_key, call_llm_json, get_llm_model_name

def extract_address_fields(raw: str) -> dict:
    load_api_key()
    model_name = get_llm_model_name()
    sys_prompt = SYSTEM_PROMPT_TEMPLATE.replace("{RAW}", raw)
    return call_llm_json(sys_prompt, model_name)
```

### Xác thực với API dữ liệu hành chính
- Cần một trong hai:
  - API: `ADMIN_API_BASE` cung cấp các endpoint:
    - GET `/provinces` → danh sách `[{ id, name, synonyms[] }]`
    - GET `/provinces/{pid}/districts`
    - GET `/districts/{did}/communes`
  - Hoặc dataset offline (`ADMIN_DATA_PATH`) với cấu trúc tương đương, load vào RAM.

- Hoặc sử dụng trực tiếp PosCake Geo API để đánh giá (khuyến nghị khi có sẵn):
  - Tài liệu: [Get Location IDs by Names](http://160.250.216.28:13886/api/v1/docs#/poscake/get_location_ids_by_names_api_v1_poscake_geo_location_ids_get)
  - Endpoint: `GET {POSCAKE_BASE}/api/v1/poscake/geo/location-ids`
  - Query params: `province_name`, `district_name`, `commune_name` (tên có dấu, nếu không chắc vẫn có thể thử bản không dấu).
  - Gợi ý cấu hình env:
    - `POSCAKE_BASE=http://160.250.216.28:13886`
  - Luồng dùng trong đánh giá một bản ghi:
    1) Lấy `province_name`, `district_name`, `commune_name` từ `top1` (khi thiếu thì để trống tham số tương ứng).
    2) Gọi API. Nếu trả về các ID hợp lệ cho cấp đã cung cấp tên thì ghi vào `found_items` theo thứ tự tỉnh→huyện→xã.
    3) Nếu thiếu ID của cấp xã nhưng đã có tỉnh/huyện, bổ sung lỗi: `"Không tìm thấy xã: <tên đã chuẩn hóa> trong huyện <tên huyện chính tắc>"` (giống ví dụ).
    4) Nếu chỉ có tỉnh, ghi lỗi tương ứng cho huyện; nếu không có tỉnh, ghi lỗi cho tỉnh.
  - Ví dụ gọi nhanh bằng Python:
    ```python
    import os, requests

    def poscake_lookup(province_name: str, district_name: str, commune_name: str, timeout: int = 15):
        base = os.getenv("POSCAKE_BASE", "http://160.250.216.28:13886")
        url = f"{base}/api/v1/poscake/geo/location-ids"
        params = {
            "province_name": province_name or "",
            "district_name": district_name or "",
            "commune_name": commune_name or "",
        }
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()  # Kết quả chứa các ID/tên nếu tìm được
    ```

- Matching:
  - Chuẩn hóa tên để match: lower, bỏ dấu, bỏ tiền tố cấp hành chính (phường/xã/thị trấn, quận/huyện/tp/thị xã).
  - Dùng fuzzy (threshold gợi ý: province ≥ 0.85, district ≥ 0.83, commune ≥ 0.80; có thể điều chỉnh theo thực tế).
  - Ưu tiên kết quả trùng khớp chính tắc (exact) trước, sau đó mới fuzzy.

### Quy tắc xử lý các lỗi thường gặp (từ file failed mẫu)
- Sai/thiếu dấu: `van khe` → `Văn Khê`; `sa ly` → `Sa Lý`.
- Biến thể cấp hành chính: `Thành phố Đông Triều` vs `Đông Triều`; `Quận Hà Đông` vs `Hà Đông`.
- Thành phần thôn/xóm/khu/liền kề: đưa hết vào `address`; ví dụ: `số nhà 30 liền kề 3 khu đô thị mới`.
- Nếu chỉ khớp tới `province` và `district` nhưng không có `commune`: báo lỗi đúng mẫu: `"Không tìm thấy xã: <tên chuẩn hóa> trong huyện <tên huyện chính tắc>"`.

### Đầu ra cho từng bản ghi (theo input `examples/failed_addresses (2).json`)
- Với mỗi `raw`, tạo một object kết quả theo schema nêu trên.
- `top1`: lấy từ Gemini + chỉnh sửa nhẹ sau xác thực tên chính tắc.
- `variants`: liệt kê 1–3 biến thể hữu ích (không spam), ví dụ thêm/bớt tiền tố hành chính hoặc thêm dấu.
- `top1_result`: điền `*_id` từ API/dataset nếu tìm được; ghi `found_items` và `errors` nếu thiếu cấp.

### Kiểm thử & tiêu chí chấp nhận
- Chạy qua toàn bộ danh sách failed: không exception, mỗi bản ghi có output hợp lệ theo schema.
- Tỷ lệ `success=true` tăng đáng kể (≥ 80% cho bộ mẫu) sau khi áp dụng quy tắc và variants.
- Log cảnh báo rõ ràng khi có nhiều ứng viên tương đương, tránh chọn sai tỉnh/huyện.
 - Khi có PosCake Geo API, đánh giá `success` dựa trên response ID từ endpoint `location-ids`; nếu thiếu `commune_id` nhưng có `province_id`/`district_id`, ghi `found_items` cho cấp tìm thấy và `errors` theo mẫu.

### Gợi ý triển khai (tối thiểu)
1) Viết hàm `extract_address_fields(raw)` dùng Gemini theo System Prompt.
2) Viết `normalize_admin_names(candidate)` để tinh chỉnh tên: thêm dấu/chính tả, chuẩn hóa tiền tố.
3) Viết `match_admin(candidate)` để gọi API/dataset lấy `province_id` → `district_id` → `commune_id` và tên chính tắc.
4) Sinh `variants` dựa trên tiền tố và dấu; re-check nhanh biến thể nếu `commune` chưa tìm được.
5) Kết hợp thành object output cuối cùng.

### Ghi chú
- Chi phí/độ trễ: chỉ gọi LLM 1 lần/bản ghi; phần còn lại dùng fuzzy + API nội bộ.
- Có thể batch hóa xác thực API theo tỉnh/huyện để giảm request.


