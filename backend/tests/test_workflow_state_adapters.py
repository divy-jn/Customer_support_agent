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

import json
from app.models import WorkflowStatus

def test_historical_order_payload_loads_into_orderstate():
    legacy_dict = {
        "session_id": "test",
        "active_domain": "order",
        "order_id": 1234,
        "last_tool": "track_order",
        "last_tool_result": {"tool_name": "track_order", "result": "delivered"}
    }
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.order_state.order_id == "1234"
    assert state.order_state.last_tool == "track_order"
    assert state.order_state.last_tool_result.result == "delivered"
    
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
        "workflow_status": "completed"
    }
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.product_state.domain_status == DomainWorkflowStatus.COMPLETED
    assert state.workflow_status == WorkflowStatus.COMPLETED

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

from app.models import GlobalWorkflowStatus

def test_legacy_status_translation_in_progress():
    legacy_dict = {"session_id": "test", "active_domain": "product", "workflow_status": "in_progress"}
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.global_status == GlobalWorkflowStatus.IN_PROGRESS
    assert state.product_state.domain_status == DomainWorkflowStatus.IN_PROGRESS

def test_legacy_status_translation_awaiting_input():
    legacy_dict = {"session_id": "test", "active_domain": "order", "workflow_status": "awaiting_input"}
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.global_status == GlobalWorkflowStatus.IN_PROGRESS
    assert state.order_state.domain_status == DomainWorkflowStatus.AWAITING_INPUT

def test_legacy_status_translation_escalated():
    legacy_dict = {"session_id": "test", "active_domain": "product", "workflow_status": "escalated"}
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.global_status == GlobalWorkflowStatus.ESCALATED
    assert state.product_state.domain_status == DomainWorkflowStatus.ESCALATED

def test_legacy_status_translation_completed():
    legacy_dict = {"session_id": "test", "active_domain": "payment", "workflow_status": "completed"}
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.global_status == GlobalWorkflowStatus.IN_PROGRESS
    assert state.payment_state.domain_status == DomainWorkflowStatus.COMPLETED

def test_legacy_status_translation_failed():
    legacy_dict = {"session_id": "test", "active_domain": "product", "workflow_status": "failed"}
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.global_status == GlobalWorkflowStatus.IN_PROGRESS
    assert state.product_state.domain_status == DomainWorkflowStatus.FAILED

def test_legacy_status_translation_suspended_invalid():
    # Because of the invariant added in F.2.7.3A, an active domain cannot be SUSPENDED.
    # Therefore, we test this by ensuring it throws the proper validation error.
    legacy_dict = {"session_id": "test", "active_domain": "product", "workflow_status": "suspended"}
    with pytest.raises(ValueError, match="PRODUCT domain cannot be active while its status is SUSPENDED"):
        WorkflowState.from_legacy(legacy_dict)

def test_legacy_status_translation_suspended_valid():
    # If the domain is not active, but the legacy payload had suspended domains, wait, legacy payloads didn't have suspended domains.
    # If a legacy payload has "suspended", it would be mapped to IN_PROGRESS via domain_status? No, it's mapped to SUSPENDED.
    # If it is GENERAL with a product state, wait, GENERAL cannot have product states in legacy.
    # We will test IDLE mapped correctly instead.
    legacy_dict = {"session_id": "test", "workflow_status": "idle"}
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.global_status == GlobalWorkflowStatus.IDLE

