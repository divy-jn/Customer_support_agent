"""Safe Payment-domain rollout slice.

Only refunds are enabled because they have an existing idempotent transaction
and confirmation boundary. Other payment intents deliberately fall back to
legacy until a customer-scoped payment-record capability exists.
"""

import asyncio
import json
import re
from typing import Any

from app.agents.approval_service import create_pending_approval
from app.tools import get_payment_record


_ORDER_ID = re.compile(r"(?:order\s*(?:#|number)?\s*)(\d+)|#(\d+)", re.IGNORECASE)
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
    """Prepare a refund request without performing a mutation."""
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
                "response": f"I couldn’t verify a payment record for order #{order_id}. I’ll have a specialist investigate this safely.",
                "workflow_state": ws_updated,
                "requires_human": True,
                "escalated": False,
            }
        status = record.get("status", "unknown")
        ws_with_tool = _update_payment_state(state.get("workflow_state"), "get_payment_record", raw)
        return {
            "response": (
                f"I verified that the payment record for order #{order_id} is {status}. "
                "I’ll have a payment support specialist review the billing issue before making any account changes."
            ),
            "tool_results": raw,
            "workflow_state": ws_with_tool,
            "requires_human": True,
            "escalated": False,
        }

    if intent not in _REFUND_INTENTS:
        return {
            "response": "I’ll connect you with the payment support flow for that billing request.",
            "workflow_state": ws_updated,
            "escalated": False,
        }

    if order_id is None:
        return {
            "response": "Please share the order number so I can prepare the refund request.",
            "escalated": False,
        }

    confirmation = f"I’ll initiate a refund for order #{order_id}. Would you like me to proceed?"
    cust_id = state.get("customer_id")
    sess_id = state.get("session_id") or "unknown"

    existing_p = state.get("pending_approval")
    if (
        isinstance(existing_p, dict)
        and existing_p.get("action") == "process_refund"
        and existing_p.get("params", {}).get("order_id") == order_id
        and existing_p.get("status") == "pending"
    ):
        pending_dict = existing_p
        confirmation = existing_p.get("message") or confirmation
    elif cust_id and cust_id > 0:
        pending_obj = create_pending_approval(
            action="process_refund",
            params={"order_id": order_id},
            customer_id=cust_id,
            session_id=sess_id,
            message=confirmation,
            intent=intent,
            original_message=state.get("message"),
        )
        pending_dict = pending_obj.model_dump(mode="json")
    else:
        pending_dict = {
            "action": "process_refund",
            "params": {"order_id": order_id},
            "message": confirmation,
            "customer_id": cust_id,
            "session_id": sess_id,
        }

    return {
        "response": confirmation,
        "pending_approval": pending_dict,
        "workflow_state": ws_updated,
        "escalated": False,
    }
