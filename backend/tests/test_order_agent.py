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
@patch("app.agents.order_agent.get_customer_history")
async def test_authentication_is_required_before_any_recent_order_lookup(mock_history):
    result = await handle_order_request({"message": "Where is my order?", "intent": "order_tracking"})
    assert "couldn’t verify" in result["response"]
    mock_history.assert_not_called()


@pytest.mark.asyncio
async def test_delivery_exception_requires_guardian_controlled_handoff():
    result = await handle_order_request({
        "message": "My order #42 arrived damaged", "intent": "damaged_item", "customer_id": 7,
    })
    assert result["requires_human"] is True
    assert "specialist" in result["response"]


@pytest.mark.asyncio
@patch("app.agents.order_agent.get_customer_history")
async def test_order_history_direct_retrieval(mock_history):
    mock_history.return_value = json.dumps({
        "recent_orders": [
            {"id": 101, "status": "delivered", "order_date": "2026-03-01", "products": {"name": "Laptop"}},
        ]
    })
    result = await handle_order_request({
        "message": "give my history of order",
        "intent": "view_order_history",
        "customer_id": 7,
    })
    assert "Order #101" in result["response"]
    assert "Laptop" in result["response"]


@pytest.mark.asyncio
@patch("app.agents.order_agent.get_customer_history", return_value='{"recent_orders": []}')
async def test_order_follow_up_inquiry_without_order_id(mock_history):
    result = await handle_order_request({
        "message": "now?",
        "intent": "follow_up_inquiry",
        "customer_id": 7,
    })
    assert "don't have any past orders" in result["response"]
    mock_history.assert_called_once_with(7)




@pytest.mark.asyncio
@patch("app.agents.order_agent.get_customer_history")
async def test_cancellation_of_my_orders_still_requires_an_order_id(mock_history):
    result = await handle_order_request({
        "message": "cancel my orders", "intent": "cancel_order", "customer_id": 7,
    })
    assert "order number you want to cancel" in result["response"]
    assert "pending_approval" not in result
    mock_history.assert_not_called()
