import pytest
from app.models import (
    WorkflowState, ProductState, OrderState, PaymentState,
    OrchestrationDomain, DomainWorkflowStatus
)



def test_ambiguous_ticket_id_without_domain_rejected():
    legacy_dict = {
        "session_id": "test",
        "active_ticket_id": 999
    }
    with pytest.raises(ValueError, match="active_ticket_id present but domain is ambiguous"):
        WorkflowState.from_legacy(legacy_dict)

def test_customer_isolation_remains_unaffected():
    # Customer ID should be preserved in parsing
    legacy_dict = {
        "session_id": "test",
        "customer_id": 12345,
        "active_domain": "product",
        "active_ticket_id": 701
    }
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.customer_id == 12345
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

def test_from_legacy_behaves_deterministically():
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
    assert not hasattr(state, "active_ticket_id") # Legacy root field should not exist
    assert not hasattr(state, "product_name") # Legacy root field should not exist

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
    
    assert not hasattr(state, "order_id")
    assert not hasattr(state, "last_tool")
    assert not hasattr(state, "last_tool_result")

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
    assert not hasattr(state, "active_ticket_id")

def test_workflow_status_is_correctly_translated():
    legacy_dict = {
        "session_id": "test",
        "active_domain": "product",
        "workflow_status": "completed"
    }
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.product_state.domain_status == DomainWorkflowStatus.COMPLETED


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
    assert not hasattr(new_state, "product_name")
    assert not hasattr(new_state, "last_tool")


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




from app.models import WorkflowState, OrchestrationDomain, DomainWorkflowStatus, GlobalWorkflowStatus
from app.agents.supervisor import Supervisor, SupervisorAction

def test_legacy_suspended_product_migrates_to_f2_representation():
    legacy_dict = {
        "session_id": "test",
        "active_domain": "product",
        "workflow_status": "suspended",
        "product_name": "Phone",
        "active_ticket_id": 99,
        "last_tool": "troubleshoot",
        "last_tool_result": {"tool_name": "troubleshoot", "result": "found issue"}
    }
    state = WorkflowState.from_legacy(legacy_dict)
    
    # 1, 4, 5, 6, 7. Valid F.2 representation of suspended product
    assert state.active_domain == OrchestrationDomain.GENERAL.value
    assert OrchestrationDomain.PRODUCT in state.suspended_domains
    assert state.product_state.domain_status == DomainWorkflowStatus.SUSPENDED
    assert state.product_state.product_name == "Phone"
    assert state.product_state.active_ticket_id == 99
    assert state.product_state.last_tool == "troubleshoot"
    assert state.product_state.last_tool_result.result == "found issue" if getattr(state.product_state.last_tool_result, "result", None) else state.product_state.last_tool_result == "found issue" or (isinstance(state.product_state.last_tool_result, dict) and state.product_state.last_tool_result["result"] == "found issue")
    assert state.global_status == GlobalWorkflowStatus.IN_PROGRESS
    
    # 10. no leakage
    assert state.order_state is None
    assert state.payment_state is None
    
    # 8. persistence round-trip
    dumped = state.model_dump_json()
    rehydrated = WorkflowState.model_validate_json(dumped)
    assert OrchestrationDomain.PRODUCT in rehydrated.suspended_domains
    assert rehydrated.active_domain == OrchestrationDomain.GENERAL.value
    assert rehydrated.product_state.product_name == "Phone"

def test_legacy_suspended_order_migrates_to_f2_representation():
    legacy_dict = {
        "session_id": "test",
        "active_domain": "order",
        "workflow_status": "suspended",
        "order_id": 456
    }
    state = WorkflowState.from_legacy(legacy_dict)
    
    # 2.
    assert state.active_domain == OrchestrationDomain.GENERAL.value
    assert OrchestrationDomain.ORDER in state.suspended_domains
    assert state.order_state.domain_status == DomainWorkflowStatus.SUSPENDED
    assert state.order_state.order_id == "456"
    assert state.product_state is None

def test_legacy_suspended_payment_migrates_to_f2_representation():
    legacy_dict = {
        "session_id": "test",
        "active_domain": "payment",
        "workflow_status": "suspended",
        "active_ticket_id": 88
    }
    state = WorkflowState.from_legacy(legacy_dict)
    
    # 3.
    assert state.active_domain == OrchestrationDomain.GENERAL.value
    assert OrchestrationDomain.PAYMENT in state.suspended_domains
    assert state.payment_state.domain_status == DomainWorkflowStatus.SUSPENDED
    assert state.payment_state.active_ticket_id == 88

def test_legacy_suspended_resumable_by_supervisor():
    legacy_dict = {
        "session_id": "test",
        "active_domain": "product",
        "workflow_status": "suspended",
        "product_name": "Phone"
    }
    state = WorkflowState.from_legacy(legacy_dict)
    
    # 9. Supervisor integration - customer asks about product again
    decision = Supervisor.decide(
        semantic_domain="product",
        semantic_intent="product_inquiry",
        state=state,
        message="What about my Phone?"
    )
    
    # Should RESUME the suspended PRODUCT workflow
    assert decision.action == SupervisorAction.RESUME
    assert decision.target_domain == OrchestrationDomain.PRODUCT
    assert decision.transition_metadata.resumed_domain == OrchestrationDomain.PRODUCT


def test_legacy_status_translation_idle():
    legacy_dict = {"session_id": "test", "workflow_status": "idle"}
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.global_status == GlobalWorkflowStatus.IDLE

def test_mixed_payload_product_with_order_id_fails():
    with pytest.raises(ValueError, match="Mixed typed/legacy payload detected"):
        WorkflowState.from_legacy({
            "session_id": "test",
            "active_domain": "product",
            "product_state": {"domain_status": "in_progress"},
            "order_id": 123
        })

def test_mixed_payload_payment_with_product_id_fails():
    with pytest.raises(ValueError, match="Mixed typed/legacy payload detected"):
        WorkflowState.from_legacy({
            "session_id": "test",
            "active_domain": "payment",
            "payment_state": {"domain_status": "in_progress"},
            "product_id": 123
        })

def test_mixed_payload_product_with_active_ticket_id_fails():
    with pytest.raises(ValueError, match="Mixed typed/legacy payload detected"):
        WorkflowState.from_legacy({
            "session_id": "test",
            "active_domain": "product",
            "product_state": {"domain_status": "in_progress"},
            "active_ticket_id": 999
        })

def test_mixed_payload_product_with_legacy_suspended_fails():
    with pytest.raises(ValueError, match="Mixed typed/legacy payload detected"):
        WorkflowState.from_legacy({
            "session_id": "test",
            "active_domain": "product",
            "product_state": {"domain_status": "in_progress"},
            "workflow_status": "suspended"
        })

def test_mixed_payload_order_with_legacy_last_tool_fails():
    with pytest.raises(ValueError, match="Mixed typed/legacy payload detected"):
        WorkflowState.from_legacy({
            "session_id": "test",
            "active_domain": "order",
            "order_state": {"domain_status": "in_progress"},
            "last_tool": "track_order"
        })

def test_mixed_payload_product_with_legacy_product_name_fails():
    with pytest.raises(ValueError, match="Mixed typed/legacy payload detected"):
        WorkflowState.from_legacy({
            "session_id": "test",
            "active_domain": "product",
            "product_state": {"domain_status": "in_progress"},
            "product_name": "Phone"
        })

def test_clean_modern_payload_succeeds():
    state = WorkflowState.from_legacy({
        "session_id": "test",
        "product_state": {
            "product_name": "Phone",
            "domain_status": "in_progress"
        }
    })
    assert state.product_state.product_name == "Phone"
