import json
from unittest.mock import patch

import pytest

from app.agents.order_agent import handle_order_request


@pytest.mark.asyncio
@patch("app.agents.order_agent.track_order")
async def test_tracking_uses_authenticated_customer_and_formats_response(mock_track):
    mock_track.return_value = json.dumps({"id": 42, "status": "shipped", "tracking_number": "TRACK-42", "products": {"name": "Headphones"}})
    result = await handle_order_request({"message": "Track order #42", "intent": "order_tracking", "customer_id": 7})
    assert "Order #42 for Headphones is currently shipped" in result["response"]
    mock_track.assert_called_once_with(42, 7)


@pytest.mark.asyncio
async def test_cancellation_only_creates_approval_request():
    result = await handle_order_request({"message": "Cancel order #42", "intent": "order_cancellation", "customer_id": 7, "session_id": "s1"})
    assert result["pending_approval"]["action"] == "cancel_order"
    assert result["pending_approval"]["params"] == {"order_id": 42}


@pytest.mark.asyncio
async def test_order_id_is_required_before_any_database_lookup():
    result = await handle_order_request({"message": "Where is my order?", "intent": "order_tracking"})
    assert "share the order number" in result["response"]


@pytest.mark.asyncio
async def test_delivery_exception_requires_guardian_controlled_handoff():
    result = await handle_order_request({
        "message": "My order #42 arrived damaged", "intent": "damaged_item", "customer_id": 7,
    })
    assert result["requires_human"] is True
    assert "specialist" in result["response"]
