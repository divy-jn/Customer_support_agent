import uuid
import pytest
import concurrent.futures
from unittest.mock import patch, MagicMock
from app.persistence.escalation import ensure_durable_escalation_state, get_durable_escalation_state
from app.models import EscalationLifecycleStatus

@pytest.fixture
def mock_supabase():
    with patch("app.persistence.escalation.supabase") as mock:
        yield mock

def test_get_nonexistent_state(mock_supabase):
    session_id = str(uuid.uuid4())
    mock_supabase.table().select().eq().execute.return_value = MagicMock(data=[])
    
    state = get_durable_escalation_state(session_id, 1)
    assert state is None

def test_ensure_state_creates_none(mock_supabase):
    session_id = str(uuid.uuid4())
    
    # Mock insert success
    mock_supabase.table().insert().execute.return_value = MagicMock()
    
    state = ensure_durable_escalation_state(session_id, 1)
    assert state == EscalationLifecycleStatus.NONE
    
    # Mock subsequent fetch returning NONE with matching customer
    mock_supabase.table().select().eq().execute.return_value = MagicMock(data=[{"status": "NONE", "customer_id": 1}])
    
    fetched = get_durable_escalation_state(session_id, 1)
    assert fetched == EscalationLifecycleStatus.NONE

def test_ensure_state_handles_conflict(mock_supabase):
    session_id = str(uuid.uuid4())
    
    # Mock insert throwing exception (unique constraint violation)
    mock_supabase.table().insert().execute.side_effect = Exception("Unique violation")
    # Mock the subsequent fetch returning the existing state with matching customer
    mock_supabase.table().select().eq().execute.return_value = MagicMock(data=[{"status": "ACTIVE", "customer_id": 1}])
    
    # Ensure should return ACTIVE since the concurrent insert won
    state = ensure_durable_escalation_state(session_id, 1)
    assert state == EscalationLifecycleStatus.ACTIVE

def test_get_durable_escalation_state_found(mock_supabase):
    session_id = str(uuid.uuid4())
    mock_supabase.table().select().eq().execute.return_value = MagicMock(data=[{"status": "RELEASED", "customer_id": 1}])
    
    state = get_durable_escalation_state(session_id, 1)
    assert state == EscalationLifecycleStatus.RELEASED

def test_customer_isolation_fails_closed(mock_supabase):
    session_id = str(uuid.uuid4())
    
    # Existing session belongs to customer 2
    mock_supabase.table().select().eq().execute.return_value = MagicMock(data=[{"status": "ACTIVE", "customer_id": 2}])
    
    with pytest.raises(ValueError, match="belongs to a different customer"):
        get_durable_escalation_state(session_id, 1)
        
    # Same logic applies for ensure
    mock_supabase.table().insert().execute.side_effect = Exception("Unique violation")
    with pytest.raises(ValueError, match="belongs to a different customer"):
        ensure_durable_escalation_state(session_id, 1)
