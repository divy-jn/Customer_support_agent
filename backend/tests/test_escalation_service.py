import uuid
import pytest
from unittest.mock import patch, MagicMock
from app.escalation.service import EscalationService, EscalationRequest, EscalationResult, IdempotencyConflict

@pytest.fixture
def mock_supabase():
    with patch("app.escalation.service.supabase") as mock:
        yield mock

def test_escalation_success(mock_supabase):
    req_id = uuid.uuid4()
    event_id = str(uuid.uuid4())
    mock_supabase.rpc().execute.return_value = MagicMock(data={
        "status": "success",
        "message": "Escalation triggered successfully.",
        "escalation_event_id": event_id,
        "session_id": "sess_1"
    })
    
    req = EscalationRequest(
        customer_id=1,
        session_id="sess_1",
        client_request_id=req_id,
        sentiment="negative",
        urgency="high"
    )
    
    res = EscalationService.escalate_session(req)
    assert res.is_success
    assert str(res.escalation_event_id) == event_id
    assert res.session_id == "sess_1"
    assert res.status == "success"

def test_escalation_bypassed(mock_supabase):
    req_id = uuid.uuid4()
    mock_supabase.rpc().execute.return_value = MagicMock(data={
        "status": "bypassed",
        "message": "Session is already ACTIVE. Escalation bypassed.",
        "session_id": "sess_1"
    })
    
    req = EscalationRequest(customer_id=1, session_id="sess_1", client_request_id=req_id)
    res = EscalationService.escalate_session(req)
    
    assert res.status == "bypassed"
    assert res.escalation_event_id is None

def test_escalation_idempotency_conflict(mock_supabase):
    req_id = uuid.uuid4()
    mock_supabase.rpc().execute.return_value = MagicMock(data={
        "error": "IdempotencyConflict",
        "message": "Conflict",
        "previous_result": {"status": "success"}
    })
    
    req = EscalationRequest(customer_id=1, session_id="sess_1", client_request_id=req_id)
    
    with pytest.raises(IdempotencyConflict) as exc:
        EscalationService.escalate_session(req)
        
    assert exc.value.previous_result == {"status": "success"}

def test_escalation_authorization_failure(mock_supabase):
    req_id = uuid.uuid4()
    mock_supabase.rpc().execute.return_value = MagicMock(data={
        "error": "Session belongs to a different customer."
    })
    
    req = EscalationRequest(customer_id=2, session_id="sess_1", client_request_id=req_id)
    res = EscalationService.escalate_session(req)
    
    assert not res.is_success
    assert res.status == "failed"
    assert res.error == "Session belongs to a different customer."
