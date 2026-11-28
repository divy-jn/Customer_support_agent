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
from app.agents.grounded_response import synthesize_lookup_reply
from app.agents.response_composer import format_support_timestamp
from app.agents.routing_policy import normalize_intent
from app.models import LookupContext
from app.tools import track_order, get_customer_history


_ORDER_ID = re.compile(r"(?:order\s*(?:#|number|id)?\s*)(\d+)|#(\d+)", re.IGNORECASE)
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


def _order_facts(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": order["id"],
        "product": (order.get("products") or {}).get("name"),
        "status": order.get("status"),
        "tracking_number": order.get("tracking_number"),
        "placed_at": format_support_timestamp(order.get("order_date")),
    }


async def _order_history(state: dict[str, Any]) -> dict[str, Any]:
    customer_id = state.get("customer_id")
    try:
        raw = await asyncio.to_thread(get_customer_history, customer_id) if type(customer_id) is int and customer_id > 0 else "{}"
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict) or payload.get("error")
            or payload.get("customer_id", customer_id) != customer_id
        ):
            raise ValueError("Invalid order history result")
        orders = payload.get("recent_orders")
        if not isinstance(orders, list):
            raise ValueError("Invalid order history result")
        if any(order.get("customer_id", customer_id) != customer_id for order in orders):
            raise ValueError("Unexpected order owner")
        records = [_order_facts(order) for order in orders]
        if orders:
            lines = ["Here is your recent order history:", ""]
            for order in orders:
                product = (order.get("products") or {}).get("name", "Product")
                status = order.get("status", "unknown")
                entry = f"- **Order #{order['id']}**: {product} — Status: *{status}*"
                if date := format_support_timestamp(order.get("order_date")):
                    entry += f" (Placed: {date})"
                if tracking := order.get("tracking_number"):
                    entry += f" | Tracking: `{tracking}`"
                lines.append(entry)
            lines.append("\nPlease let me know if you would like more details about any specific order!")
            response = "\n".join(lines)
        else:
            response = "You don't have any past orders on file with us yet."
    except (AttributeError, KeyError, TypeError, ValueError):
        return {
            "response": "I couldn’t verify your order history right now. Please try again.",
            "tool_results": None,
            "workflow_state": state.get("workflow_state"),
            "escalated": False,
        }
    response = await synthesize_lookup_reply(
        message=state["message"], history=state.get("conversation_history", []),
        facts={"kind": "order_history", "records": records}, fallback=response,
    )
    return {
        "response": response,
        "tool_results": raw,
        "workflow_state": state.get("workflow_state"),
        "lookup_context": LookupContext(kind="order_history").model_dump(),
        "escalated": False,
    }


async def handle_order_request(state: dict[str, Any]) -> dict[str, Any]:
    """Handle tracking/cancellation and safely hand off delivery exceptions."""
    intent = normalize_intent("order", state.get("intent", ""))
    msg_lower = state["message"].lower()
    order_id = _order_id(state["message"])
    if intent == "view_order_history" or (
        order_id is None
        and any(phrase in msg_lower for phrase in ("order history", "past order", "previous order", "order list", "my orders"))
    ):
        return await _order_history(state)
    if order_id is None:
        order_id = state.get("lookup_order_id")
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

    if order_id is None:
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
        cust_id_raw = state.get("customer_id")
        try:
            cust_id = int(cust_id_raw) if cust_id_raw is not None else None
        except (ValueError, TypeError):
            cust_id = None
        sess_id = state.get("session_id") or "unknown"

        existing_p = state.get("pending_approval")
        if (
            isinstance(existing_p, dict)
            and existing_p.get("action") == "cancel_order"
            and str(existing_p.get("params", {}).get("order_id")) == str(order_id)
            and existing_p.get("status") == "pending"
            and "approval_id" in existing_p
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
            pending_dict = None

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
                "The delivery details need review by a support specialist."
            ),
            # There is no claim/returns tool yet.  Explicitly require Guardian
            # to create or update a customer-scoped support case instead of
            # guessing about fulfilment, replacement, or refund eligibility.
            "workflow_state": ws_updated,
            "requires_human": True,
            "escalated": False,
        }

    customer_id = state.get("customer_id")
    if type(customer_id) is not int or customer_id <= 0:
        return {
            "response": "I couldn’t verify that order right now. Please try again.",
            "workflow_state": ws_updated,
            "escalated": False,
        }
    try:
        raw = await asyncio.to_thread(track_order, order_id, customer_id)
        order = json.loads(raw)
        if not isinstance(order, dict):
            raise ValueError("Unexpected order tool result")
        if error := order.get("error"):
            if error in (
                f"Order #{order_id} not found",
                f"Order #{order_id} not found or does not belong to you",
            ):
                return {
                    "response": f"I couldn't find order #{order_id} on your account.",
                    "workflow_state": ws_updated,
                    "escalated": False,
                }
            raise ValueError("Order lookup failed")
        if order.get("customer_id", customer_id) != customer_id or order.get("id") != order_id:
            raise ValueError("Unexpected order identity")
        facts = _order_facts(order)
    except Exception:
        return {
            "response": "I couldn’t verify that order right now. Please try again.",
            "workflow_state": ws_updated,
            "escalated": False,
        }

    response = await synthesize_lookup_reply(
        message=state["message"], history=state.get("conversation_history", []),
        facts={"kind": "order", "records": [facts]}, fallback=_format_tracking(order),
    )
    ws_with_tool = _update_order_state(state.get("workflow_state"), order_id, "track_order", raw)
    return {
        "response": response,
        "tool_results": raw,
        "workflow_state": ws_with_tool,
        "lookup_context": LookupContext(kind="order", record_id=order_id).model_dump(),
        "escalated": False,
    }
