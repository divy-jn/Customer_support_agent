"""Safe Payment-domain rollout slice.

Only refunds are enabled because they have an existing idempotent transaction
and confirmation boundary. Other payment intents deliberately fall back to
legacy until a customer-scoped payment-record capability exists.
"""

import re
from typing import Any


_ORDER_ID = re.compile(r"(?:order\s*(?:#|number)?\s*)(\d+)|#(\d+)", re.IGNORECASE)
_REFUND_INTENTS = {"refund", "refund_request", "process_refund"}
_REVIEW_INTENTS = {
    "billing_issue", "payment_failure", "payment_failed", "transaction_issue",
    "duplicate_charge", "charged_twice", "payment_method",
}


def _order_id(message: str) -> int | None:
    match = _ORDER_ID.search(message)
    return int(match.group(1) or match.group(2)) if match else None


async def handle_payment_request(state: dict[str, Any]) -> dict[str, Any]:
    """Prepare a refund request without performing a mutation."""
    intent = state.get("intent", "").lower()
    if intent in _REVIEW_INTENTS:
        return {
            "response": (
                "I’ll have a payment support specialist review this billing issue. "
                "They will verify the transaction before making any account changes."
            ),
            # No customer-scoped payment-record tool exists, so never infer a
            # transaction outcome or refund eligibility from conversation text.
            "requires_human": True,
            "escalated": False,
        }

    if intent not in _REFUND_INTENTS:
        return {
            "response": "I’ll connect you with the payment support flow for that billing request.",
            "escalated": False,
        }

    order_id = _order_id(state["message"])
    if order_id is None:
        return {
            "response": "Please share the order number so I can prepare the refund request.",
            "escalated": False,
        }

    confirmation = f"I’ll initiate a refund for order #{order_id}. Would you like me to proceed?"
    return {
        "response": confirmation,
        "pending_approval": {
            "action": "process_refund",
            "params": {"order_id": order_id},
            "message": confirmation,
            "customer_id": state.get("customer_id"),
            "session_id": state.get("session_id"),
        },
        "escalated": False,
    }
