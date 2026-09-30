import json
from app.models import WorkflowStatus

def test_historical_order_payload_loads_into_orderstate():
    legacy_dict = {
        "session_id": "test",
        "active_domain": "order",
        "order_id": 1234,
        "last_tool": "track_order",
        "last_tool_result": "delivered"
    }
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.order_state.order_id == "1234"
    assert state.order_state.last_tool == "track_order"
    assert state.order_state.last_tool_result == "delivered"
    
    assert state.order_id is None
    assert state.last_tool is None
    assert state.last_tool_result is None

def test_historical_payment_payload_loads_into_paymentstate():
    legacy_dict = {
        "session_id": "test",
        "active_domain": "payment",
        "active_ticket_id": 55,
        "last_tool": "process_refund"
    }
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.payment_state.active_ticket_id == 55
    assert state.payment_state.last_tool == "process_refund"
    assert state.active_ticket_id is None

def test_workflow_status_is_correctly_translated():
    legacy_dict = {
        "session_id": "test",
        "active_domain": "product",
        "workflow_status": "suspended"
    }
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.product_state.domain_status == DomainWorkflowStatus.SUSPENDED
    assert state.workflow_status == WorkflowStatus.SUSPENDED

def test_contradictory_facts_fail():
    legacy_dict = {
        "session_id": "test",
        "product_id": 1,
        "order_id": 2
    }
    with pytest.raises(ValueError, match="Contradictory cross-domain facts"):
        WorkflowState.from_legacy(legacy_dict)

def test_invalid_active_domain_fails():
    legacy_dict = {
        "session_id": "test",
        "active_domain": "invalid_domain"
    }
    with pytest.raises(ValueError, match="Invalid active_domain: invalid_domain"):
        WorkflowState.from_legacy(legacy_dict)

def test_old_payload_roundtrips_preserves_typed_state():
    legacy_dict = {
        "session_id": "test",
        "active_domain": "product",
        "product_name": "Phone",
        "last_tool": "search"
    }
    state = WorkflowState.from_legacy(legacy_dict)
    json_str = state.model_dump_json()
    new_state = WorkflowState.model_validate_json(json_str)
    
    assert new_state.product_state.product_name == "Phone"
    assert new_state.product_state.last_tool == "search"
    assert new_state.product_name is None
    assert new_state.last_tool is None

def test_already_typed_f2_payload_uses_native_deserialization():
    typed_dict = {
        "session_id": "test",
        "product_state": {
            "product_name": "Phone",
            "domain_status": "in_progress"
        },
        "order_id": 999  # Legacy root field to test it doesn't leak into typed state
    }
    state = WorkflowState.from_legacy(typed_dict)
    assert state.product_state.product_name == "Phone"
    assert state.order_state is None # Does not implicitly create OrderState from root order_id because it used model_validate directly
    
def test_cross_domain_leakage_prevented_in_translation():
    legacy_dict = {
        "session_id": "test",
        "active_domain": "order",
        "order_id": 123
    }
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.order_state.order_id == "123"
    assert state.product_state is None
    assert state.payment_state is None
