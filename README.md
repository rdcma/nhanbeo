## Product QA Pipeline (Hybrid RAG + LLM)

### 1) Chức năng chính
- Nhận diện ý định (intent): `product_query` | `smalltalk` | `other`.
- Trích xuất từ khóa (keyword) tiếng Việt (lọc từ chỉ định “này/kia/đó…”, tối thiểu 2 từ). Chỉ thêm “điện” vào “nồi cơm” khi câu có nhắc “điện”.
- Truy xuất ứng viên sản phẩm bằng fuzzy + (tùy chọn) embedding.
- Rerank bằng LLM để chọn các `display_id` phù hợp, hỗ trợ nhiều danh mục.
- Thứ tự ưu tiên: (1) sản phẩm trong `--priority_json`, (2) vị trí sớm hơn trong nguồn (API/CSV), (3) điểm khớp.

### 2) Yêu cầu hệ thống
- Python 3.10+ (khuyến nghị 3.11+)
- Nguồn dữ liệu:
  - API: `--api_url "http://160.250.216.28:13886/api/v1/products/sold-quantity/list"` (khuyên dùng)
  - Hoặc CSV: `Current_product_names__with_clean_name_.csv` (cột: `display_id`, `name`, `clean_name`)
- Biến môi trường:
  - `GOOGLE_API_KEY` (bắt buộc)
  - `GOOGLE_MODEL` (tùy chọn, mặc định: `gemini-2.5-flash`) – intent/keyword/rerank
  - `GOOGLE_EMBED_MODEL` (tùy chọn, mặc định: `text-embedding-004`) – embedding

### 3) Cài đặt và môi trường ảo
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Tạo file `.env` tại thư mục gốc:
```
GOOGLE_API_KEY=your_google_api_key_here
# Tùy chọn (đã có mặc định):
# GOOGLE_MODEL=gemini-2.5-flash
# GOOGLE_EMBED_MODEL=text-embedding-004
```

### 4) Chạy demo
- Tương tác (hỏi khi thiếu `--q`):
```bash
python run_demo.py
```

- Truyền câu hỏi trực tiếp:
```bash
python run_demo.py --q "cho tôi nồi ủ, nồi cơm điện"
```

- Bật embedding (lần đầu sẽ build và lưu cache):
```bash
python run_demo.py --embed --q "nồi cơm điện"
```

- Đổi model rerank/intent/keyword (ví dụ dùng `gemini-2.5-flash`):
```bash
export GOOGLE_MODEL=gemini-2.5-flash
python run_demo.py --embed --q "nồi cơm điện"
```

- Dùng API làm nguồn dữ liệu (khuyên dùng):
```bash
python run_demo.py --api_url "http://160.250.216.28:13886/api/v1/products/sold-quantity/list" --embed --q "nồi cơm"
```

- Ưu tiên theo lịch sử đơn hàng (`--priority_json`):
```bash
python run_demo.py \
  --api_url "http://160.250.216.28:13886/api/v1/products/sold-quantity/list" \
  --priority_json examples/orders_priority.json \
  --embed \
  --q "nồi cơm"
```

### 5) Embedding cache (tiết kiệm chi phí và thời gian)
- Cache lưu tại `product_qa/.cache/`:
  - Vector: `<embed_model>_<source_key>.npy`
  - Metadata: `<embed_model>_<source_key>.json` (chứa `ids`, `names` theo thứ tự).
- `source_key` dựa trên nguồn dữ liệu (URL API hoặc đường dẫn tuyệt đối CSV).
- Cập nhật gia tăng: chỉ embed thêm `(display_id, clean_name)` chưa có trong cache; phần còn lại tái sử dụng. Log ví dụ:
  - `[EmbeddingIndex] Preparing embeddings (incremental)...`
  - `[EmbeddingIndex] Embedding N new names (reused M).`
  - `[EmbeddingIndex] Saved cache: text-embedding-004_xxxxxxxx.npy shape=(..., 768)`

### 6) Nguồn dữ liệu và ưu tiên
- API: map `display_id`, `name` → `clean_name`, đặt `priority` theo thứ tự API trả về.
- CSV: yêu cầu cột `display_id`, `clean_name` (có thể có thêm `name`). `priority` là chỉ số dòng (0-based).
- Ưu tiên tổng hợp: (1) `preferred_ids` từ `--priority_json`, (2) `priority` nhỏ hơn tốt hơn, (3) điểm khớp.

### 7) Cấu trúc thư mục quan trọng
```
product_qa/
  pipeline.py      # pipeline chính (intent, keyword, retrieve, rerank, cache)
  __init__.py
  .cache/          # nơi lưu embeddings .npy + .json (tự tạo khi build)
run_demo.py        # CLI demo
requirements.txt
Current_product_names__with_clean_name_.csv
```

### 8) Ví dụ kết quả đầu ra
```json
{
  "intent": "product_query",
  "keywords": ["nồi ủ", "nồi cơm điện"],
  "candidates": [
    {"display_id": "C688", "clean_name": "Nồi ủ chân không Gume GM-2086", "score": 0.92, "priority": 61, "source": "fuzzy"}
  ],
  "reranked": {
    "selections": [
      {"keyword": "nồi ủ", "display_ids": ["C688", "C600"]},
      {"keyword": "nồi cơm điện", "display_ids": ["C540", "K688"]}
    ],
    "needs_clarification": false,
    "clarify_question": ""
  },
  "final_product": {
    "keyword": "nồi cơm",
    "product": {"display_id": "C540", "clean_name": "NỒI CƠM TÁCH ĐƯỜNG SK SK1009", "score": 0.73, "priority": 19, "source": "fuzzy"}
  }
}
```

### 9) Tùy biến nhanh
- Điều chỉnh `top_k` và logic merge/ưu tiên trong `retrieve_candidates`.
- Sửa prompt trong các hằng `INTENT_PROMPT`, `KEYWORD_PROMPT`, và hàm `rerank_with_llm`.
- Có thể “làm giàu” văn bản embed (thêm từ đồng nghĩa) trước khi gọi embedding.
 - Mặc định tìm kiếm embedding trả `top_k=10` (xem `EmbeddingIndex.search`).

### 10) Khắc phục sự cố
- Cảnh báo LibreSSL từ urllib3: nâng cấp Python (pyenv/conda) để dùng OpenSSL mới; cảnh báo không chặn chạy.
- Không thấy file cache: đảm bảo chạy với `--embed` và có log `[EmbeddingIndex] Saved cache ...`.
- Lỗi API: kiểm tra `GOOGLE_API_KEY`, hạn mức/quyền truy cập, hoặc thử lại model khác qua `GOOGLE_MODEL`.
- JSON lỗi định dạng từ LLM: code đã có bắt lỗi và cố gắng trích JSON; xem `raw` trong output để debug.
