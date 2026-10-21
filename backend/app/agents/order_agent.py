"""Deterministic Order domain agent for the V2 rollout.

Read-only tracking is executed directly with customer ownership enforced by the
shared tool.  Cancellation is only proposed here; the established approval
node performs the mutation after explicit customer confirmation.
"""

import asyncio
import json
import re
from typing import Any

from app.tools import track_order


_ORDER_ID = re.compile(r"(?:order\s*(?:#|number)?\s*)(\d+)|#(\d+)", re.IGNORECASE)
_CANCEL_INTENTS = {"cancel_order", "order_cancellation", "cancellation"}
_EXCEPTION_INTENTS = {
    "delivery_issue", "delivery_delay", "missing_item", "wrong_item",
    "damaged_item", "order_issue", "shipping_issue",
}


def _order_id(message: str) -> int | None:
    match = _ORDER_ID.search(message)
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def _format_tracking(order: dict[str, Any]) -> str:
    status = order.get("status", "unknown")
    tracking = order.get("tracking_number")
    product = (order.get("products") or {}).get("name")
    details = f"Order #{order.get('id')}"
    if product:
        details += f" for {product}"
    details += f" is currently {status}."
    if tracking:
        details += f" Tracking number: {tracking}."
    return details


async def handle_order_request(state: dict[str, Any]) -> dict[str, Any]:
    """Handle tracking/cancellation and safely hand off delivery exceptions."""
    order_id = _order_id(state["message"])
    intent = state.get("intent", "").lower()
    if order_id is None:
        return {
            "response": "Please share the order number so I can look that up for you.",
            "escalated": False,
        }

    if intent in _CANCEL_INTENTS:
        return {
            "response": f"I'd like to cancel order #{order_id} for you. Shall I go ahead?",
            "pending_approval": {
                "action": "cancel_order",
                "params": {"order_id": order_id},
                "message": f"I'd like to cancel order #{order_id} for you. Shall I go ahead?",
                "customer_id": state.get("customer_id"),
                "session_id": state.get("session_id"),
            },
            "escalated": False,
        }

    if intent in _EXCEPTION_INTENTS:
        return {
            "response": (
                f"I’m sorry there is an issue with order #{order_id}. "
                "I’ll have a support specialist review the delivery details and help resolve it."
            ),
            # There is no claim/returns tool yet.  Explicitly require Guardian
            # to create or update a customer-scoped support case instead of
            # guessing about fulfilment, replacement, or refund eligibility.
            "requires_human": True,
            "escalated": False,
        }

    raw = await asyncio.to_thread(track_order, order_id, state.get("customer_id"))
    try:
        order = json.loads(raw)
    except json.JSONDecodeError:
        order = {"error": "Unable to read the order record."}
    if order.get("error"):
        return {"response": f"I couldn't retrieve that order: {order['error']}", "escalated": False}
    return {"response": _format_tracking(order), "tool_results": raw, "escalated": False}
