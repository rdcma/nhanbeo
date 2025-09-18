from dataclasses import dataclass
from typing import Any, Dict, Optional

from .config import get_orders_json_path, get_default_conversation_id, REPEAT_FREESHIP_TO_AGENT_THRESHOLD
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


def _tagged_key(conversation_id: str) -> str:
    return f"{COUNTER_PREFIX}{conversation_id}:tagged"


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
        tagged_key = _tagged_key(conv_id)
        # We only increment for free-ship requests or cancel threats.
        # For fee amount questions, we read current without incrementing.
        current = self.counter.get_current(key)

        # If already tagged to agent for this conversation within TTL, do not reply further
        if self.counter.get_flag(tagged_key):
            return ShipFeeResponse(
                case="tagged_agent",
                reply_text="",
                action=None,
                actions={"apply_free_shipping": False},
                diagnostic={
                    "asked_count": current,
                    "shipping_fee": None,
                    "order_id": None,
                    "status": None,
                    "picked_reason": "already_tagged_agent",
                },
            )

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
                    "asked_count": current,
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

        # Complaint about fee (without explicit free ask): reply with priority sentence; do NOT count as freeship ask
        if signals.get("is_complaint"):
            return ShipFeeResponse(
                case="fee_question_complaint",
                reply_text=T.render_fee_complaint(),
                action=None,
                actions={"apply_free_shipping": False},
                diagnostic={
                    "asked_count": current,
                    "shipping_fee": fee,
                    "order_id": order_id,
                    "status": status,
                    "picked_reason": "fee_complaint_priority",
                },
            )

        # If user explicitly asks fee amount, answer with the numeric fee regardless of ask count
        if signals.get("about_fee_amount") and not signals.get("wants_free"):
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

        # Merge cancel_threat with wants_free flow
        wants_free_effective = bool(signals.get("wants_free") or signals.get("cancel_threat"))

        if wants_free_effective:
            # Ask freeship specific replies per count
            if asked <= 1:
                return ShipFeeResponse(
                    case="ask_free_ship_first_time",
                    reply_text=T.TEMPLATE_ASK_FREE_FIRST_TIME,
                    action=None,
                    actions={"apply_free_shipping": False},
                    diagnostic={
                        "asked_count": asked,
                        "shipping_fee": fee,
                        "order_id": order_id,
                        "status": status,
                        "picked_reason": "ask_free_first_time",
                    },
                )
            # second time or more
            over_tag = asked > REPEAT_FREESHIP_TO_AGENT_THRESHOLD
            should_tag = asked >= REPEAT_FREESHIP_TO_AGENT_THRESHOLD
            # If tagging now, set the flag so subsequent messages short-circuit
            if should_tag:
                self.counter.set_flag(tagged_key, True, ttl_seconds=900)
            return ShipFeeResponse(
                case="ask_free_ship",
                reply_text=("" if over_tag else T.TEMPLATE_ASK_FREE),
                action=("tagAgent" if should_tag else None),
                actions={"apply_free_shipping": False},
                diagnostic={
                    "asked_count": asked,
                    "shipping_fee": fee,
                    "order_id": order_id,
                    "status": status,
                    "picked_reason": "ask_free_repeat",
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


