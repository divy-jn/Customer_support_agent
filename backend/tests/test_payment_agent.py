import pytest

from app.agents.payment_agent import handle_payment_request


@pytest.mark.asyncio
async def test_refund_needs_order_id():
    result = await handle_payment_request({"message": "I need a refund", "intent": "refund_request"})
    assert "share the order number" in result["response"]


@pytest.mark.asyncio
async def test_refund_is_approval_gated():
    result = await handle_payment_request({
        "message": "Refund order #19", "intent": "refund_request", "customer_id": 7, "session_id": "s1",
    })
    assert result["pending_approval"]["action"] == "process_refund"
    assert result["pending_approval"]["params"] == {"order_id": 19}


@pytest.mark.asyncio
async def test_billing_does_not_attempt_an_unsupported_mutation():
    result = await handle_payment_request({"message": "My card was charged twice", "intent": "billing_issue"})
    assert "pending_approval" not in result
    assert result["requires_human"] is True
