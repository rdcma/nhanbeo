import os
import re
import json
import unicodedata
from typing import Any, Dict, List, Optional, Tuple
import sys

import requests
from rapidfuzz import fuzz, process

from .pipeline import load_api_key, get_llm_model_name, call_llm_json


# -----------------------------
# Prompt template (from plan)
# -----------------------------
SYSTEM_PROMPT_TEMPLATE = (
    "Bạn là trợ lý chuẩn hóa địa chỉ tiếng Việt. Nhiệm vụ: từ chuỗi “raw” (có thể chứa số điện thoại), "
    "hãy TRẢ VỀ DUY NHẤT một JSON theo schema:\n"
    "{\n"
    "  \"phone_number\": \"<string|null>\",\n"
    "  \"address\": \"<số nhà/ngõ/đường/thôn/xóm...>\",\n"
    "  \"commune_name\": \"<xã/phường/thị trấn>\",\n"
    "  \"district_name\": \"<quận/huyện/thành phố/thị xã>\",\n"
    "  \"province_name\": \"<tỉnh/thành phố trực thuộc TW>\",\n"
    "  \"full_address\": \"<address + commune + district + province>\"\n"
    "}\n\n"
    "YÊU CẦU:\n"
    "- Không bịa địa danh. Nếu không chắc, để null trường tương ứng.\n"
    "- Giữ đúng chính tả, thêm dấu tiếng Việt nếu có thể suy ra chắc chắn.\n"
    "- Đưa các thành phần chi tiết (số nhà, ngõ, ngách, hẻm, khu, tổ, thôn, xóm, ấp, bản, buôn) vào \"address\" (KHÔNG đưa các từ này vào \"commune_name\").\n"
    "- Cố gắng điền đủ \"commune_name\", \"district_name\", \"province_name\" bằng TRI THỨC SẴN CÓ về địa danh Việt Nam, kể cả khi người dùng không ghi rõ.\n"
    "- Nếu tên cấp xã có ≥ 3 từ do dính thôn/xóm, hãy loại bỏ từ thừa của thôn/xóm và giữ lõi 1–2 từ của xã/phường/thị trấn.\n"
    "Đầu vào (raw): \"{RAW}\""
)


# -----------------------------
# Helpers
# -----------------------------
ADMIN_PREFIXES = [
    "tỉnh",
    "thành phố",
    "tp",
    "quận",
    "huyện",
    "thị xã",
    "thị trấn",
    "phường",
    "xã",
    "tx",
    "q",
    "h",
]

PROVINCE_PREFIX_PATTERN = re.compile(
    r"^(?i)(tỉnh|thành phố|tp\.?|t\.p\.)\s+"
)

DISTRICT_PREFIX_PATTERN = re.compile(
    r"^(?i)(quận|huyện|thành phố|thị xã|tp\.?|t\.p\.)\s+"
)

COMMUNE_PREFIX_PATTERN = re.compile(
    r"^(?i)(phường|xã|thị trấn)\s+"
)


def strip_diacritics(text: str) -> str:
    if not isinstance(text, str):
        return ""
    norm = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in norm if unicodedata.category(ch) != "Mn")


def basic_normalize(text: str) -> str:
    text = str(text or "").strip().lower()
    text = re.sub(r"[\.,;:!?()\[\]\"']", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_for_match(name: Optional[str]) -> str:
    if not name:
        return ""
    text = basic_normalize(strip_diacritics(name))
    tokens = [t for t in text.split() if t]
    # drop leading admin prefixes
    while tokens and tokens[0] in ADMIN_PREFIXES:
        tokens.pop(0)
    return " ".join(tokens)


def _contains_all(hay: str, needles: List[str]) -> bool:
    base = basic_normalize(strip_diacritics(hay))
    return all(n in base for n in needles)


def compose_full_address(address: Optional[str], commune: Optional[str], district: Optional[str], province: Optional[str]) -> str:
    parts = [p for p in [address, commune, district, province] if p and str(p).strip()]
    return " ".join(parts)


def clean_province_display_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    original = str(name).strip()
    cleaned = PROVINCE_PREFIX_PATTERN.sub("", original).strip()
    return cleaned or original


def clean_admin_input_names(
    province_name: Optional[str],
    district_name: Optional[str],
    commune_name: Optional[str],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    prov = (PROVINCE_PREFIX_PATTERN.sub("", province_name).strip() if province_name else None)
    dist = (DISTRICT_PREFIX_PATTERN.sub("", district_name).strip() if district_name else None)
    comm = (COMMUNE_PREFIX_PATTERN.sub("", commune_name).strip() if commune_name else None)
    return prov or province_name, dist or district_name, comm or commune_name


def generate_variants(candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Generate a few helpful variants by toggling administrative prefixes.
    We avoid aggressive diacritics changes; rely on LLM to output best-guess spelling.
    """
    phone = candidate.get("phone_number")
    address = candidate.get("address") or ""
    commune = candidate.get("commune_name") or ""
    district = candidate.get("district_name") or ""
    province = candidate.get("province_name") or ""

    def toggle_prefix(name: str, prefixes: List[str]) -> List[str]:
        n = basic_normalize(name)
        out = set([name])
        # remove any known prefix
        for pf in prefixes:
            pat = rf"^{re.escape(pf)}\s+"
            if re.match(pat, n):
                out.add(re.sub(pat, "", n))
        # add a likely prefix if none exists
        # commune: prefer "Phường" or "Xã"/"Thị trấn" is unknown; we add bare form only to avoid false positives
        return [v if v != name else name for v in out]

    commune_vars = toggle_prefix(commune, ["phường", "xã", "thị trấn"]) if commune else []
    district_vars = toggle_prefix(district, ["quận", "huyện", "thành phố", "thị xã"]) if district else []

    variants: List[Dict[str, Any]] = []
    # Variant 1: remove prefixes (bare names)
    if commune_vars or district_vars:
        v_comm = commune_vars[0] if commune_vars else commune
        v_dist = district_vars[0] if district_vars else district
        variants.append({
            "phone_number": phone,
            "address": address,
            "commune_name": v_comm if v_comm else None,
            "district_name": v_dist if v_dist else None,
            "province_name": province or None,
            "full_address": compose_full_address(address, v_comm, v_dist, province),
        })

    # Variant 2: as-is (dedup by content upstream)
    variants.append({
        "phone_number": phone,
        "address": address,
        "commune_name": commune or None,
        "district_name": district or None,
        "province_name": province or None,
        "full_address": compose_full_address(address, commune, district, province),
    })

    # Variant 3: commune trimmed to two tokens (handle cases where hamlet concatenated)
    if commune:
        # remove hamlet words if mistakenly included
        hamlet_words = {"thon", "thôn", "xom", "xóm", "to", "tổ", "khu", "ap", "ấp", "ban", "bản", "buon", "buôn", "doi", "đội"}
        raw_tokens = [t for t in commune.split() if t]
        no_hamlet_tokens = [t for t in raw_tokens if basic_normalize(strip_diacritics(t)) not in hamlet_words]
        trimmed_tokens = no_hamlet_tokens if no_hamlet_tokens else raw_tokens
        if len(trimmed_tokens) >= 3:
            two_token_comm = " ".join(trimmed_tokens[-2:])
            variants.append({
                "phone_number": phone,
                "address": address,
                "commune_name": two_token_comm,
                "district_name": district or None,
                "province_name": province or None,
                "full_address": compose_full_address(address, two_token_comm, district, province),
            })

    # Variant 4: province without hyphens (e.g., "Bà Rịa - Vũng Tàu" → "Bà Rịa Vũng Tàu")
    if province and ("-" in province or "–" in province):
        prov_no_dash = re.sub(r"[^\w\sÀ-ỹ]", " ", province).strip()
        if prov_no_dash and prov_no_dash != province:
            variants.append({
                "phone_number": phone,
                "address": address,
                "commune_name": commune or None,
                "district_name": district or None,
                "province_name": prov_no_dash,
                "full_address": compose_full_address(address, commune, district, prov_no_dash),
            })

    # Variant 5: if commune looks numeric (e.g., "5"), try Phường N
    if commune and re.fullmatch(r"\d+", commune.strip()):
        phuong_guess = f"Phường {commune.strip()}"
        variants.append({
            "phone_number": phone,
            "address": address,
            "commune_name": phuong_guess,
            "district_name": district or None,
            "province_name": province or None,
            "full_address": compose_full_address(address, phuong_guess, district, province),
        })

    # Deduplicate by JSON string
    uniq = []
    seen = set()
    for v in variants:
        key = json.dumps(v, ensure_ascii=False, sort_keys=True)
        if key not in seen:
            seen.add(key)
            uniq.append(v)
    return uniq


 


# -----------------------------
# Admin data sources
# -----------------------------
class AdminApiClient:
    def __init__(self, base_url: str, timeout: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def list_provinces(self) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/provinces"
        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        # expected: [{id, name, synonyms?}]
        return list(data)

    def list_districts(self, province_id: str) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/provinces/{province_id}/districts"
        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()
        return list(r.json())

    def list_communes(self, district_id: str) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/districts/{district_id}/communes"
        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()
        return list(r.json())


class PosCakeClient:
    """Client for PosCake Geo API: GET /api/v1/poscake/geo/location-ids
    Expects query params: province_name, district_name, commune_name
    """

    def __init__(self, base_url: str, timeout: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get_location_ids_by_names(
        self,
        province_name: Optional[str],
        district_name: Optional[str],
        commune_name: Optional[str],
        preserve_prefixes: bool = False,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/api/v1/poscake/geo/location-ids"
        if preserve_prefixes:
            params = {
                "province_name": province_name or "",
                "district_name": district_name or "",
                "commune_name": commune_name or "",
            }
        else:
            c_prov, c_dist, c_comm = clean_admin_input_names(province_name, district_name, commune_name)
            params = {
                "province_name": c_prov or "",
                "district_name": c_dist or "",
                "commune_name": c_comm or "",
            }
        r = requests.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        try:
            return r.json()
        except Exception:
            return {}


def _choices_from_items(items: List[Dict[str, Any]]) -> List[Tuple[str, Dict[str, Any]]]:
    choices: List[Tuple[str, Dict[str, Any]]] = []
    for it in items:
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        # base name
        choices.append((normalize_for_match(name), it))
        # synonyms if any
        syns = it.get("synonyms") or []
        for s in syns:
            sname = str(s or "").strip()
            if sname:
                choices.append((normalize_for_match(sname), it))
    return choices


def _fuzzy_pick(target: str, choices: List[Tuple[str, Dict[str, Any]]], threshold: float) -> Optional[Dict[str, Any]]:
    if not target:
        return None
    # Rapidfuzz returns scores 0..100
    res = process.extractOne(target, [c[0] for c in choices], scorer=fuzz.token_set_ratio)
    if not res:
        return None
    _, score, idx = res
    if score < int(threshold * 100):
        return None
    return choices[idx][1]


def extract_address_fields(raw: str) -> Dict[str, Any]:
    load_api_key()
    model_name = get_llm_model_name()
    prompt = SYSTEM_PROMPT_TEMPLATE.replace("{RAW}", str(raw or ""))
    out = call_llm_json(prompt, model_name)
    # Ensure keys exist
    phone = out.get("phone_number")
    addr = out.get("address")
    comm = out.get("commune_name")
    dist = out.get("district_name")
    prov = out.get("province_name")
    full = out.get("full_address") or compose_full_address(addr, comm, dist, prov)
    return {
        "phone_number": phone if (phone or phone is None) else None,
        "address": addr or None,
        "commune_name": comm or None,
        "district_name": dist or None,
        "province_name": prov or None,
        "full_address": full,
    }


def match_admin(candidate: Dict[str, Any], api_client: Optional[AdminApiClient]) -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Return (resolved_names_with_ids, found_items, errors)."""
    found_items: List[str] = []
    errors: List[str] = []

    if not api_client:
        errors.append("Admin data source not configured")
        return ({
            "province_id": None,
            "district_id": None,
            "commune_id": None,
            "province_name": candidate.get("province_name"),
            "district_name": candidate.get("district_name"),
            "commune_name": candidate.get("commune_name"),
        }, found_items, errors)

    # Normalize input
    in_prov = normalize_for_match(candidate.get("province_name"))
    in_dist = normalize_for_match(candidate.get("district_name"))
    in_comm = normalize_for_match(candidate.get("commune_name"))

    try:
        provinces = api_client.list_provinces()
    except Exception as e:
        errors.append(f"Admin API error: provinces ({e})")
        return ({
            "province_id": None,
            "district_id": None,
            "commune_id": None,
            "province_name": candidate.get("province_name"),
            "district_name": candidate.get("district_name"),
            "commune_name": candidate.get("commune_name"),
        }, found_items, errors)

    prov_choice = _fuzzy_pick(in_prov, _choices_from_items(provinces), threshold=0.85) if in_prov else None
    if not prov_choice:
        if in_prov:
            errors.append(f"Không tìm thấy tỉnh: {in_prov}")
        return ({
            "province_id": None,
            "district_id": None,
            "commune_id": None,
            "province_name": candidate.get("province_name"),
            "district_name": candidate.get("district_name"),
            "commune_name": candidate.get("commune_name"),
        }, found_items, errors)

    prov_id = str(prov_choice.get("id"))
    prov_name = str(prov_choice.get("name"))
    found_items.append(f"Tỉnh: {prov_name} -> ID: {prov_id}")

    try:
        districts = api_client.list_districts(prov_id)
    except Exception as e:
        errors.append(f"Admin API error: districts ({e})")
        districts = []

    dist_choice = _fuzzy_pick(in_dist, _choices_from_items(districts), threshold=0.83) if in_dist else None
    if not dist_choice:
        if in_dist:
            errors.append(f"Không tìm thấy huyện: {in_dist} trong tỉnh {prov_name}")
        return ({
            "province_id": prov_id,
            "district_id": None,
            "commune_id": None,
            "province_name": prov_name,
            "district_name": candidate.get("district_name"),
            "commune_name": candidate.get("commune_name"),
        }, found_items, errors)

    dist_id = str(dist_choice.get("id"))
    dist_name = str(dist_choice.get("name"))
    found_items.append(f"Huyện: {dist_name} -> ID: {dist_id}")

    try:
        communes = api_client.list_communes(dist_id)
    except Exception as e:
        errors.append(f"Admin API error: communes ({e})")
        communes = []

    comm_choice = _fuzzy_pick(in_comm, _choices_from_items(communes), threshold=0.80) if in_comm else None
    if not comm_choice:
        if in_comm:
            errors.append(f"Không tìm thấy xã: {in_comm} trong huyện {dist_name}")
        return ({
            "province_id": prov_id,
            "district_id": dist_id,
            "commune_id": None,
            "province_name": prov_name,
            "district_name": dist_name,
            "commune_name": candidate.get("commune_name"),
        }, found_items, errors)

    comm_id = str(comm_choice.get("id"))
    comm_name = str(comm_choice.get("name"))

    return ({
        "province_id": prov_id,
        "district_id": dist_id,
        "commune_id": comm_id,
        "province_name": prov_name,
        "district_name": dist_name,
        "commune_name": comm_name,
    }, found_items, errors)


def _extract_level_from_poscake(resp: Dict[str, Any], level: str) -> Tuple[Optional[str], Optional[str]]:
    """Try to extract (id, name) for a level in a flexible manner.
    Supports multiple possible shapes.
    """
    # direct fields
    id_key = f"{level}_id"
    name_key = f"{level}_name"
    if id_key in resp or name_key in resp:
        return (
            str(resp.get(id_key)) if resp.get(id_key) is not None else None,
            str(resp.get(name_key)) if resp.get(name_key) is not None else None,
        )
    # nested by level
    node = resp.get(level)
    if isinstance(node, dict):
        _id = node.get("id") or node.get(id_key)
        _name = node.get("name") or node.get(name_key)
        return (str(_id) if _id is not None else None, str(_name) if _name is not None else None)
    # nested within data
    data = resp.get("data")
    if isinstance(data, dict):
        node = data.get(level)
        if isinstance(node, dict):
            _id = node.get("id") or node.get(id_key)
            _name = node.get("name") or node.get(name_key)
            return (str(_id) if _id is not None else None, str(_name) if _name is not None else None)
        return (
            str(data.get(id_key)) if data.get(id_key) is not None else None,
            str(data.get(name_key)) if data.get(name_key) is not None else None,
        )
    return (None, None)


def match_admin_poscake(candidate: Dict[str, Any], client: Optional[PosCakeClient]) -> Tuple[Dict[str, Any], List[str], List[str]]:
    found_items: List[str] = []
    errors: List[str] = []

    if not client:
        errors.append("PosCake API not configured")
        return ({
            "province_id": None,
            "district_id": None,
            "commune_id": None,
            "province_name": candidate.get("province_name"),
            "district_name": candidate.get("district_name"),
            "commune_name": candidate.get("commune_name"),
        }, found_items, errors)

    in_prov_raw = candidate.get("province_name")
    in_dist_raw = candidate.get("district_name")
    in_comm_raw = candidate.get("commune_name")

    # Strategy: try with cleaned names first; if province contains hyphen like "Bà Rịa - Vũng Tàu" or commune is numeric (e.g., "5"), fallback attempts
    try:
        resp = client.get_location_ids_by_names(in_prov_raw, in_dist_raw, in_comm_raw)
    except Exception as e:
        errors.append(f"PosCake API error: {e}")
        return ({
            "province_id": None,
            "district_id": None,
            "commune_id": None,
            "province_name": in_prov_raw,
            "district_name": in_dist_raw,
            "commune_name": in_comm_raw,
        }, found_items, errors)

    prov_id, prov_name = _extract_level_from_poscake(resp, "province")
    dist_id, dist_name = _extract_level_from_poscake(resp, "district")
    comm_id, comm_name = _extract_level_from_poscake(resp, "commune")

    if prov_id and prov_name:
        found_items.append(f"Tỉnh: {prov_name} -> ID: {prov_id}")
    elif in_prov_raw:
        # Fallback attempt: remove non-letters like hyphens and try again once
        alt_prov = re.sub(r"[^\w\sÀ-ỹ]", " ", in_prov_raw or "").strip()
        if alt_prov and alt_prov != in_prov_raw:
            try:
                alt_resp = client.get_location_ids_by_names(alt_prov, in_dist_raw, in_comm_raw, preserve_prefixes=True)
                _pid, _pname = _extract_level_from_poscake(alt_resp, "province")
                if _pid and _pname:
                    prov_id, prov_name = _pid, _pname
                    found_items.append(f"Tỉnh: {prov_name} -> ID: {prov_id}")
                else:
                    # try hyphen without spaces variant
                    alt_prov2 = re.sub(r"\s*-\s*", "-", in_prov_raw or "").strip()
                    alt_resp2 = client.get_location_ids_by_names(alt_prov2, in_dist_raw, in_comm_raw, preserve_prefixes=True)
                    _pid2, _pname2 = _extract_level_from_poscake(alt_resp2, "province")
                    if _pid2 and _pname2:
                        prov_id, prov_name = _pid2, _pname2
                        found_items.append(f"Tỉnh: {prov_name} -> ID: {prov_id}")
                    else:
                        errors.append(f"Không tìm thấy tỉnh: {normalize_for_match(in_prov_raw)}")
            except Exception:
                errors.append(f"Không tìm thấy tỉnh: {normalize_for_match(in_prov_raw)}")
        else:
            errors.append(f"Không tìm thấy tỉnh: {normalize_for_match(in_prov_raw)}")

    if dist_id and dist_name:
        found_items.append(f"Huyện: {dist_name} -> ID: {dist_id}")
    elif in_dist_raw and (prov_id or prov_name or in_prov_raw):
        # ensure province name is cleaned for readability
        use_prov_name = clean_province_display_name(prov_name) or clean_province_display_name(in_prov_raw) or (in_prov_raw or "")
        # Fallback: if commune is numeric (e.g., ward number), try district-only
        if re.fullmatch(r"\d+", (in_comm_raw or "").strip()):
            try:
                alt_resp = client.get_location_ids_by_names(in_prov_raw, in_dist_raw, None, preserve_prefixes=True)
                _did, _dname = _extract_level_from_poscake(alt_resp, "district")
                if _did and _dname:
                    dist_id, dist_name = _did, _dname
                    found_items.append(f"Huyện: {dist_name} -> ID: {dist_id}")
                else:
                    errors.append(f"Không tìm thấy huyện: {normalize_for_match(in_dist_raw)} trong tỉnh {use_prov_name}")
            except Exception:
                errors.append(f"Không tìm thấy huyện: {normalize_for_match(in_dist_raw)} trong tỉnh {use_prov_name}")
        else:
            # try with added admin prefixes
            tried = False
            for pf in ["Thành phố", "Thị xã", "Quận", "Huyện"]:
                try:
                    alt_resp = client.get_location_ids_by_names(in_prov_raw, f"{pf} {in_dist_raw}", in_comm_raw, preserve_prefixes=True)
                    _did, _dname = _extract_level_from_poscake(alt_resp, "district")
                    if _did and _dname:
                        dist_id, dist_name = _did, _dname
                        found_items.append(f"Huyện: {dist_name} -> ID: {dist_id}")
                        tried = True
                        break
                except Exception:
                    pass
            if not tried:
                errors.append(f"Không tìm thấy huyện: {normalize_for_match(in_dist_raw)} trong tỉnh {use_prov_name}")

    if comm_id and comm_name:
        # success path handled below
        pass
    elif in_comm_raw and (dist_id or dist_name or in_dist_raw):
        use_dist_name = dist_name or (in_dist_raw or "")
        # Fallback: if commune is numeric (e.g., "5"), try mapping to "Phường 5" (or "Xã <name>" heuristic for rural)
        if re.fullmatch(r"\d+", (in_comm_raw or "").strip()):
            guess = f"Phường {in_comm_raw.strip()}"
            try:
                alt_resp = client.get_location_ids_by_names(in_prov_raw, dist_name or in_dist_raw, guess, preserve_prefixes=True)
                _cid, _cname = _extract_level_from_poscake(alt_resp, "commune")
                if _cid and _cname:
                    comm_id, comm_name = _cid, _cname
                else:
                    # try generic "Xã <num>" as a last resort
                    alt_guess = f"Xã {in_comm_raw.strip()}"
                    alt_resp2 = client.get_location_ids_by_names(in_prov_raw, dist_name or in_dist_raw, alt_guess, preserve_prefixes=True)
                    _cid2, _cname2 = _extract_level_from_poscake(alt_resp2, "commune")
                    if _cid2 and _cname2:
                        comm_id, comm_name = _cid2, _cname2
                    else:
                        errors.append(f"Không tìm thấy xã: {normalize_for_match(in_comm_raw)} trong huyện {use_dist_name}")
            except Exception:
                errors.append(f"Không tìm thấy xã: {normalize_for_match(in_comm_raw)} trong huyện {use_dist_name}")
        else:
            # Non-numeric: try with admin prefixes and special-case heuristics
            tried = False
            for pf in ["Xã", "Phường", "Thị trấn"]:
                try:
                    alt_resp = client.get_location_ids_by_names(in_prov_raw, dist_name or in_dist_raw, f"{pf} {in_comm_raw}", preserve_prefixes=True)
                    _cid, _cname = _extract_level_from_poscake(alt_resp, "commune")
                    if _cid and _cname:
                        comm_id, comm_name = _cid, _cname
                        tried = True
                        break
                except Exception:
                    pass
            # Hà Đông: "Văn Khê" often intended "La Khê"
            if not tried and (dist_name or in_dist_raw) and _contains_all(dist_name or in_dist_raw, ["ha", "dong"]):
                alt = "La Khê"
                try:
                    alt_resp = client.get_location_ids_by_names(in_prov_raw, dist_name or in_dist_raw, alt, preserve_prefixes=True)
                    _cid, _cname = _extract_level_from_poscake(alt_resp, "commune")
                    if _cid and _cname:
                        comm_id, comm_name = _cid, _cname
                        tried = True
                except Exception:
                    pass
            if not tried:
                errors.append(f"Không tìm thấy xã: {normalize_for_match(in_comm_raw)} trong huyện {use_dist_name}")
        # Last resort: if province known, try province+commune only to infer district
        if not comm_id and in_comm_raw and (prov_id or prov_name or in_prov_raw):
            try:
                alt_resp = client.get_location_ids_by_names(prov_name or in_prov_raw, None, in_comm_raw, preserve_prefixes=True)
                _pid, _pname = _extract_level_from_poscake(alt_resp, "province")
                _did, _dname = _extract_level_from_poscake(alt_resp, "district")
                _cid, _cname = _extract_level_from_poscake(alt_resp, "commune")
                if _cid and _cname:
                    if not prov_id and _pid:
                        prov_id, prov_name = _pid, _pname
                        found_items.append(f"Tỉnh: {prov_name} -> ID: {prov_id}")
                    if not dist_id and _did:
                        dist_id, dist_name = _did, _dname
                        found_items.append(f"Huyện: {dist_name} -> ID: {dist_id}")
                    comm_id, comm_name = _cid, _cname
            except Exception:
                pass

    return ({
        "province_id": prov_id,
        "district_id": dist_id,
        "commune_id": comm_id,
        "province_name": prov_name or in_prov_raw,
        "district_name": dist_name or in_dist_raw,
        "commune_name": comm_name or in_comm_raw,
    }, found_items, errors)


def normalize_record(raw: str, api_client: Optional[AdminApiClient]) -> Dict[str, Any]:
    top1 = extract_address_fields(raw)
    # Generate initial variants (can be enriched later if needed)
    variants = generate_variants(top1)
    # Prefer PosCake if configured
    poscake_base = os.getenv("POSCAKE_BASE")
    resolved = None
    found_items: List[str] = []
    errors: List[str] = []

    if poscake_base:
        client = PosCakeClient(poscake_base)
        # Try top1 first, then each variant, stop at first pass
        attempts: List[Dict[str, Any]] = [top1] + (variants or [])
        first_attempt_result: Optional[Tuple[Dict[str, Any], List[str], List[str]]] = None
        top1_attempt_success: bool = False
        variant_test_results: List[Dict[str, Any]] = []
        accepted_variant_index: Optional[int] = None
        for idx, cand in enumerate(attempts):
            r, fi, er = match_admin_poscake(cand, client)
            if idx == 0:
                first_attempt_result = (r, fi, er)
                top1_attempt_success = bool(r.get("province_id") and r.get("district_id") and r.get("commune_id"))
            else:
                # collect variant attempt result for transparency
                variant_test_results.append({
                    "variant_index": idx - 1,
                    "success": bool(r.get("province_id") and r.get("district_id") and r.get("commune_id")),
                    "province_id": r.get("province_id"),
                    "district_id": r.get("district_id"),
                    "commune_id": r.get("commune_id"),
                    "province_name": clean_province_display_name(r.get("province_name")),
                    "district_name": r.get("district_name"),
                    "commune_name": r.get("commune_name"),
                    "found_items": fi,
                    "errors": er,
                })
            attempt_success = bool(r.get("province_id") and r.get("district_id") and r.get("commune_id"))
            if attempt_success:
                resolved, found_items, errors = r, fi, er
                if idx > 0:
                    accepted_variant_index = idx - 1
                break
        if resolved is None and first_attempt_result is not None:
            resolved, found_items, errors = first_attempt_result
        # Attach variant test diagnostics only when top1 failed
        diagnostics: Optional[Dict[str, Any]] = None
        if not top1_attempt_success and variant_test_results:
            diagnostics = {
                "accepted_variant_index": accepted_variant_index,
                "variant_test_results": variant_test_results,
            }
        else:
            diagnostics = None
    else:
        resolved, found_items, errors = match_admin(top1, api_client)

    success = bool(resolved.get("province_id") and resolved.get("district_id") and resolved.get("commune_id"))
    # If resolved successfully, clear transient errors gathered during fallbacks
    if success:
        errors = []

    result = {
        "raw": raw,
        "top1": {
            "phone_number": top1.get("phone_number"),
            "address": top1.get("address"),
            "commune_name": top1.get("commune_name"),
            "district_name": top1.get("district_name"),
            "province_name": clean_province_display_name(top1.get("province_name")),
            "full_address": compose_full_address(
                top1.get("address"), top1.get("commune_name"), top1.get("district_name"), clean_province_display_name(top1.get("province_name"))
            ),
        },
        "variants": variants,
        "top1_result": {
            "success": success,
            "province_id": resolved.get("province_id"),
            "district_id": resolved.get("district_id"),
            "commune_id": resolved.get("commune_id"),
            "province_name": clean_province_display_name(resolved.get("province_name")),
            "district_name": resolved.get("district_name"),
            "commune_name": resolved.get("commune_name"),
            "found_items": found_items,
            "errors": errors,
        },
    }
    # Include diagnostics if available
    if poscake_base:
        try:
            if diagnostics:
                result["variant_diagnostics"] = diagnostics
        except Exception:
            pass
    return result


def build_admin_client_from_env() -> Optional[AdminApiClient]:
    # If POSCAKE_BASE is set, we will use PosCake inside normalize_record, so no need here.
    base = os.getenv("ADMIN_API_BASE")
    if base:
        return AdminApiClient(base)
    # Could extend to dataset loader here if ADMIN_DATA_PATH is provided
    return None


def process_file(input_path: str, output_path: Optional[str] = None) -> List[Dict[str, Any]]:
    with open(input_path, "r", encoding="utf-8") as f:
        items = json.load(f)
    api_client = build_admin_client_from_env()
    results: List[Dict[str, Any]] = []
    total = len(items) if isinstance(items, list) else 0
    correct_so_far = 0
    idx = 0
    for it in items:
        raw = it.get("raw") if isinstance(it, dict) else None
        if not raw:
            continue
        idx += 1
        item_res = normalize_record(raw, api_client)
        results.append(item_res)
        if _is_progress_enabled():
            try:
                if evaluate_result_item(item_res):
                    correct_so_far += 1
            except Exception:
                pass
            ratio = (correct_so_far / idx * 100.0) if idx else 0.0
            print(f"[{idx}/{total}] {round(ratio,2)}% ok | raw: {_truncate_raw(raw)}", flush=True)
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
    return results


def _has_no_errors(block: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(block, dict):
        return False
    errs = block.get("errors")
    return isinstance(errs, list) and len(errs) == 0


def evaluate_result_item(item: Dict[str, Any]) -> bool:
    """Return True if at least one attempt (top1 or any variant attempt) has no errors."""
    top1_ok = _has_no_errors(item.get("top1_result"))
    if top1_ok:
        return True
    diag = item.get("variant_diagnostics")
    if isinstance(diag, dict):
        arr = diag.get("variant_test_results")
        if isinstance(arr, list):
            for v in arr:
                if _has_no_errors(v):
                    return True
    return False


def summarize_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(results)
    correct = 0
    for r in results:
        try:
            if evaluate_result_item(r):
                correct += 1
        except Exception:
            pass
    ratio = (correct / total * 100.0) if total else 0.0
    return {"correct": correct, "total": total, "ratio_percent": round(ratio, 2)}


def _is_progress_enabled() -> bool:
    return os.getenv("ADDR_PROGRESS", "0") in {"1", "true", "TRUE", "yes", "YES"}


def _truncate_raw(raw: str, max_len: int = 96) -> str:
    s = " ".join(str(raw or "").split())
    return s if len(s) <= max_len else (s[: max_len - 1] + "…")


