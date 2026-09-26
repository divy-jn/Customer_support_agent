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

# 1. first issue -> CREATE
def test_first_issue_creates(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 101, "status": "success"})
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Broken screen", order_id=456)
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "CREATED"
    assert res.ticket_id == 101

# 2. same issue + same order -> UPDATE
# 12. actual ticket mutation occurs on UPDATE
def test_same_issue_same_order_updates(mock_supabase, mock_update_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "order_id": 456, "type": "technical_issue", "status": "open", "priority": "medium", "subject": "Broken screen"}
    ])
    mock_update_ticket.return_value = json.dumps({"status": "success", "ticket_id": 101})
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Still broken", order_id=456)
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "UPDATED"
    assert res.ticket_id == 101
    mock_update_ticket.assert_called_once_with(ticket_id=101, customer_id=1, priority=None, description_append="Still broken")

# 3. same issue, paraphrased message -> UPDATE
def test_same_issue_paraphrased_updates(mock_supabase, mock_update_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "order_id": 456, "type": "technical_issue", "status": "open", "priority": "medium", "subject": "Broken screen"}
    ])
    mock_update_ticket.return_value = json.dumps({"status": "success", "ticket_id": 101})
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="The display is cracked now", order_id=456)
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "UPDATED"

# 4. same order + DIFFERENT issue type -> NEW TICKET
def test_same_order_different_issue_creates(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "order_id": 456, "type": "technical_issue", "status": "open"}
    ])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    ctx = IssueContext(customer_id=1, domain="product", intent="refund_request", message="I want a refund", order_id=456)
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "CREATED"
    assert res.ticket_id == 102

# 5. same product + DIFFERENT issue -> NEW TICKET
def test_same_product_different_issue_creates(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "subject": "Issue: Technical Support - SuperPhone"}
    ])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    ctx = IssueContext(customer_id=1, domain="product", intent="billing_issue", message="I was double charged", product_name="SuperPhone")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "CREATED"

# 6. active_ticket_id + compatible intent -> UPDATE
def test_active_ticket_compatible_intent_updates(mock_supabase, mock_update_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue"}
    ])
    mock_update_ticket.return_value = json.dumps({"status": "success"})
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx, active_ticket_id=101)
    assert res.action == "UPDATED"

# 7. active_ticket_id + incompatible intent -> NEW TICKET
# 18. issue switch invalidates stale active_ticket_id where required
def test_active_ticket_incompatible_intent_creates(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue"}
    ])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    ctx = IssueContext(customer_id=1, domain="product", intent="billing_issue", message="Help")
    res = TicketLifecycleService.process_issue(ctx, active_ticket_id=101)
    assert res.action == "CREATED"

# 8. active_ticket_id + changed order -> NEW TICKET
def test_active_ticket_changed_order_creates(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "order_id": 111}
    ])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help with another", order_id=222)
    res = TicketLifecycleService.process_issue(ctx, active_ticket_id=101)
    assert res.action == "CREATED"

# 9. multiple candidate tickets -> deterministic selection (by recency)
def test_multiple_candidates_deterministic_selection(mock_supabase, mock_update_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "updated_at": "2026-01-01"},
        {"id": 102, "customer_id": 1, "type": "technical_issue", "updated_at": "2026-02-01"}
    ])
    mock_update_ticket.return_value = json.dumps({"status": "success"})
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "UPDATED"
    assert res.ticket_id == 102 # Selected the more recent one

# 10. ambiguous candidates -> safe behavior
def test_ambiguous_candidates_safe_behavior(mock_supabase, mock_update_ticket):
    # Two tickets of the same type without clear distinguishing features
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "updated_at": "2026-01-01"},
        {"id": 102, "customer_id": 1, "type": "technical_issue", "updated_at": "2026-02-01"}
    ])
    mock_update_ticket.return_value = json.dumps({"status": "success"})
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx)
    # Picks most recent to safely avoid spamming duplicate tickets
    assert res.action == "UPDATED"
    assert res.ticket_id == 102

# 11. stale ticket does not automatically absorb unrelated work
def test_stale_ticket_does_not_absorb_unrelated(mock_supabase, mock_create_ticket):
    # Ticket has product A, user asks about product B without active_ticket_id
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "subject": "Issue - Product A"}
    ])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help", product_name="Product B")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "CREATED"

# 13. lookup failure -> FAILED, NEVER CREATE
# 20. sanitized failure reason
def test_lookup_failure_returns_failed(mock_supabase):
    mock_supabase.table().select().eq().neq().execute.side_effect = Exception("DB Down")
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "FAILED"
    assert res.reason == "Ticket lifecycle operation failed." # Sanitized

# 14. create failure -> FAILED
def test_create_failure_returns_failed(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[])
    mock_create_ticket.return_value = json.dumps({"error": "Failed to create"})
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "FAILED"

# 15. update failure -> FAILED
def test_update_failure_returns_failed(mock_supabase, mock_update_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue"}
    ])
    mock_update_ticket.return_value = json.dumps({"error": "Failed to update"})
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "FAILED"

# 16. cross-customer ticket cannot be matched
def test_cross_customer_not_matched(mock_supabase, mock_create_ticket):
    # Setup mock to simulate that the DB only returns tickets for customer 2 if customer_id=2
    def mock_eq(field, value):
        if field == "customer_id" and value == 1:
            return MagicMock(neq=lambda *args: MagicMock(execute=lambda: MagicMock(data=[])))
        return MagicMock()
    
    mock_supabase.table().select().eq.side_effect = mock_eq
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    # Even if we maliciously pass active_ticket_id=101 (belonging to customer 2)
    res = TicketLifecycleService.process_issue(ctx, active_ticket_id=101)
    
    assert res.action == "CREATED"
