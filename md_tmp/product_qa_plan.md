## Kế hoạch triển khai hệ thống hỏi đáp sản phẩm (VN) — Chọn RAG hay LLM thuần?

### Mục tiêu
- **Hiểu câu hỏi mơ hồ**: ví dụ "cho tôi nồi ủ, nồi cơm điện" → tìm đúng sản phẩm tương ứng trong CSV.
- **Trích xuất keyword tự nhiên**: lấy ra danh mục/ý chính người dùng nói.
- **Nhận diện ý định**: phân biệt hỏi sản phẩm vs smalltalk/bâng quơ.

### Dữ liệu hiện có
- File: `Current_product_names__with_clean_name_.csv` với các cột: `display_id`, `name`, `clean_name`.
- Trường sử dụng chính cho tìm kiếm: `clean_name` (đã được làm sạch). Trả về cho UI/logic: `display_id` + `clean_name`.

---

## So sánh: LLM thuần vs RAG

- **LLM thuần**
  - **Ưu**: thiết lập nhanh (chỉ prompt), tốt để phân loại ý định và trích xuất keyword.
  - **Nhược**: không gắn với dữ liệu sản phẩm nội bộ → dễ "bịa" tên sản phẩm; khó bảo đảm tính nhất quán `display_id`.

- **RAG** (Retrieve-then-Generate)
  - **Ưu**: truy xuất trực tiếp từ tập sản phẩm (theo `clean_name`) → trả về đúng `display_id`, mở rộng truy vấn mơ hồ, kiểm soát tốt.
  - **Nhược**: cần tạo chỉ mục (fuzzy/embedding) và hạ tầng truy xuất.

- **Khuyến nghị**: Dùng kiến trúc **Hybrid**
  - LLM cho: nhận diện ý định + trích xuất keyword + rerank/mapping.
  - RAG cho: truy xuất ứng viên từ CSV bằng fuzzy/embedding (ưu tiên `clean_name`).

---

## Kiến trúc đề xuất (Hybrid)

1) **Intent detection** (LLM): phân loại câu vào 3 nhóm:
   - `product_query` (tìm/đề xuất sản phẩm)
   - `smalltalk` (chào hỏi, cảm ơn, thời tiết, v.v.)
   - `other` (khác, không rõ)

2) **Keyword extraction** (LLM): trích xuất keyword/danh mục tiếng Việt, dạng ngắn gọn (ví dụ: "nồi ủ", "nồi cơm điện").

3) **Candidate retrieval** (RAG):
   - B1: Chuẩn hóa truy vấn (lowercase, bỏ dấu/stopwords nhẹ).
   - B2: Truy xuất ứng viên từ `clean_name` theo 2 chiến lược song song:
     - Fuzzy matching (token set ratio, substring, regex từ khóa).
     - Embedding search (cosine similarity trên vector `clean_name`).
   - B3: Hợp nhất và cắt `top_k` (ví dụ 20).

4) **Rerank + Disambiguate** (LLM):
   - Cho LLM xem `top_k` ứng viên (chỉ `display_id | clean_name`).
   - Yêu cầu LLM chọn các `display_id` phù hợp với truy vấn; nếu nhiều danh mục trong câu ("nồi ủ, nồi cơm điện"), trả 2 nhóm.
   - Nếu mơ hồ, yêu cầu hỏi lại, kèm gợi ý bộ lọc.

5) **Finalization**:
   - Trả về danh sách: `{display_id, clean_name, score, matched_keywords}`.
   - Log ý định, keyword, các ứng viên và lựa chọn cuối để phục vụ đánh giá.

---

## Prompt đề xuất (Tiếng Việt)

### 1) Intent detection
```
Bạn là bộ phân loại ý định. Phân loại câu sau vào 1 trong các nhãn: product_query, smalltalk, other.
Chỉ trả về JSON: {"intent": "..."}
Câu: "{{user_text}}"
```

### 2) Keyword extraction
```
Nhiệm vụ: trích xuất các từ khóa sản phẩm ngắn gọn (tiếng Việt), có ích cho tìm kiếm.
Trả về JSON: {"keywords": ["...", "..."]}
Ví dụ: "cho tôi nồi ủ, nồi cơm điện" -> {"keywords": ["nồi ủ", "nồi cơm điện"]}
Câu: "{{user_text}}"
```

### 3) Rerank chọn sản phẩm
```
Bạn nhận: truy vấn người dùng và danh sách ứng viên (display_id | clean_name).
Hãy chọn các sản phẩm phù hợp nhất, có thể chia theo từng từ khóa.
Trả về JSON:
{
  "selections": [
    {"keyword": "...", "display_ids": ["C688", "C600"]}
  ],
  "needs_clarification": false,
  "clarify_question": ""
}

Truy vấn: "{{user_text}}"
Từ khóa: {{keywords_json}}
Ứng viên (tối đa 20):
{{display_id}} | {{clean_name}}
...
```

---

## Từ điển từ khóa khởi tạo (seed)
- "nồi ủ": match từ "nồi ủ", "ủ chân không", "ủ cháo" → ưu tiên `clean_name` chứa "Nồi ủ"/"chân không".
- "nồi cơm điện": match "nồi cơm", "cơm điện", "tách đường".
- Có thể bổ sung dần theo log.

---

## Thiết lập Google Generative AI
- Biến môi trường `.env`: `GOOGLE_API_KEY=...`
- Thư viện Python: `google-generativeai`, `python-dotenv`.

```bash
pip install google-generativeai python-dotenv rapidfuzz pandas numpy scikit-learn
```

---

## Mẫu code Python (MVP)

```python
import os
import re
import json
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from rapidfuzz import fuzz, process
from sklearn.metrics.pairwise import cosine_similarity

import google.generativeai as genai


def load_api_key():
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GOOGLE_API_KEY in .env")
    genai.configure(api_key=api_key)


def load_products(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    # Chuẩn hóa field phụ cho tìm kiếm
    df["clean_lower"] = df["clean_name"].astype(str).str.lower()
    return df[["display_id", "clean_name", "clean_lower"]]


def basic_normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def fuzzy_candidates(df: pd.DataFrame, query: str, limit: int = 20):
    # Dùng token_set_ratio trên clean_lower
    choices = df["clean_lower"].tolist()
    results = process.extract(
        basic_normalize(query), choices, scorer=fuzz.token_set_ratio, limit=limit
    )
    # results: list of (matched_text, score, index)
    rows = []
    for _, score, idx in results:
        r = df.iloc[idx]
        rows.append({
            "display_id": r["display_id"],
            "clean_name": r["clean_name"],
            "score": float(score)/100.0,
            "source": "fuzzy"
        })
    return rows


def embed_texts_gemini(texts: list[str], model_name: str = "text-embedding-004") -> np.ndarray:
    model = genai.GenerativeModel(model_name)
    # Gemini Embedding API hiện dùng theo endpoint riêng; một số SDK dùng genai.embed_content
    # Sử dụng embed_content để ổn định
    vectors = []
    for t in texts:
        emb = genai.embed_content(model=model_name, content=t)
        vectors.append(emb["embedding"])  # type: ignore
    return np.array(vectors)


class EmbeddingIndex:
    def __init__(self, df: pd.DataFrame, model_name: str = "text-embedding-004"):
        self.df = df.reset_index(drop=True)
        self.model_name = model_name
        self.matrix = embed_texts_gemini(self.df["clean_name"].tolist(), model_name)

    def search(self, query: str, top_k: int = 20):
        qv = embed_texts_gemini([query], self.model_name)[0]
        sims = cosine_similarity([qv], self.matrix)[0]
        top_idx = np.argsort(-sims)[:top_k]
        rows = []
        for i in top_idx:
            r = self.df.iloc[i]
            rows.append({
                "display_id": r["display_id"],
                "clean_name": r["clean_name"],
                "score": float(sims[i]),
                "source": "embedding"
            })
        return rows


def call_llm_json(prompt: str, model_name: str = "gemini-1.5-pro") -> dict:
    model = genai.GenerativeModel(model_name)
    resp = model.generate_content(prompt)
    text = resp.candidates[0].content.parts[0].text  # type: ignore
    try:
        return json.loads(text)
    except Exception:
        # Fallback: cố gắng trích JSON trong text
        m = re.search(r"\{[\s\S]*\}", text)
        return json.loads(m.group(0)) if m else {"error": "Malformed JSON", "raw": text}


INTENT_PROMPT = """
Bạn là bộ phân loại ý định. Phân loại câu vào: product_query, smalltalk, other.
Chỉ trả lời JSON: {"intent": "..."}
Câu: "{user_text}"
"""

KEYWORD_PROMPT = """
Nhiệm vụ: trích xuất từ khóa sản phẩm tiếng Việt (ngắn gọn) phục vụ tìm kiếm.
Chỉ trả lời JSON: {"keywords": ["..."]}
Câu: "{user_text}"
"""

def detect_intent(user_text: str) -> str:
    out = call_llm_json(INTENT_PROMPT.format(user_text=user_text))
    return out.get("intent", "other")


def extract_keywords(user_text: str) -> list[str]:
    out = call_llm_json(KEYWORD_PROMPT.format(user_text=user_text))
    return out.get("keywords", [])


def retrieve_candidates(df: pd.DataFrame, idx: EmbeddingIndex | None, keywords: list[str], user_text: str, top_k: int = 20):
    bag = []
    # Fuzzy theo user_text
    bag.extend(fuzzy_candidates(df, user_text, limit=top_k))
    # Fuzzy theo từng keyword
    for kw in keywords:
        bag.extend(fuzzy_candidates(df, kw, limit=max(5, top_k//len(keywords) if keywords else top_k)))
    # Embedding nếu có index
    if idx is not None:
        bag.extend(idx.search(user_text, top_k=top_k))
    # Hợp nhất theo display_id, lấy max score
    combined = {}
    for r in bag:
        did = r["display_id"]
        if did not in combined:
            combined[did] = r
        else:
            combined[did]["score"] = max(combined[did]["score"], r["score"])
    # Sắp xếp theo score
    merged = sorted(combined.values(), key=lambda x: -x["score"])[:top_k]
    return merged


def rerank_with_llm(user_text: str, keywords: list[str], candidates: list[dict]) -> dict:
    cand_lines = "\n".join([f"{c['display_id']} | {c['clean_name']}" for c in candidates])
    prompt = f"""
Bạn nhận: truy vấn người dùng và danh sách ứng viên (display_id | clean_name).
Hãy chọn các sản phẩm phù hợp nhất, có thể chia theo từng từ khóa.
Trả về JSON:\n{{
  "selections": [
    {{"keyword": "...", "display_ids": ["..."]}}
  ],
  "needs_clarification": false,
  "clarify_question": ""
}}

Truy vấn: "{user_text}"
Từ khóa: {json.dumps(keywords, ensure_ascii=False)}
Ứng viên:\n{cand_lines}
"""
    return call_llm_json(prompt)


def product_qa_pipeline(csv_path: str, build_embedding: bool = False):
    load_api_key()
    df = load_products(csv_path)
    idx = EmbeddingIndex(df) if build_embedding else None

    def ask(user_text: str) -> dict:
        intent = detect_intent(user_text)
        if intent != "product_query":
            return {"intent": intent, "message": "Đây không phải câu hỏi sản phẩm."}

        keywords = extract_keywords(user_text)
        cands = retrieve_candidates(df, idx, keywords, user_text)
        if not cands:
            return {"intent": intent, "keywords": keywords, "results": []}

        reranked = rerank_with_llm(user_text, keywords, cands)
        return {
            "intent": intent,
            "keywords": keywords,
            "candidates": cands,
            "reranked": reranked,
        }

    return ask


if __name__ == "__main__":
    pipeline = product_qa_pipeline(
        csv_path="Current_product_names__with_clean_name_.csv",
        build_embedding=False  # bật True khi cần embedding search
    )

    # Ví dụ
    query = "cho tôi nồi ủ, nồi cơm điện"
    result = pipeline(query)
    print(json.dumps(result, ensure_ascii=False, indent=2))
```

Lưu ý:
- Với `embed_texts_gemini`, nếu SDK thay đổi, tham khảo tài liệu chính thức về Embedding model `text-embedding-004` và phương thức `embed_content`.
- Nếu số lượng sản phẩm lớn, lưu vector vào file `.npy`/SQLite/FAISS thay vì tính lại mỗi lần.

---

## Đánh giá và logging
- Log: `intent`, `keywords`, `top_k candidates`, `final selections`, `needs_clarification`.
- Metric đề xuất:
  - Precision@k/Recall@k theo mapping thủ công (gold set nhỏ ban đầu).
  - Tỷ lệ phân loại ý định đúng (confusion matrix).
  - A/B prompt cho rerank.

---

## Kế hoạch triển khai
- Tuần 1: MVP (fuzzy + intent/keyword LLM + rerank LLM) chạy trên CSV hiện tại.
- Tuần 2: Bổ sung embedding index, log và dashboard giám sát.
- Tuần 3: Mở rộng từ điển từ khóa và bộ test; tối ưu prompt.

---

## Rủi ro và phương án
- Biến động API/SDK: cô lập adapter gọi LLM/Embedding.
- Truy vấn mơ hồ nhiều danh mục: luôn hỗ trợ `needs_clarification` để hỏi lại.
- Nhiễu tiếng Việt: chuẩn hóa, bổ sung từ điển đồng nghĩa từ log người dùng.


