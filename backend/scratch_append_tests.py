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
        "last_tool_result": "found issue"
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
