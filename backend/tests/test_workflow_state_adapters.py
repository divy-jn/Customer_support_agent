import pytest
from pydantic import ValidationError
from app.models import (
    WorkflowState, 
    ProductState, 
    OrderState, 
    PaymentState,
    OrchestrationDomain, 
    GlobalWorkflowStatus, 
    DomainWorkflowStatus,
    WorkflowStatus,
    ToolResultEnvelope
)

def test_legacy_product_state():
    """A. legacy product state -> from_legacy() -> ProductState populated correctly"""
    legacy = {
        "session_id": "sess_1",
        "semantic_intent": "product_inquiry",
        "product_id": 100,
        "product_name": "Phone",
        "manufacturer": "Apple",
        "active_ticket_id": 999,
        "workflow_status": "in_progress"
    }
    state = WorkflowState.from_legacy(legacy)
    assert state.active_domain == OrchestrationDomain.PRODUCT
    assert state.product_state is not None
    assert state.product_state.product_id == 100
    assert state.product_state.product_name == "Phone"
    assert state.product_state.manufacturer == "Apple"
    assert state.product_state.active_ticket_id == 999
    assert state.product_state.domain_status == DomainWorkflowStatus.IN_PROGRESS
    
    assert state.order_state is None
    assert state.payment_state is None

def test_legacy_order_state():
    """B. legacy order state -> from_legacy() -> OrderState populated correctly"""
    legacy = {
        "session_id": "sess_2",
        "skill_name": "order_lookup",
        "order_id": 555,
        "active_ticket_id": 888
    }
    state = WorkflowState.from_legacy(legacy)
    assert state.active_domain == OrchestrationDomain.ORDER
    assert state.order_state is not None
    assert state.order_state.order_id == "555"
    assert state.order_state.active_ticket_id == 888

def test_legacy_payment_state():
    """C. legacy payment state -> from_legacy() -> PaymentState populated correctly"""
    legacy = {
        "session_id": "sess_3",
        "semantic_intent": "refund_request",
        "active_ticket_id": 777
    }
    state = WorkflowState.from_legacy(legacy)
    assert state.active_domain == OrchestrationDomain.PAYMENT
    assert state.payment_state is not None
    assert state.payment_state.active_ticket_id == 777

def test_to_legacy_projection_product():
    """D. product state -> to_legacy_projection() -> legacy product fields preserved"""
    state = WorkflowState(
        session_id="sess_4",
        active_domain=OrchestrationDomain.PRODUCT,
        product_state=ProductState(
            product_id=101,
            product_name="Tablet",
            manufacturer="Samsung",
            active_ticket_id=123
        )
    )
    proj = state.to_legacy_projection()
    assert proj["product_id"] == 101
    assert proj["product_name"] == "Tablet"
    assert proj["manufacturer"] == "Samsung"
    assert proj["active_ticket_id"] == 123
    assert proj["order_id"] is None

def test_to_legacy_projection_order():
    """E. order state -> to_legacy_projection() -> legacy order fields preserved"""
    state = WorkflowState(
        session_id="sess_5",
        active_domain=OrchestrationDomain.ORDER,
        order_state=OrderState(
            order_id="1002",
            active_ticket_id=124
        )
    )
    proj = state.to_legacy_projection()
    assert proj["order_id"] == 1002
    assert proj["active_ticket_id"] == 124
    assert proj["product_name"] is None

def test_to_legacy_projection_payment():
    """F. payment state -> to_legacy_projection() -> legacy payment fields preserved"""
    state = WorkflowState(
        session_id="sess_6",
        active_domain=OrchestrationDomain.PAYMENT,
        payment_state=PaymentState(
            active_ticket_id=125
        )
    )
    proj = state.to_legacy_projection()
    assert proj["active_ticket_id"] == 125

def test_round_trip():
    """G. valid round trip: legacy -> new -> legacy"""
    legacy = {
        "session_id": "sess_7",
        "customer_id": 99,
        "schema_version": 1,
        "state_revision": 5,
        "active_domain": "order",
        "semantic_intent": "order_status",
        "skill_name": "order_lookup",
        "skill_version": "1.0",
        "last_tool": "check_db",
        "workflow_status": "suspended",
        "pending_input": "need order ID",
        "turn_count": 3,
        "active_ticket_id": 500,
        "product_id": None,
        "product_name": None,
        "order_id": 2005,
        "manufacturer": None
    }
    state = WorkflowState.from_legacy(legacy)
    proj = state.to_legacy_projection()
    
    for k, v in legacy.items():
        assert proj.get(k) == v

def test_ambiguous_domain_rejected():
    """H. ambiguous domain -> rejected"""
    legacy = {
        "session_id": "sess_8",
        # Ambiguous because product_id and order_id are both present but no active_domain
        "product_id": 100,
        "order_id": 200
    }
    with pytest.raises(ValueError) as excinfo:
        WorkflowState.from_legacy(legacy)
    assert "Ambiguous domain" in str(excinfo.value)

def test_cross_domain_ticket_leakage():
    """I. cross-domain ticket leakage attempt -> rejected"""
    legacy = {
        "session_id": "sess_9",
        "active_ticket_id": 111,
        # General domain with no facts should not have a ticket!
        "semantic_intent": "greeting"
    }
    with pytest.raises(ValueError) as excinfo:
        WorkflowState.from_legacy(legacy)
    assert "active_ticket_id present but domain is ambiguous or general" in str(excinfo.value)

def test_malformed_legacy_data():
    """J. malformed legacy data -> rejected"""
    legacy = {
        "session_id": "sess_10",
        "active_domain": "not_a_valid_domain"
    }
    with pytest.raises(ValueError) as excinfo:
        WorkflowState.from_legacy(legacy)
    assert "Invalid active_domain" in str(excinfo.value)

def test_unsupported_schema_version():
    """K. unsupported schema version -> rejected"""
    legacy = {
        "session_id": "sess_11",
        "schema_version": 2
    }
    with pytest.raises(ValueError) as excinfo:
        WorkflowState.from_legacy(legacy)
    assert "Unsupported legacy schema_version" in str(excinfo.value)

def test_oversized_bounded_field():
    """L. oversized bounded field -> rejected"""
    legacy = {
        "session_id": "sess_12",
        "semantic_intent": "product_inquiry",
        "product_name": "x" * 300
    }
    with pytest.raises(ValidationError):
        WorkflowState.from_legacy(legacy)

def test_status_preservation():
    """M, N, O. completed, suspended, escalated states preserved"""
    for status, expected in [
        ("completed", DomainWorkflowStatus.COMPLETED),
        ("suspended", DomainWorkflowStatus.SUSPENDED),
        ("escalated", DomainWorkflowStatus.ESCALATED)
    ]:
        legacy = {
            "session_id": "sess_status",
            "semantic_intent": "product_inquiry",
            "workflow_status": status
        }
        state = WorkflowState.from_legacy(legacy)
        assert state.product_state.domain_status == expected
        proj = state.to_legacy_projection()
        assert proj["workflow_status"] == status

def test_state_revision_preserved():
    """P. state_revision preserved"""
    legacy = {
        "session_id": "sess_14",
        "state_revision": 42
    }
    state = WorkflowState.from_legacy(legacy)
    assert state.state_revision == 42
    proj = state.to_legacy_projection()
    assert proj["state_revision"] == 42

def test_schema_version_current():
    """Q. schema_version becomes current supported version after from_legacy()"""
    # Even if missing in legacy dict, it should be set to 1
    legacy = {
        "session_id": "sess_15"
    }
    state = WorkflowState.from_legacy(legacy)
    assert state.schema_version == 1
    proj = state.to_legacy_projection()
    assert proj["schema_version"] == 1
