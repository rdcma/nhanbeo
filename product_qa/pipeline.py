import os
import re
import json
import time
import hashlib
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import pandas as pd
import numpy as np
from dotenv import load_dotenv
from rapidfuzz import fuzz, process
from sklearn.metrics.pairwise import cosine_similarity

import google.generativeai as genai
import requests


def load_api_key() -> None:
    load_dotenv()
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GOOGLE_API_KEY in .env")
    genai.configure(api_key=api_key)


def get_llm_model_name() -> str:
    # Model cho intent/keyword/rerank
    return os.getenv("GOOGLE_MODEL", "gemini-2.5-flash")


def get_embed_model_name() -> str:
    # Model cho embedding
    return os.getenv("GOOGLE_EMBED_MODEL", "text-embedding-004")


def load_products(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    # priority: vị trí trong file, dòng càng nhỏ càng ưu tiên
    df["priority"] = np.arange(len(df), dtype=int)
    df["clean_lower"] = df["clean_name"].astype(str).str.lower()
    return df[["display_id", "clean_name", "clean_lower", "priority"]]


def load_products_from_api(api_url: str, timeout: int = 20) -> pd.DataFrame:
    resp = requests.get(api_url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("current") or data.get("data") or []
    # Map về display_id, clean_name; ưu tiên order như API trả về
    rows = []
    for i, it in enumerate(items):
        did = str(it.get("display_id") or it.get("product_display_id") or "").strip()
        name = str(it.get("name") or it.get("clean_name") or "").strip()
        if not did or not name:
            continue
        rows.append({
            "display_id": did,
            "clean_name": name,
            "priority": i,  # thứ tự theo API
        })
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("API returned no valid products")
    df["clean_lower"] = df["clean_name"].astype(str).str.lower()
    return df[["display_id", "clean_name", "clean_lower", "priority"]]


def basic_normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def fuzzy_candidates(df: pd.DataFrame, query: str, limit: int = 20) -> List[Dict]:
    choices = df["clean_lower"].tolist()
    results = process.extract(
        basic_normalize(query), choices, scorer=fuzz.token_set_ratio, limit=limit
    )
    rows: List[Dict] = []
    for _, score, idx in results:
        r = df.iloc[idx]
        rows.append({
            "display_id": r["display_id"],
            "clean_name": r["clean_name"],
            "score": float(score) / 100.0,
            "priority": int(r["priority"]),
            "source": "fuzzy",
        })
    return rows


def _to_vector(emb_resp) -> List[float]:
    # Normalize various response shapes from SDK
    # Possible shapes:
    # - {"embedding": {"values": [...]}}
    # - {"embedding": [...]}
    # - resp.embedding.values
    # - resp.embedding
    if isinstance(emb_resp, dict):
        emb = emb_resp.get("embedding")
        if isinstance(emb, dict) and "values" in emb:
            return emb["values"]
        if isinstance(emb, list):
            return emb
    if hasattr(emb_resp, "embedding"):
        emb = getattr(emb_resp, "embedding")
        if hasattr(emb, "values"):
            return emb.values  # type: ignore
        return emb  # type: ignore
    raise ValueError("Unknown embedding response shape")


def embed_texts_gemini(texts: List[str], model_name: str = "text-embedding-004") -> np.ndarray:
    # Prefer embed_content for embeddings
    vectors: List[List[float]] = []
    for t in texts:
        emb = genai.embed_content(model=model_name, content=t)
        vectors.append(_to_vector(emb))
    return np.array(vectors)


class EmbeddingIndex:
    def __init__(self, df: pd.DataFrame, model_name: Optional[str] = None, source_key: str = "default") -> None:
        # Giữ nguyên cột priority từ df gốc
        self.df = df.reset_index(drop=True)
        self.model_name = model_name or get_embed_model_name()
        self.source_key = source_key
        self.matrix = self._load_or_build_index()

    def _cache_dir(self) -> Path:
        base = Path(__file__).parent / ".cache"
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _key_from_source(self) -> str:
        hasher = hashlib.sha256()
        hasher.update(self.model_name.encode("utf-8"))
        hasher.update(b"|")
        hasher.update(self.source_key.encode("utf-8"))
        return hasher.hexdigest()[:16]

    def _cache_paths(self) -> Dict[str, Path]:
        key = f"{self.model_name}_{self._key_from_source()}"
        cache_dir = self._cache_dir()
        return {
            "matrix": cache_dir / f"{key}.npy",
            "meta": cache_dir / f"{key}.json",
        }

    def _try_load_cache(self) -> Optional[Tuple[np.ndarray, Dict]]:
        paths = self._cache_paths()
        matrix_path, meta_path = paths["matrix"], paths["meta"]
        if matrix_path.exists() and meta_path.exists():
            try:
                with meta_path.open("r", encoding="utf-8") as f:
                    meta = json.load(f)
                if (
                    meta.get("model_name") == self.model_name
                ):
                    arr = np.load(matrix_path)
                    print(f"[EmbeddingIndex] Loaded cache: {matrix_path.name} shape={arr.shape}")
                    return arr, meta
            except Exception:
                return None
        return None

    def _save_cache(self, matrix: np.ndarray, ids: List[str], names: List[str]) -> None:
        paths = self._cache_paths()
        matrix_path, meta_path = paths["matrix"], paths["meta"]
        try:
            np.save(matrix_path, matrix.astype(np.float32))
            meta = {
                "model_name": self.model_name,
                "num_rows": int(len(ids)),
                "created_ts": int(time.time()),
                "ids": ids,
                "names": names,
            }
            with meta_path.open("w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False)
            print(f"[EmbeddingIndex] Saved cache: {matrix_path.name} shape={matrix.shape}")
        except Exception:
            pass

    def _build_or_update_matrix(self) -> np.ndarray:
        cached = self._try_load_cache()
        df_ids = self.df["display_id"].astype(str).tolist()
        df_names = self.df["clean_name"].astype(str).tolist()

        if cached is None:
            vectors = embed_texts_gemini(df_names, self.model_name)
            self._save_cache(vectors, df_ids, df_names)
            return vectors

        cached_matrix, meta = cached
        cached_ids: List[str] = meta.get("ids", [])
        cached_names: List[str] = meta.get("names", [])
        index_map: Dict[Tuple[str, str], int] = {
            (cid, cname): i for i, (cid, cname) in enumerate(zip(cached_ids, cached_names))
        }

        rows: List[np.ndarray] = []
        missing_names: List[str] = []
        missing_positions: List[int] = []
        reused = 0
        for pos, (did, name) in enumerate(zip(df_ids, df_names)):
            key = (did, name)
            idx = index_map.get(key)
            if idx is not None:
                rows.append(cached_matrix[idx])
                reused += 1
            else:
                rows.append(None)  # placeholder
                missing_names.append(name)
                missing_positions.append(pos)

        new_vectors = None
        if missing_names:
            print(f"[EmbeddingIndex] Embedding {len(missing_names)} new names (reused {reused}).")
            new_vectors = embed_texts_gemini(missing_names, self.model_name)

        if missing_names:
            it = iter(new_vectors)
            for i in missing_positions:
                rows[i] = next(it)

        matrix = np.vstack(rows)
        self._save_cache(matrix, df_ids, df_names)
        return matrix

    def _load_or_build_index(self) -> np.ndarray:
        print("[EmbeddingIndex] Preparing embeddings (incremental)...")
        return self._build_or_update_matrix()

    def search(self, query: str, top_k: int = 10) -> List[Dict]:
        qv = embed_texts_gemini([query], self.model_name)[0]
        sims = cosine_similarity([qv], self.matrix)[0]
        top_idx = np.argsort(-sims)[:top_k]
        rows: List[Dict] = []
        for i in top_idx:
            r = self.df.iloc[i]
            rows.append({
                "display_id": r["display_id"],
                "clean_name": r["clean_name"],
                "score": float(sims[i]),
                "priority": int(r["priority"]),
                "source": "embedding",
            })
        return rows


def call_llm_json(prompt: str, model_name: Optional[str] = None) -> Dict:
    model = genai.GenerativeModel(model_name or get_llm_model_name())
    resp = model.generate_content(prompt)
    text = resp.candidates[0].content.parts[0].text  # type: ignore
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        return json.loads(m.group(0)) if m else {"error": "Malformed JSON", "raw": text}


INTENT_PROMPT = (
    'Bạn là bộ phân loại ý định. Phân loại câu vào: product_query, smalltalk, other.'
    '\nChỉ trả lời JSON: {"intent": "..."}'
    '\nCâu: "{user_text}"'
)


KEYWORD_PROMPT = (
    'Nhiệm vụ: trích xuất cụm từ khóa sản phẩm tiếng Việt (ưu tiên cụm >= 2 từ). ' \
    'Bỏ qua từ chỉ định/điểm chỉ như: "này", "kia", "đó", và các từ nền tảng như FB/live.' \
    '\nChỉ trả lời JSON: {"keywords": ["..."]}'
    '\nCâu: "{user_text}"'
)


def detect_intent(user_text: str) -> str:
    prompt = INTENT_PROMPT.replace("{user_text}", user_text)
    out = call_llm_json(prompt)
    return out.get("intent", "other")


DEICTIC_WORDS = {"này", "kia", "đó", "ấy", "đây", "đấy"}
NOISE_WORDS = {"fb", "facebook", "live", "livestream", "video", "xem", "kịp", "thấy", "nhưng", "chưa", "được", "của", "chị", "anh", "em", "mình", "tôi"}


def _normalize_keyword(kw: str, add_electric: bool = False) -> str:
    text = kw.strip().lower()
    # remove basic punctuation
    text = re.sub(r"[\.,;:!?()\[\]\"']", " ", text)
    tokens = [t for t in text.split() if t]
    # remove noise tokens anywhere
    tokens = [t for t in tokens if t not in NOISE_WORDS]
    # strip trailing deictic words
    while tokens and tokens[-1] in DEICTIC_WORDS:
        tokens.pop()
    normalized = " ".join(tokens).strip()
    # domain enrichment: chỉ thêm "điện" khi có ngữ cảnh liên quan tới điện
    if add_electric and normalized.startswith("nồi cơm") and "điện" not in normalized:
        normalized = (normalized + " điện").strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def extract_keywords(user_text: str) -> List[str]:
    prompt = KEYWORD_PROMPT.replace("{user_text}", user_text)
    out = call_llm_json(prompt)
    raw_kws: List[str] = out.get("keywords", [])
    lower_text = user_text.lower()
    # Chỉ coi là có ngữ cảnh điện khi người dùng nhắc từ "điện" rõ ràng
    electric_hint = "điện" in lower_text
    # normalize + dedup (preserve order)
    seen = set()
    norm_kws: List[str] = []
    for kw in raw_kws:
        if not isinstance(kw, str):
            continue
        nk = _normalize_keyword(kw, add_electric=electric_hint)
        if nk and nk not in seen:
            seen.add(nk)
            norm_kws.append(nk)
    # keep only >= 2 tokens
    filtered = [k for k in norm_kws if len(k.split()) >= 2]
    return filtered if filtered else norm_kws


def retrieve_candidates(
    df: pd.DataFrame,
    idx: Optional[EmbeddingIndex],
    keywords: List[str],
    user_text: str,
    top_k: int = 20,
    preferred_ids: Optional[set] = None,
) -> List[Dict]:
    bag: List[Dict] = []
    bag.extend(fuzzy_candidates(df, user_text, limit=top_k))
    for kw in keywords:
        bag.extend(
            fuzzy_candidates(
                df, kw, limit=max(5, top_k // len(keywords) if keywords else top_k)
            )
        )
    if idx is not None:
        bag.extend(idx.search(user_text, top_k=top_k))
    combined: Dict[str, Dict] = {}
    for r in bag:
        did = r["display_id"]
        if did not in combined:
            combined[did] = r
        else:
            combined[did]["score"] = max(combined[did]["score"], r["score"])
            # Ưu tiên priority nhỏ hơn (gần đầu file hơn)
            combined[did]["priority"] = min(
                combined[did].get("priority", 1_000_000), r.get("priority", 1_000_000)
            )
    def sort_key(item: Dict):
        # Ưu tiên 1: có trong preferred_ids
        pref_flag = 0 if (preferred_ids and item.get("display_id") in preferred_ids) else 1
        # Ưu tiên 2: thứ tự dòng trong CSV (priority nhỏ hơn tốt hơn)
        pri = int(item.get("priority", 1_000_000))
        # Ưu tiên 3: điểm số (cao hơn tốt hơn)
        score = -float(item.get("score", 0.0))
        return (pref_flag, pri, score)

    merged = sorted(combined.values(), key=sort_key)[:top_k]
    return merged


def rerank_with_llm(user_text: str, keywords: List[str], candidates: List[Dict]) -> Dict:
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


def _select_final_product(
    keywords: List[str], candidates: List[Dict], reranked: Optional[Dict], preferred_ids: Optional[set] = None
) -> Dict:
    # Ưu tiên keyword có nhiều từ hơn (>= 3 từ). Chọn ra đúng 1 sản phẩm cuối cùng.
    def count_words(s: Optional[str]) -> int:
        if not s:
            return 0
        return len(str(s).strip().split())

    id_to_rank = {c["display_id"]: i for i, c in enumerate(candidates)}
    id_to_priority = {c["display_id"]: int(c.get("priority", 1_000_000)) for c in candidates}

    selections = (reranked or {}).get("selections", []) if isinstance(reranked, dict) else []
    if selections:
        # Lọc selections theo keyword >= 3 từ; nếu rỗng, dùng toàn bộ
        eligible = [s for s in selections if count_words(s.get("keyword")) >= 3]
        pool = eligible if eligible else selections
        # Chọn selection có keyword dài nhất (nhiều từ nhất)
        chosen = None
        for s in pool:
            if chosen is None or count_words(s.get("keyword")) > count_words(chosen.get("keyword")):
                chosen = s
        if chosen and chosen.get("display_ids"):
            dids = chosen.get("display_ids", [])
            # Ưu tiên preferred_ids nếu có, sau đó theo thứ hạng trong candidates
            def rank_key(d: str) -> tuple:
                pref = 0 if (preferred_ids and d in preferred_ids) else 1
                pri = id_to_priority.get(d, 1_000_000)
                idx = id_to_rank.get(d, 10**9)
                return (pref, pri, idx)
            best_id = min(dids, key=rank_key) if dids else None
            if best_id is not None:
                detail = next((c for c in candidates if c["display_id"] == best_id), None)
                if detail:
                    return {"keyword": chosen.get("keyword"), "product": detail}
    # Fallback: chọn top-1 từ danh sách tổng hợp
    if candidates:
        # Nếu có preferred_ids, chọn candidate thuộc preferred có priority nhỏ nhất
        if preferred_ids:
            preferred_cands = [c for c in candidates if c.get("display_id") in preferred_ids]
            if preferred_cands:
                best = min(preferred_cands, key=lambda c: int(c.get("priority", 1_000_000)))
                return {"keyword": None, "product": best}
        # Nếu không có preferred, chọn candidate có priority nhỏ nhất
        best = min(candidates, key=lambda c: int(c.get("priority", 1_000_000)))
        return {"keyword": None, "product": best}
    return {"keyword": None, "product": None}


def product_qa_pipeline(csv_path: Optional[str] = None, build_embedding: bool = False, preferred_ids: Optional[set] = None, api_url: Optional[str] = None):
    load_api_key()
    if api_url:
        df = load_products_from_api(api_url)
    elif csv_path:
        df = load_products(csv_path)
    else:
        raise ValueError("Either api_url or csv_path must be provided")
    # Source key để cache theo nguồn (api/csv) nhằm cho phép cập nhật gia tăng
    source_key = api_url or (os.path.abspath(csv_path) if csv_path else "default")
    idx = EmbeddingIndex(df, source_key=source_key) if build_embedding else None

    def ask(user_text: str) -> Dict:
        intent = detect_intent(user_text)
        if intent != "product_query":
            return {"intent": intent, "message": "Đây không phải câu hỏi sản phẩm."}
        keywords = extract_keywords(user_text)
        cands = retrieve_candidates(df, idx, keywords, user_text, preferred_ids=preferred_ids)
        if not cands:
            return {"intent": intent, "keywords": keywords, "results": []}
        reranked = rerank_with_llm(user_text, keywords, cands)
        final_pick = _select_final_product(keywords, cands, reranked, preferred_ids=preferred_ids)
        return {
            "intent": intent,
            "keywords": keywords,
            "candidates": cands,
            "reranked": reranked,
            "final_product": final_pick,
        }

    return ask


