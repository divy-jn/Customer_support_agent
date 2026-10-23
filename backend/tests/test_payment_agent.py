import json
from unittest.mock import patch

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
@patch("app.agents.payment_agent.get_payment_record")
async def test_billing_uses_customer_scoped_ledger_and_requires_human_review(mock_payment):
    mock_payment.return_value = json.dumps({"order_id": 19, "customer_id": 7, "status": "captured"})
    result = await handle_payment_request({
        "message": "My card was charged twice for order #19", "intent": "billing_issue", "customer_id": 7,
    })
    mock_payment.assert_called_once_with(19, 7)
    assert "captured" in result["response"]
    assert "pending_approval" not in result
    assert result["requires_human"] is True


@pytest.mark.asyncio
async def test_billing_requires_order_id_for_authoritative_lookup():
    result = await handle_payment_request({"message": "My card was charged twice", "intent": "billing_issue"})
    assert "share the order number" in result["response"]
