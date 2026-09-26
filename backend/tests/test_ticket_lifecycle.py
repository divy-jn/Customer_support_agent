import pytest
import json
from unittest.mock import patch, MagicMock
from app.tickets.lifecycle import TicketLifecycleService, IssueContext, TicketLifecycleResult

@pytest.fixture
def mock_supabase():
    with patch("app.tickets.lifecycle.supabase") as mock:
        yield mock

@pytest.fixture
def mock_create_ticket():
    with patch("app.tickets.lifecycle.create_ticket") as mock:
        yield mock

@pytest.fixture
def mock_update_ticket():
    with patch("app.tickets.lifecycle.update_ticket") as mock:
        yield mock

def test_ignored_intent():
    ctx = IssueContext(
        customer_id=1,
        domain="product",
        intent="product_inquiry",
        message="What is the battery life?"
    )
    result = TicketLifecycleService.process_issue(ctx)
    assert result.action == "IGNORED"
    assert result.ticket_id is None
    assert result.matched_existing is False

def test_create_new_ticket(mock_supabase, mock_create_ticket):
    # Mock no existing tickets
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[])
    
    mock_create_ticket.return_value = json.dumps({"ticket_id": 101, "status": "success"})
    
    ctx = IssueContext(
        customer_id=1,
        domain="product",
        intent="technical_support",
        message="My phone screen is flickering.",
        order_id=456,
        product_name="SuperPhone"
    )
    
    result = TicketLifecycleService.process_issue(ctx)
    assert result.action == "CREATED"
    assert result.ticket_id == 101
    assert result.order_id == 456
    assert result.matched_existing is False
    
    # create_ticket should be called with correct fields
    mock_create_ticket.assert_called_once_with(
        customer_id=1,
        subject="Issue: Technical Support - SuperPhone",
        description="My phone screen is flickering.",
        ticket_type="technical_issue",
        priority="medium",
        channel="chat",
        order_id=456
    )

def test_update_existing_ticket_same_order(mock_supabase, mock_update_ticket, mock_create_ticket):
    # Mock existing ticket with same order_id
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "order_id": 456, "type": "technical_issue", "status": "open", "priority": "medium", "subject": "Issue: Technical Support"}
    ])
    
    ctx = IssueContext(
        customer_id=1,
        domain="product",
        intent="technical_support",
        message="It is still flickering today.",
        order_id=456
    )
    
    result = TicketLifecycleService.process_issue(ctx, active_ticket_id=101)
    
    # Should update, not create
    assert result.action == "UPDATED"
    assert result.ticket_id == 101
    assert result.matched_existing is True
    assert mock_create_ticket.call_count == 0

def test_different_order_creates_new_ticket(mock_supabase, mock_create_ticket):
    # Mock existing ticket but with a DIFFERENT order_id
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "order_id": 456, "type": "technical_issue", "status": "open", "priority": "medium", "subject": "Issue: Technical Support"}
    ])
    
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    
    ctx = IssueContext(
        customer_id=1,
        domain="product",
        intent="technical_support",
        message="My OTHER phone screen is flickering.",
        order_id=789 # Different order
    )
    
    # Even if active_ticket_id is provided, order mismatch should force a new ticket
    result = TicketLifecycleService.process_issue(ctx, active_ticket_id=101)
    
    assert result.action == "CREATED"
    assert result.ticket_id == 102
    assert result.matched_existing is False
