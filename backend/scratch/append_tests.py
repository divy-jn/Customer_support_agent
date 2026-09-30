content = """        TicketLifecycleService.process_issue(ctx, active_ticket_id=999)

def test_legacy_invalid_domain_rejected():
    ctx = IssueContext(customer_id=1, domain="invalid_domain", intent="test", message="msg")
    with pytest.raises(ValueError, match="Invalid legacy enum value"):
        TicketLifecycleService.process_issue(ctx)

def test_legacy_invalid_urgency_rejected():
    ctx = IssueContext(customer_id=1, domain="product", intent="test", message="msg", urgency="invalid_urgency")
    with pytest.raises(ValueError, match="Invalid legacy enum value"):
        TicketLifecycleService.process_issue(ctx)

def test_legacy_invalid_sentiment_rejected():
    ctx = IssueContext(customer_id=1, domain="product", intent="test", message="msg", sentiment="invalid_sentiment")
    with pytest.raises(ValueError, match="Invalid legacy enum value"):
        TicketLifecycleService.process_issue(ctx)

def test_legacy_valid_adapts_successfully(mock_supabase, mock_create_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[])
    mock_create_ticket.return_value = json.dumps({"ticket_id": 102, "status": "success"})
    
    ctx = IssueContext(customer_id=1, domain="product", intent="technical_support", message="msg", urgency="high", sentiment="negative")
    res = TicketLifecycleService.process_issue(ctx)
    assert res.action == "CREATED"
    assert res.ticket_id == 102

def test_ticket_context_uses_internal_active_ticket_id(mock_supabase, mock_update_ticket):
    mock_supabase.table().select().eq().neq().execute.return_value = MagicMock(data=[
        {"id": 101, "customer_id": 1, "type": "technical_issue", "order_id": 123, "subject": "Issue: Technical Support"}
    ])
    mock_update_ticket.return_value = json.dumps({"status": "success"})
    
    # Passing active_ticket_id inside TicketContext
    ctx = TicketContext(customer_id=1, domain=OrchestrationDomain.PRODUCT, intent="technical_support", message="Help", active_ticket_id=101)
    res = TicketLifecycleService.process_issue(ctx)
    
    assert res.action == "UPDATED"
    assert res.ticket_id == 101
"""
with open('backend/tests/test_ticket_lifecycle.py', 'a', encoding='utf-8') as f:
    f.write(content)
