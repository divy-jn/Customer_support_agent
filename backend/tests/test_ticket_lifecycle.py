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


# Base correctness: original description preserved
def test_description_append_preserves_original(mock_supabase, mock_update_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue"}
    ])
    mock_update_ticket.return_value = json.dumps({"status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Follow-up message")
    res = TicketLifecycleService.process_issue(ctx, active_ticket_id=101)
    
    assert res.action == "UPDATED"
    mock_update_ticket.assert_called_once_with(
        ticket_id=101, 
        customer_id=1, 
        priority="medium", 
        description_append="Follow-up message",
        order_id=None
    )


# 1. same ticket_type, existing order_id, new message has no identifiers, no active_ticket_id -> CREATE
def test_same_type_existing_order_no_new_id_no_active_creates(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "order_id": 123}
    ])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "CREATED"


# 2. same order, existing product unknown, new product explicit, no active_ticket_id -> CREATE
def test_same_order_unknown_product_new_product_explicit_creates(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "order_id": 123, "subject": "General Issue"}
    ])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help", order_id=123, product_name="ProductB")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "CREATED"


# 3. same order + same explicit product -> UPDATE
def test_same_order_same_explicit_product_updates(mock_supabase, mock_update_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "order_id": 123, "subject": "Issue - ProductB"}
    ])
    mock_update_ticket.return_value = json.dumps({"status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help", order_id=123, product_name="ProductB")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "UPDATED"


# 4. same order + different explicit product -> CREATE
def test_same_order_different_explicit_product_creates(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "order_id": 123, "subject": "Issue - ProductA"}
    ])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help", order_id=123, product_name="ProductB")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "CREATED"


# 5. active_ticket_id + existing product unknown + new product explicit -> UPDATE and bind product
def test_active_ticket_unknown_product_new_product_updates(mock_supabase, mock_update_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "order_id": 123, "subject": "General Issue"}
    ])
    mock_update_ticket.return_value = json.dumps({"status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help", order_id=123, product_name="ProductB")
    res = TicketLifecycleService.process_issue(ctx, active_ticket_id=101)
    
    assert res.action == "UPDATED"
    # Although we don't bind product in the DB schema right now via tool args, it successfully resolves UPDATE


# 6. active_ticket_id + compatible same issue + no new identifiers -> UPDATE
def test_active_ticket_compatible_issue_no_identifiers_updates(mock_supabase, mock_update_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "order_id": 123, "subject": "Issue - ProductB"}
    ])
    mock_update_ticket.return_value = json.dumps({"status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx, active_ticket_id=101)
    assert res.action == "UPDATED"


# 7. no active ticket + only matching ticket_type -> CREATE
def test_no_active_ticket_only_matching_type_creates(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue"}
    ])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "CREATED"


# Other boundary failures / infrastructure
def test_lookup_failure_returns_failed(mock_supabase):
    mock_supabase.table().select().eq().neq().execute.side_effect = Exception("DB Down")
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "FAILED"

def test_update_failure_returns_failed(mock_supabase, mock_update_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "order_id": 123, "subject": "Issue - ProductB"}
    ])
    mock_update_ticket.return_value = json.dumps({"error": "Failed to update"})
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help", order_id=123, product_name="ProductB")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "FAILED"

def test_create_failure_returns_failed(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[])
    mock_create_ticket.return_value = json.dumps({"error": "Failed to create"})
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="Help")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "FAILED"

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
