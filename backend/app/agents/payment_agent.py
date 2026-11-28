"""Handle verified payment lookups and route refund requests for specialist review."""

import asyncio
import json
import re
from typing import Any

from app.tools import get_payment_record


_ORDER_ID = re.compile(r"(?:order\s*(?:#|number|id)?\s*)(\d+)|#(\d+)", re.IGNORECASE)
_REFUND_INTENTS = {"refund", "refund_request", "process_refund"}
_REVIEW_INTENTS = {
    "billing_issue", "payment_failure", "payment_failed", "transaction_issue",
    "duplicate_charge", "charged_twice",
}


def _order_id(message: str) -> int | None:
    match = _ORDER_ID.search(message)
    return int(match.group(1) or match.group(2)) if match else None


def _update_payment_state(ws: Any, tool_name: str | None = None, tool_res: Any = None) -> dict[str, Any]:
    if hasattr(ws, "model_dump"):
        ws_dict = ws.model_dump(mode="json")
    elif isinstance(ws, dict):
        ws_dict = json.loads(json.dumps(ws))
    else:
        ws_dict = {}
    if "payment_state" not in ws_dict or not isinstance(ws_dict.get("payment_state"), dict):
        ws_dict["payment_state"] = {"domain_status": "in_progress"}
    if tool_name:
        ws_dict["payment_state"]["last_tool"] = tool_name
        if tool_res is not None:
            ws_dict["payment_state"]["last_tool_result"] = {
                "tool_name": tool_name,
                "result": tool_res,
            }
    return ws_dict


async def handle_payment_request(state: dict[str, Any]) -> dict[str, Any]:
    """Resolve payment facts or ask Guardian for specialist review."""
    intent = state.get("intent", "").lower()

    order_id = _order_id(state["message"])
    if order_id is None:
        raw_ws = state.get("workflow_state") or {}
        ord_st = raw_ws.get("order_state") if isinstance(raw_ws, dict) else getattr(raw_ws, "order_state", None)
        if isinstance(ord_st, dict) and ord_st.get("order_id"):
            try:
                order_id = int(ord_st["order_id"])
            except (ValueError, TypeError):
                pass
        elif ord_st and hasattr(ord_st, "order_id") and ord_st.order_id:
            try:
                order_id = int(ord_st.order_id)
            except (ValueError, TypeError):
                pass

    ws_updated = _update_payment_state(state.get("workflow_state"))

    if intent in _REVIEW_INTENTS:
        if order_id is None:
            return {
                "response": "Please share the order number so I can verify the payment record before opening a support case.",
                "escalated": False,
            }
        raw = await asyncio.to_thread(get_payment_record, order_id, state.get("customer_id"))
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            record = {"error": "Unable to read the payment record."}
        if record.get("error"):
            return {
                "response": f"I couldn’t verify a payment record for order #{order_id}. A payment specialist needs to review this.",
                "workflow_state": ws_updated,
                "requires_human": True,
                "escalated": False,
            }
        status = record.get("status", "unknown")
        ws_with_tool = _update_payment_state(state.get("workflow_state"), "get_payment_record", raw)
        return {
            "response": (
                f"I verified that the payment record for order #{order_id} is {status}. "
                "This billing issue needs specialist review before any account changes."
            ),
            "tool_results": raw,
            "workflow_state": ws_with_tool,
            "requires_human": True,
            "escalated": False,
        }

    if intent not in _REFUND_INTENTS:
        return {
            "response": "This request is about payment or billing support.",
            "workflow_state": ws_updated,
            "escalated": False,
        }

    if order_id is None:
        return {
            "response": "Please share the order number for payment specialist review.",
            "escalated": False,
        }

    return {
        "response": f"A payment specialist needs to review the refund request for order #{order_id}.",
        "pending_approval": None,
        "workflow_state": ws_updated,
        "requires_human": True,
        "escalated": False,
    }
