import pytest
from app.models import (
    WorkflowState, ProductState, OrderState, PaymentState,
    OrchestrationDomain, DomainWorkflowStatus
)

def test_product_projection():
    state = WorkflowState(session_id="test")
    state.active_domain = OrchestrationDomain.PRODUCT.value
    state.product_state = ProductState(active_ticket_id=701, product_name="Phone")
    proj = state.to_legacy_projection()
    assert proj["active_ticket_id"] == 701
    assert proj["product_name"] == "Phone"

def test_order_projection():
    state = WorkflowState(session_id="test")
    state.active_domain = OrchestrationDomain.ORDER.value
    state.order_state = OrderState(active_ticket_id=702, order_id="123")
    proj = state.to_legacy_projection()
    assert proj["active_ticket_id"] == 702
    assert proj["order_id"] == 123

def test_payment_projection():
    state = WorkflowState(session_id="test")
    state.active_domain = OrchestrationDomain.PAYMENT.value
    state.payment_state = PaymentState(active_ticket_id=703)
    proj = state.to_legacy_projection()
    assert proj["active_ticket_id"] == 703

def test_domain_switch_projection():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701)
    state.order_state = OrderState(active_ticket_id=702)
    
    state.active_domain = OrchestrationDomain.PRODUCT.value
    proj_prod = state.to_legacy_projection()
    assert proj_prod["active_ticket_id"] == 701
    
    state.active_domain = OrchestrationDomain.ORDER.value
    proj_order = state.to_legacy_projection()
    assert proj_order["active_ticket_id"] == 702

def test_product_ticket_survives_order_projection():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701)
    state.order_state = OrderState(active_ticket_id=702)
    state.active_domain = OrchestrationDomain.ORDER.value
    
    proj = state.to_legacy_projection()
    assert proj["active_ticket_id"] == 702
    assert state.product_state.active_ticket_id == 701

def test_order_ticket_survives_product_projection():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701)
    state.order_state = OrderState(active_ticket_id=702)
    state.active_domain = OrchestrationDomain.PRODUCT.value
    
    proj = state.to_legacy_projection()
    assert proj["active_ticket_id"] == 701
    assert state.order_state.active_ticket_id == 702

def test_payment_ticket_survives_product_projection():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701)
    state.payment_state = PaymentState(active_ticket_id=703)
    state.active_domain = OrchestrationDomain.PRODUCT.value
    
    proj = state.to_legacy_projection()
    assert proj["active_ticket_id"] == 701
    assert state.payment_state.active_ticket_id == 703

def test_ambiguous_ticket_id_without_domain_rejected():
    legacy_dict = {
        "session_id": "test",
        "active_ticket_id": 999
    }
    with pytest.raises(ValueError, match="active_ticket_id present but domain is ambiguous"):
        WorkflowState.from_legacy(legacy_dict)

def test_customer_isolation_remains_unaffected():
    # Customer ID should be preserved in projection and parsing
    legacy_dict = {
        "session_id": "test",
        "customer_id": 12345,
        "active_domain": "product",
        "active_ticket_id": 701
    }
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.customer_id == 12345
    proj = state.to_legacy_projection()
    assert proj["customer_id"] == 12345

def test_serialization_round_trip_preserves_all_domain_ticket_ids():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701)
    state.order_state = OrderState(active_ticket_id=702)
    state.payment_state = PaymentState(active_ticket_id=703)
    state.active_domain = OrchestrationDomain.PRODUCT.value
    
    serialized = state.model_dump_json()
    new_state = WorkflowState.model_validate_json(serialized)
    
    assert new_state.product_state.active_ticket_id == 701
    assert new_state.order_state.active_ticket_id == 702
    assert new_state.payment_state.active_ticket_id == 703

def test_legacy_projection_does_not_mutate_authoritative_domain_states():
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701, product_name="Phone")
    
    proj = state.to_legacy_projection()
    
    assert state.product_state.active_ticket_id == 701
    assert state.product_state.product_name == "Phone"

def test_from_legacy_to_legacy_projection_behaves_deterministically():
    legacy_dict = {
        "session_id": "test",
        "active_domain": "product",
        "active_ticket_id": 701,
        "product_name": "Phone",
        "schema_version": 1
    }
    state = WorkflowState.from_legacy(legacy_dict)
    
    assert state.product_state.active_ticket_id == 701
    assert state.product_state.product_name == "Phone"
    assert state.active_ticket_id is None # Legacy root field should be empty
    assert state.product_name is None # Legacy root field should be empty
    
    proj = state.to_legacy_projection()
    assert proj["active_ticket_id"] == 701
    assert proj["product_name"] == "Phone"
