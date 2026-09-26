import pytest
import json
from unittest.mock import patch, MagicMock
from app.tickets.lifecycle import TicketLifecycleService, IssueContext

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


# 1. original description preserved on update & 2. follow-up appended
def test_description_append_preserves_original(mock_supabase, mock_update_ticket):
    # Mock lookup
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue"}
    ])
    mock_update_ticket.return_value = json.dumps({"status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Follow-up message")
    res = TicketLifecycleService.process_issue(ctx)
    
    assert res.action == "UPDATED"
    mock_update_ticket.assert_called_once_with(
        ticket_id=101, 
        customer_id=1, 
        priority="medium", 
        description_append="Follow-up message",
        order_id=None
    )


# 3. explicit order + null candidate does not blindly merge -> CREATE
def test_explicit_order_null_candidate_creates(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue"} # order_id is null
    ])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help with order", order_id=123)
    res = TicketLifecycleService.process_issue(ctx)
    
    assert res.action == "CREATED"


# 4. active ticket + newly supplied order can continue safely -> UPDATE
def test_active_ticket_new_order_updates(mock_supabase, mock_update_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue"} # order_id is null
    ])
    mock_update_ticket.return_value = json.dumps({"status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help with order", order_id=123)
    res = TicketLifecycleService.process_issue(ctx, active_ticket_id=101)
    
    assert res.action == "UPDATED"
    mock_update_ticket.assert_called_once_with(
        ticket_id=101, 
        customer_id=1, 
        priority="medium", 
        description_append="Help with order",
        order_id=123
    )

# 5. different product identity -> new ticket
def test_different_product_creates(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "subject": "Issue - ProductA"}
    ])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help", product_name="ProductB")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "CREATED"


# 6. unknown product identity is not treated as equal -> new ticket
def test_unknown_product_identity_creates(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "subject": "General Issue"} # No reliable product
    ])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help", product_name="ProductB")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "CREATED"


# 7. ambiguous tickets -> CREATE
def test_ambiguous_tickets_creates(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "order_id": 123},
        {"id": 102, "customer_id": 1, "type": "technical_issue", "order_id": 123}
    ])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 103, "status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help", order_id=123)
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "CREATED"


# 8. exact candidate -> UPDATE
def test_exact_candidate_updates(mock_supabase, mock_update_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "order_id": 123}
    ])
    mock_update_ticket.return_value = json.dumps({"status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help", order_id=123)
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "UPDATED"


# 9. multiple exact candidates -> deterministic selection
def test_ambiguous_tickets_without_identifier_creates(mock_supabase, mock_create_ticket):
    # Two same-type open tickets, no active_ticket_id, no order_id, no product identity
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue"},
        {"id": 102, "customer_id": 1, "type": "technical_issue"}
    ])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 103, "status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx)
    # Expected: CREATE because they are ambiguous and we don't use recency tiebreaker for unrelated issues
    assert res.action == "CREATED"


# 10. lookup failure -> FAILED, no create
def test_lookup_failure_returns_failed(mock_supabase):
    mock_supabase.table().select().eq().neq().execute.side_effect = Exception("DB Down")
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "FAILED"
    assert res.reason == "Ticket lifecycle operation failed."


# 11. update failure -> FAILED
def test_update_failure_returns_failed(mock_supabase, mock_update_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue"}
    ])
    mock_update_ticket.return_value = json.dumps({"error": "Failed to update"})
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "FAILED"


# 12. create failure -> FAILED
def test_create_failure_returns_failed(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[])
    mock_create_ticket.return_value = json.dumps({"error": "Failed to create"})
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "FAILED"


# 13. cross-customer active_ticket_id rejected
def test_cross_customer_not_matched(mock_supabase, mock_create_ticket):
    def mock_eq(field, value):
        if field == "customer_id" and value == 1:
            return MagicMock(neq=lambda *args: MagicMock(execute=lambda: MagicMock(data=[])))
        return MagicMock()
    
    mock_supabase.table().select().eq.side_effect = mock_eq
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx, active_ticket_id=101)
    
    assert res.action == "CREATED"
