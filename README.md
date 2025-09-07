## Product QA Pipeline (Hybrid RAG + LLM)

### 1) Chức năng chính
- Nhận diện ý định (intent): `product_query` | `smalltalk` | `other`.
- Trích xuất từ khóa (keyword) tiếng Việt từ câu hỏi tự nhiên.
- Truy xuất ứng viên sản phẩm từ CSV bằng fuzzy + (tùy chọn) embedding.
- Rerank bằng LLM để chọn các `display_id` phù hợp, hỗ trợ nhiều danh mục cùng lúc.
- Ưu tiên sản phẩm xuất hiện sớm hơn trong CSV (thứ tự dòng).

### 2) Yêu cầu hệ thống
- Python 3.10+ (khuyến nghị 3.11+)
- File CSV: `Current_product_names__with_clean_name_.csv` với cột: `display_id`, `name`, `clean_name`
- Biến môi trường:
  - `GOOGLE_API_KEY` (bắt buộc)
  - `GOOGLE_MODEL` (tùy chọn, mặc định: `gemini-2.5-flash`) – dùng cho intent/keyword/rerank
  - `GOOGLE_EMBED_MODEL` (tùy chọn, mặc định: `text-embedding-004`) – dùng cho embedding

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

### 5) Embedding cache (tiết kiệm chi phí và thời gian)
- Khi chạy với `--embed`, hệ thống embed `clean_name` và lưu cache tại: `product_qa/.cache/` gồm:
  - File vector: `<model>_<fingerprint>.npy`
  - Metadata: `<model>_<fingerprint>.json`
- Fingerprint dựa trên `display_id|clean_name` và tên model. Thay đổi CSV hoặc model → cache khác.
- Lần sau chạy lại (cùng CSV + model) sẽ tự động load cache, không embed lại. Bạn sẽ thấy log:
  - Lần đầu: `[EmbeddingIndex] Building embeddings ...` + `[EmbeddingIndex] Saved cache ...`
  - Lần sau: `[EmbeddingIndex] Loaded cache ...`

### 6) Quy ước dữ liệu CSV và ưu tiên
- Cột bắt buộc: `display_id`, `clean_name`. `name` dùng để tham khảo.
- Dòng càng trên đầu càng ưu tiên khi điểm số bằng nhau (đã thêm trường `priority`).
- Tìm kiếm fuzzy theo `clean_name` (hạ chữ thường, bỏ khoảng trắng thừa).

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
  }
}
```

### 9) Tùy biến nhanh
- Điều chỉnh `top_k` và logic merge/ưu tiên trong `retrieve_candidates`.
- Sửa prompt trong các hằng `INTENT_PROMPT`, `KEYWORD_PROMPT`, và hàm `rerank_with_llm`.
- Có thể “làm giàu” văn bản embed (thêm từ đồng nghĩa) trước khi gọi embedding.

### 10) Khắc phục sự cố
- Cảnh báo LibreSSL từ urllib3: nâng cấp Python (pyenv/conda) để dùng OpenSSL mới; cảnh báo không chặn chạy.
- Không thấy file cache: đảm bảo chạy với `--embed` và có log `[EmbeddingIndex] Saved cache ...`.
- Lỗi API: kiểm tra `GOOGLE_API_KEY`, hạn mức/quyền truy cập, hoặc thử lại model khác qua `GOOGLE_MODEL`.
- JSON lỗi định dạng từ LLM: code đã có bắt lỗi và cố gắng trích JSON; xem `raw` trong output để debug.
