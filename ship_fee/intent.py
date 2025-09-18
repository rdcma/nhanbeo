import os
import re
import hashlib
import time
from typing import Dict, Tuple

from product_qa.pipeline import load_api_key, call_llm_json
from .config import get_llm_model_name, get_intent_strategy


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
    r"th(ế)?\s*thôi.*(chào|bye).*shop",
    r"thôi.*(chào|bye)",
    r"khỏi.*mua",
    r"kh(ô|o)ng.*mua.*n(ư|u)a",
    r"đ(ể|e)\s*l(ầ|a)n\s*kh(á|a)c",
    r"thôi.*shop",
]

SMALLTALK_KEYWORDS = [
    r"^hi$|^hello$|^alo$|^chào$|^chao$",
    r"cảm\s*ơn|thanks|thank\s*you|tks",
    r"ok|oke|oki|được\s*rồi|vâng|dạ|ừ|uhm|ừm",
]

# Complaints about shipping fee without explicit free request
COMPLAINT_KEYWORDS = [
    r"(ôi|oi).*phí.*cao",
    r"đắt.*phí",
    r"ship.*cao",
    r"mua.*\d+k.*m(à|a)\s*ship.*\d+k",
    r"cao\s*h(ơ|o)n.*(đ(ồ|o))",
    r"(ôi|oi)\s*cao\s*th(ế|e)",
    r"cao\s*th(ế|e)",
    r"cao\s*qu(á|a)",
    r"đắt\s*qu(á|a)",
    r"đắt\s*th(ế|e)",
    r"phí\s*cao",
    r"cao.*v(ậ|a)y",
    r"đắt.*v(ậ|a)y",
    r"cao.*nh(ỉ|i)",
    r"đắt.*nh(ỉ|i)",
]


def _regex_detect(text: str) -> Dict:
    t = text.lower().strip()
    intent = "ship_fee" if any(re.search(p, t) for p in SHIP_KEYWORDS) else "other"
    wants_free = bool(re.search(r"miễn\s*ship|miễn\s*phí\s*ship|free\s*ship|freeship|miễn\s*phí\s*vận\s*chuyển|giảm\s*ship|bớt\s*ship|miễn\s*phí\s*vch|free\s*shipping", t))
    cancel_threat = bool(
        any(re.search(p, t) for p in CANCEL_KEYWORDS)
        or re.search(r"không\s*miễn\s*ship\s*(thì)?\s*hủy|không\s*free\s*(thì)?\s*hủy", t)
    )
    about_fee_amount = bool(
        re.search(r"(bao\s*nhiêu|mất\s*bao\s*nhiêu|nhiêu|bao\s*tiền|tiền\s*ship|phí\s*vận\s*chuyển)", t)
        or re.search(r"phí\s*ship", t)
    )
    is_complaint = bool(any(re.search(p, t) for p in COMPLAINT_KEYWORDS))
    # Rule score
    score = 0.0
    if wants_free:
        score = 0.9
    elif about_fee_amount:
        score = 0.85
    if is_complaint:
        score = max(score, 0.85)
    elif intent == "ship_fee":
        score = 0.6
    is_smalltalk = bool(any(re.search(p, t) for p in SMALLTALK_KEYWORDS)) and not wants_free and not about_fee_amount and not cancel_threat and not is_complaint and intent != "ship_fee"
    if is_smalltalk:
        # Treat smalltalk as strong rule to avoid unnecessary LLM calls
        score = max(score, 0.85)
    return {
        "intent_guess": (
            "ask_freeship" if wants_free else (
                "fee_question" if (about_fee_amount or is_complaint) else (
                    "fee_question" if intent == "ship_fee" else ("smalltalk" if is_smalltalk else "other")
                )
            )
        ),
        "rule_score": float(score),
        "wants_free": wants_free,
        "cancel_threat": cancel_threat,
        "about_fee_amount": about_fee_amount,
        "is_smalltalk": is_smalltalk,
        "is_complaint": is_complaint,
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
        "Bạn là bộ phân loại ý định cho câu hỏi về phí ship. Hãy phân loại vào một trong các nhãn sau:\n"
        "- fee_question_general: hỏi phí ship/bao nhiêu, muốn biết con số.\n"
        "- fee_question_complaint: than phiền phí ship cao (ôi cao thế, đắt quá...).\n"
        "- ask_freeship: yêu cầu miễn/giảm phí ship.\n"
        "- cancel_threat: xu hướng không mua nữa vì phí ship (thôi chào shop, hủy...).\n"
        "- smalltalk: chào hỏi/cảm ơn/ok, không liên quan phí.\n"
        "- other: không thuộc các nhóm trên.\n"
        "Chỉ trả lời JSON với schema: {\"intent\":\"fee_question_general|fee_question_complaint|ask_freeship|cancel_threat|smalltalk|other\",\n"
        "\"confidence\":0.0,\n"
        "\"signals\":{\"wants_free\":bool,\"about_fee_amount\":bool,\"cancel_threat\":bool,\"is_complaint\":bool}}\n"
        f"Câu: \"{user_text}\""
    )
    out = call_llm_json(prompt, model)
    if not isinstance(out, dict):
        out = {"intent": "fee_question_general", "confidence": 0.5, "signals": {}}
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
    strategy = get_intent_strategy()
    rule = _regex_detect(user_text)
    use_llm = (strategy == "llm") or (rule["rule_score"] < 0.8)
    intent_final = rule["intent_guess"]
    wants_free = rule["wants_free"]
    cancel_threat = rule["cancel_threat"]
    is_smalltalk = bool(rule.get("is_smalltalk"))
    is_complaint = bool(rule.get("is_complaint"))

    if use_llm:
        try:
            llm = _llm_classify(user_text)
            if isinstance(llm, dict):
                if llm.get("intent") in {"fee_question_general", "fee_question_complaint", "ask_freeship", "cancel_threat", "smalltalk", "other"}:
                    intent_final = llm.get("intent")
                sig = llm.get("signals") or {}
                wants_free = bool(sig.get("wants_free") or wants_free or intent_final == "ask_freeship")
                cancel_threat = bool(sig.get("cancel_threat") or cancel_threat or intent_final == "cancel_threat")
                about_fee_amount = bool(sig.get("about_fee_amount") or rule.get("about_fee_amount") or intent_final == "fee_question_general")
                is_complaint = bool(sig.get("is_complaint") or is_complaint or intent_final == "fee_question_complaint")
                is_smalltalk = bool(intent_final == "smalltalk" or is_smalltalk)
        except Exception:
            pass

    # Prefer rule smalltalk over ambiguous LLM non-free intents
    if is_smalltalk and intent_final not in {"ask_freeship"} and not rule.get("about_fee_amount") and not cancel_threat:
        intent_final = "smalltalk"

    # Map back to service schema including smalltalk
    if intent_final == "smalltalk":
        return {
            "intent": "smalltalk",
            "smalltalk_reply": generate_smalltalk_reply(user_text),
            "wants_free": False,
            "cancel_threat": False,
            "about_fee_amount": False,
            "is_complaint": False,
        }
    # Normalize final intent for service layer
    if intent_final in {"fee_question_general", "fee_question_complaint"}:
        normalized_intent = "fee_question"
    elif intent_final == "ask_freeship":
        normalized_intent = "ask_freeship"
    elif intent_final == "cancel_threat":
        normalized_intent = "fee_question"  # still ship-related handling in service
    else:
        normalized_intent = intent_final

    intent_for_service = "ship_fee" if normalized_intent in {"fee_question", "ask_freeship"} else "other"
    return {
        "intent": intent_for_service,
        "wants_free": bool(wants_free),
        "cancel_threat": bool(cancel_threat),
        "about_fee_amount": bool(rule.get("about_fee_amount")) if not use_llm else bool(locals().get("about_fee_amount", rule.get("about_fee_amount"))),
        "is_complaint": bool(is_complaint),
    }


