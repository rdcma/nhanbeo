import json
from pathlib import Path
from typing import Any, Dict, Optional


ACTIVE_STATUSES = {0, 1}
SUCCESS_STATUS = 3


def load_orders(path: str) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Orders JSON not found: {path}")
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def pick_latest_active_order(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    orders = data.get("orders") or []
    if not isinstance(orders, list) or not orders:
        return None
    for order in orders:
        try:
            order_info = order.get("order_info") or {}
            status = int(order_info.get("status")) if order_info.get("status") is not None else None
            items = order.get("items") or []
            if status in ACTIVE_STATUSES and isinstance(items, list) and len(items) > 0:
                return order
        except Exception:
            continue
    return None


def extract_shipping_fee(order: Dict[str, Any]) -> Optional[int]:
    if not order:
        return None
    order_info = order.get("order_info") or {}
    fee = order_info.get("shipping_fee")
    try:
        return int(fee) if fee is not None else None
    except Exception:
        return None


def has_success_order(data: Dict[str, Any]) -> bool:
    orders = data.get("orders") or []
    if not isinstance(orders, list) or not orders:
        return False
    for order in orders:
        try:
            order_info = order.get("order_info") or {}
            status = int(order_info.get("status")) if order_info.get("status") is not None else None
            if status == SUCCESS_STATUS:
                return True
        except Exception:
            continue
    return False


