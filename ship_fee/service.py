from dataclasses import dataclass
from typing import Any, Dict, Optional

from .config import get_orders_json_path, get_default_conversation_id
from .orders import load_orders, pick_latest_active_order, extract_shipping_fee, has_success_order
from .counter import CounterStore
from .intent import classify_intent
from . import templates as T


COUNTER_PREFIX = "shipfee:"


@dataclass
class ShipFeeResponse:
    case: str
    reply_text: str
    action: Optional[str]
    actions: Dict[str, bool]
    diagnostic: Dict[str, Any]


def _counter_key(conversation_id: str) -> str:
    return f"{COUNTER_PREFIX}{conversation_id}"


class ShipFeeService:
    def __init__(self, counter: Optional[CounterStore] = None) -> None:
        self.counter = counter or CounterStore()

    def answer(
        self,
        user_text: str,
        conversation_id: Optional[str] = None,
        orders_json_path: Optional[str] = None,
        orders_data: Optional[Dict[str, Any]] = None,
    ) -> ShipFeeResponse:
        conv_id = conversation_id or get_default_conversation_id()
        key = _counter_key(conv_id)
        # We only increment for free-ship requests or cancel threats.
        # For fee amount questions, we read current without incrementing.
        current = self.counter.get_current(key)

        if orders_data is not None:
            data = orders_data
        else:
            orders_path = orders_json_path or get_orders_json_path()
            data = load_orders(orders_path)
        order = pick_latest_active_order(data)

        if not order:
            return ShipFeeResponse(
                case="no_order",
                reply_text=T.TEMPLATE_NO_ORDER,
                action=None,
                actions={"apply_free_shipping": False},
                diagnostic={
                    "asked_count": asked,
                    "shipping_fee": None,
                    "order_id": None,
                    "status": None,
                    "picked_reason": "no_active_order",
                },
            )

        fee = extract_shipping_fee(order)
        order_info = order.get("order_info") or {}
        order_id = order_info.get("id")
        status = order_info.get("status")

        if fee == 0:
            return ShipFeeResponse(
                case="freeship",
                reply_text=T.TEMPLATE_FREESHIP,
                action=None,
                actions={"apply_free_shipping": False},
                diagnostic={
                    "asked_count": current,
                    "shipping_fee": fee,
                    "order_id": order_id,
                    "status": status,
                    "picked_reason": "fee_is_zero",
                },
            )

        signals = classify_intent(user_text)

        # Case selection per plan
        # If intent is smalltalk, return LLM smalltalk reply without counting
        if signals.get("intent") == "smalltalk":
            return ShipFeeResponse(
                case="smalltalk",
                reply_text=str(signals.get("smalltalk_reply") or "Dạ vâng ạ!"),
                action=None,
                actions={"apply_free_shipping": False},
                diagnostic={
                    "asked_count": current,
                    "shipping_fee": fee,
                    "order_id": order_id,
                    "status": status,
                    "picked_reason": "smalltalk",
                },
            )

        # If user explicitly asks fee amount, answer with the numeric fee regardless of ask count
        if signals.get("about_fee_amount"):
            return ShipFeeResponse(
                case="has_ship_first_time",
                reply_text=T.render_fee_amount(fee if fee is not None else 0),
                action=None,
                actions={"apply_free_shipping": False},
                diagnostic={
                    "asked_count": current,
                    "shipping_fee": fee,
                    "order_id": order_id,
                    "status": status,
                    "picked_reason": "explicit_fee_question",
                },
            )

        # From here on, we count as a free-ship related ask
        asked = self.counter.increase_and_get(key, ttl_seconds=900)

        if asked == 1 and not signals.get("wants_free"):
            return ShipFeeResponse(
                case="has_ship_first_time",
                reply_text=T.TEMPLATE_FIRST_TIME,
                action=None,
                actions={"apply_free_shipping": False},
                diagnostic={
                    "asked_count": asked,
                    "shipping_fee": fee,
                    "order_id": order_id,
                    "status": status,
                    "picked_reason": "first_time_ask",
                },
            )

        if signals.get("cancel_threat") or asked >= 3:
            loyal = has_success_order(data)
            return ShipFeeResponse(
                case="ask_free_ship_many_times",
                reply_text=(T.TEMPLATE_ESCALATE_FREESHIP_LOYAL if loyal else T.TEMPLATE_ESCALATE_FREESHIP_NEW),
                action="freeship",
                actions={"apply_free_shipping": True},
                diagnostic={
                    "asked_count": asked,
                    "shipping_fee": fee,
                    "order_id": order_id,
                    "status": status,
                    "picked_reason": "cancel_threat_or_third_time",
                },
            )

        if asked == 2 or signals.get("wants_free"):
            loyal = has_success_order(data)
            return ShipFeeResponse(
                case="ask_free_ship",
                reply_text=T.TEMPLATE_ASK_FREE,
                action=None,
                actions={"apply_free_shipping": False},
                diagnostic={
                    "asked_count": asked,
                    "shipping_fee": fee,
                    "order_id": order_id,
                    "status": status,
                    "picked_reason": "second_time_or_wants_free",
                },
            )

        # Fallback to first time template
        return ShipFeeResponse(
            case="has_ship_first_time",
            reply_text=T.TEMPLATE_FIRST_TIME,
            action=None,
            actions={"apply_free_shipping": False},
            diagnostic={
                "asked_count": asked,
                "shipping_fee": fee,
                "order_id": order_id,
                "status": status,
                "picked_reason": "fallback_first_time",
            },
        )


