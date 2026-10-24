"""Deterministic Order domain agent for the V2 rollout.

Read-only tracking is executed directly with customer ownership enforced by the
shared tool.  Cancellation is only proposed here; the established approval
node performs the mutation after explicit customer confirmation.
"""

import asyncio
import json
import re
from typing import Any

from app.agents.approval_service import create_pending_approval
from app.tools import track_order, get_customer_history


_ORDER_ID = re.compile(r"(?:order\s*(?:#|number)?\s*)(\d+)|#(\d+)", re.IGNORECASE)
_CANCEL_INTENTS = {"cancel_order", "order_cancellation", "cancellation"}
_HISTORY_INTENTS = {"view_order_history", "order_history", "list_orders", "past_orders", "my_orders"}
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


def _update_order_state(ws: Any, oid: int, tool_name: str | None = None, tool_res: Any = None) -> dict[str, Any]:
    if hasattr(ws, "model_dump"):
        ws_dict = ws.model_dump(mode="json")
    elif isinstance(ws, dict):
        ws_dict = json.loads(json.dumps(ws))
    else:
        ws_dict = {}
    if "order_state" not in ws_dict or not isinstance(ws_dict.get("order_state"), dict):
        ws_dict["order_state"] = {"domain_status": "in_progress"}
    ws_dict["order_state"]["order_id"] = str(oid)
    if tool_name:
        ws_dict["order_state"]["last_tool"] = tool_name
        if tool_res is not None:
            ws_dict["order_state"]["last_tool_result"] = {
                "tool_name": tool_name,
                "entity_reference": str(oid),
                "result": tool_res,
            }
    return ws_dict


async def handle_order_request(state: dict[str, Any]) -> dict[str, Any]:
    """Handle tracking/cancellation and safely hand off delivery exceptions."""
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

    intent = state.get("intent", "").lower()
    if order_id is None:
        msg_lower = state.get("message", "").lower()
        if intent in _HISTORY_INTENTS or any(w in msg_lower for w in ("history", "orders", "past order", "previous order", "order list")):
            cust_id = state.get("customer_id")
            if cust_id:
                raw = await asyncio.to_thread(get_customer_history, cust_id)
                try:
                    data = json.loads(raw)
                    recent_orders = data.get("recent_orders") or []
                    if recent_orders:
                        lines = ["Here is your recent order history:"]
                        for ord_item in recent_orders:
                            oid = ord_item.get("id")
                            status = ord_item.get("status", "unknown")
                            date = ord_item.get("order_date", "")
                            prod_name = (ord_item.get("products") or {}).get("name", "Product")
                            tracking = ord_item.get("tracking_number")
                            entry = f"• **Order #{oid}**: {prod_name} — Status: *{status}*"
                            if date:
                                entry += f" (Placed: {date})"
                            if tracking:
                                entry += f" | Tracking: `{tracking}`"
                            lines.append(entry)
                        lines.append("\nPlease let me know if you would like more details about any specific order!")
                        return {
                            "response": "\n".join(lines),
                            "tool_results": raw,
                            "workflow_state": state.get("workflow_state"),
                            "escalated": False,
                        }
                    else:
                        return {
                            "response": "You don't have any past orders on file with us yet.",
                            "tool_results": raw,
                            "workflow_state": state.get("workflow_state"),
                            "escalated": False,
                        }
                except Exception:
                    pass
        if intent in {"follow_up_inquiry", "follow_up", "order_status", "general_order_inquiry"} or msg_lower in {"now?", "now", "what now?", "next?"}:
            return {
                "response": "I'm ready to assist you! If you would like to track, modify, or cancel a specific order, please let me know your order number.",
                "workflow_state": state.get("workflow_state"),
                "escalated": False,
            }
        return {
            "response": "Please share the order number so I can look that up for you.",
            "escalated": False,
        }

    ws_updated = _update_order_state(state.get("workflow_state"), order_id)

    if intent in _CANCEL_INTENTS:
        confirm_msg = f"I'd like to cancel order #{order_id} for you. Shall I go ahead?"
        cust_id = state.get("customer_id")
        sess_id = state.get("session_id") or "unknown"

        existing_p = state.get("pending_approval")
        if (
            isinstance(existing_p, dict)
            and existing_p.get("action") == "cancel_order"
            and existing_p.get("params", {}).get("order_id") == order_id
            and existing_p.get("status") == "pending"
        ):
            pending_dict = existing_p
            confirm_msg = existing_p.get("message") or confirm_msg
        elif cust_id and cust_id > 0:
            pending_obj = create_pending_approval(
                action="cancel_order",
                params={"order_id": order_id},
                customer_id=cust_id,
                session_id=sess_id,
                message=confirm_msg,
                intent=intent,
                original_message=state.get("message"),
            )
            pending_dict = pending_obj.model_dump(mode="json")
        else:
            pending_dict = {
                "action": "cancel_order",
                "params": {"order_id": order_id},
                "message": confirm_msg,
                "customer_id": cust_id,
                "session_id": sess_id,
            }

        return {
            "response": confirm_msg,
            "pending_approval": pending_dict,
            "workflow_state": ws_updated,
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
            "workflow_state": ws_updated,
            "requires_human": True,
            "escalated": False,
        }

    raw = await asyncio.to_thread(track_order, order_id, state.get("customer_id"))
    try:
        order = json.loads(raw)
    except json.JSONDecodeError:
        order = {"error": "Unable to read the order record."}
    if order.get("error"):
        return {
            "response": f"I couldn't retrieve that order: {order['error']}",
            "workflow_state": ws_updated,
            "escalated": False,
        }
    ws_with_tool = _update_order_state(state.get("workflow_state"), order_id, "track_order", raw)
    return {
        "response": _format_tracking(order),
        "tool_results": raw,
        "workflow_state": ws_with_tool,
        "escalated": False,
    }
