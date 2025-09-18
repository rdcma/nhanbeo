import os
import re
import hashlib
import time
from typing import Dict, Tuple

from product_qa.pipeline import load_api_key, call_llm_json
from .config import get_llm_model_name


SHIP_KEYWORDS = [
    r"\bship\b",
    r"shipping",
    r"phí\s*ship",
    r"vận\s*chuyển",
    r"miễn\s*ship",
    r"free\s*ship",
]

CANCEL_KEYWORDS = [
    r"hủy",
    r"ko\s*lấy\s*hàng",
    r"không\s*lấy\s*hàng",
    r"cancel",
]

SMALLTALK_KEYWORDS = [
    r"^hi$|^hello$|^alo$|^chào$|^chao$",
    r"cảm\s*ơn|thanks|thank\s*you|tks",
    r"ok|oke|oki|được\s*rồi|vâng|dạ|ừ|uhm|ừm",
]


def _regex_detect(text: str) -> Dict:
    t = text.lower().strip()
    intent = "ship_fee" if any(re.search(p, t) for p in SHIP_KEYWORDS) else "other"
    wants_free = bool(re.search(r"miễn\s*ship|free\s*ship|freeship|miễn\s*phí\s*vận\s*chuyển|giảm\s*ship|bớt\s*ship", t))
    cancel_threat = bool(
        any(re.search(p, t) for p in CANCEL_KEYWORDS)
        or re.search(r"không\s*miễn\s*ship\s*(thì)?\s*hủy|không\s*free\s*(thì)?\s*hủy", t)
    )
    about_fee_amount = bool(
        re.search(r"(bao\s*nhiêu|mất\s*bao\s*nhiêu|nhiêu|bao\s*tiền|tiền\s*ship|phí\s*vận\s*chuyển)", t)
        or re.search(r"phí\s*ship", t)
    )
    # Rule score
    score = 0.0
    if wants_free:
        score = 0.9
    elif about_fee_amount:
        score = 0.85
    elif intent == "ship_fee":
        score = 0.6
    is_smalltalk = bool(any(re.search(p, t) for p in SMALLTALK_KEYWORDS)) and not wants_free and not about_fee_amount and intent != "ship_fee"
    if is_smalltalk:
        # Treat smalltalk as strong rule to avoid unnecessary LLM calls
        score = max(score, 0.85)
    return {
        "intent_guess": (
            "ask_freeship" if wants_free else (
                "fee_question" if about_fee_amount else (
                    "fee_question" if intent == "ship_fee" else ("smalltalk" if is_smalltalk else "other")
                )
            )
        ),
        "rule_score": float(score),
        "wants_free": wants_free,
        "cancel_threat": cancel_threat,
        "about_fee_amount": about_fee_amount,
        "is_smalltalk": is_smalltalk,
    }


_LLM_CACHE: Dict[str, Tuple[float, Dict]] = {}
_LLM_TTL_SECONDS = 300


def _llm_classify(user_text: str) -> Dict:
    # Cache by text hash for a short time to limit cost
    key = hashlib.md5(user_text.encode("utf-8")).hexdigest()
    now = time.time()
    hit = _LLM_CACHE.get(key)
    if hit and now - hit[0] < _LLM_TTL_SECONDS:
        return hit[1]
    load_api_key()
    model = get_llm_model_name()
    prompt = (
        "Bạn là bộ phân loại ý định về shipping. Hãy phân loại câu vào 3 nhãn: \n"
        "- fee_question: hỏi phí ship (bao nhiêu, mất bao nhiêu, tính như nào)\n"
        "- ask_freeship: yêu cầu miễn/giảm phí ship (freeship).\n"
        "- smalltalk: chào hỏi/xã giao/đồng ý/cảm ơn/ok... không liên quan phí.\n"
        "Chỉ trả lời JSON: {\"intent\":\"fee_question|ask_freeship|smalltalk\",\"confidence\":0.0,\"signals\":{\"wants_free\":bool,\"about_fee_amount\":bool,\"cancel_threat\":bool}}\n"
        f"Câu: \"{user_text}\""
    )
    out = call_llm_json(prompt, model)
    if not isinstance(out, dict):
        out = {"intent": "fee_question", "confidence": 0.5, "signals": {}}
    _LLM_CACHE[key] = (now, out)
    return out


def generate_smalltalk_reply(user_text: str) -> str:
    """Use LLM to produce a short, friendly smalltalk reply in Vietnamese."""
    try:
        load_api_key()
        model = get_llm_model_name()
        prompt = (
            "Bạn là trợ lý CSKH thân thiện. Người dùng vừa nói câu smalltalk. "
            "Hãy đáp ngắn gọn (tối đa 1-2 câu), lịch sự, tiếng Việt, không hỏi thêm.\n"
            f"Người dùng: \"{user_text}\"\n"
            "Chỉ trả về câu trả lời, không giải thích."
        )
        out = call_llm_json(prompt, model)  # may return string-able dict; fallback below
        if isinstance(out, dict):
            # try common fields
            return str(out.get("text") or out.get("reply") or "Dạ vâng ạ!")
    except Exception:
        pass
    # Fallback generic
    return "Dạ vâng ạ! Em cảm ơn mình ạ."


def classify_intent(user_text: str) -> Dict:
    """Hybrid classifier returning fields used by service.

    Output compatibility with previous version:
      {"intent": "ship_fee"|"other", "wants_free": bool, "cancel_threat": bool}
    where intent="ship_fee" means the text is about shipping.
    """
    rule = _regex_detect(user_text)
    use_llm = rule["rule_score"] < 0.8
    intent_final = rule["intent_guess"]
    wants_free = rule["wants_free"]
    cancel_threat = rule["cancel_threat"]
    is_smalltalk = bool(rule.get("is_smalltalk"))

    if use_llm:
        try:
            llm = _llm_classify(user_text)
            if isinstance(llm, dict):
                if llm.get("intent") in {"fee_question", "ask_freeship", "smalltalk"}:
                    intent_final = llm.get("intent")
                sig = llm.get("signals") or {}
                wants_free = bool(sig.get("wants_free") or wants_free or intent_final == "ask_freeship")
                cancel_threat = bool(sig.get("cancel_threat") or cancel_threat)
                about_fee_amount = bool(sig.get("about_fee_amount") or rule.get("about_fee_amount"))
                is_smalltalk = bool(intent_final == "smalltalk" or is_smalltalk)
        except Exception:
            pass

    # Prefer rule smalltalk over ambiguous LLM non-free intents
    if is_smalltalk and intent_final not in {"ask_freeship"} and not rule.get("about_fee_amount"):
        intent_final = "smalltalk"

    # Map back to service schema including smalltalk
    if intent_final == "smalltalk":
        return {
            "intent": "smalltalk",
            "smalltalk_reply": generate_smalltalk_reply(user_text),
            "wants_free": False,
            "cancel_threat": False,
            "about_fee_amount": False,
        }
    intent_for_service = "ship_fee" if intent_final in {"fee_question", "ask_freeship"} else "other"
    return {
        "intent": intent_for_service,
        "wants_free": bool(wants_free),
        "cancel_threat": bool(cancel_threat),
        "about_fee_amount": bool(rule.get("about_fee_amount")) if not use_llm else bool(locals().get("about_fee_amount", rule.get("about_fee_amount"))),
    }


