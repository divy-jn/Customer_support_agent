import pytest
from app.models import WorkflowState, WorkflowStatus, GlobalWorkflowStatus, DomainWorkflowStatus
from app.agents.supervisor import Supervisor, SupervisorAction, SupervisorDecision, OrchestrationDomain, InvalidSupervisorDecisionError

def create_base_state(domain=None, status=WorkflowStatus.IDLE, ticket_id=None) -> WorkflowState:
    legacy_dict = {
        "session_id": "test_session",
        "customer_id": 999,
        "active_domain": domain,
        "workflow_status": status.value if isinstance(status, WorkflowStatus) else status,
        "active_ticket_id": ticket_id,
    }
    
    if domain == "product":
        legacy_dict["product_id"] = 123
        legacy_dict["product_name"] = "Test Product"
    elif domain == "order":
        legacy_dict["order_id"] = 456
        
    state = WorkflowState.from_legacy(legacy_dict)
    
    if status in (WorkflowStatus.IN_PROGRESS, WorkflowStatus.AWAITING_INPUT, WorkflowStatus.SUSPENDED, WorkflowStatus.RESUMED, WorkflowStatus.COMPLETED, WorkflowStatus.FAILED):
        state.global_status = GlobalWorkflowStatus.IN_PROGRESS
    elif status == WorkflowStatus.ESCALATED:
        state.global_status = GlobalWorkflowStatus.ESCALATED
        
    return state

def test_product_workflow_product_message():
    state = create_base_state(domain="product", status=WorkflowStatus.IN_PROGRESS)
    decision = Supervisor.decide("product", "support", state, "my phone is broken")
    assert decision.action == SupervisorAction.CONTINUE
    assert decision.target_domain == OrchestrationDomain.PRODUCT
    
    new_state = Supervisor.apply_decision(state, decision)
    assert new_state.global_status == GlobalWorkflowStatus.IN_PROGRESS
    assert new_state.product_state.domain_status == DomainWorkflowStatus.IN_PROGRESS
    assert new_state.workflow_status == WorkflowStatus.IN_PROGRESS
    
def test_product_workflow_general_conversational():
    state = create_base_state(domain="product", status=WorkflowStatus.AWAITING_INPUT)
    decision = Supervisor.decide("general", "greeting", state, "hi")
    assert decision.action == SupervisorAction.CONTINUE
    assert decision.target_domain == OrchestrationDomain.PRODUCT
    
    new_state = Supervisor.apply_decision(state, decision)
    assert new_state.global_status == GlobalWorkflowStatus.IN_PROGRESS
    assert new_state.product_state.domain_status == DomainWorkflowStatus.AWAITING_INPUT
    assert new_state.workflow_status == WorkflowStatus.AWAITING_INPUT

def test_product_workflow_general_unrelated():
    state = create_base_state(domain="product", status=WorkflowStatus.IN_PROGRESS)
    decision = Supervisor.decide("general", "policy_inquiry", state, "return policy")
    assert decision.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert decision.target_domain == OrchestrationDomain.GENERAL
    
    new_state = Supervisor.apply_decision(state, decision)
    assert new_state.global_status == GlobalWorkflowStatus.IN_PROGRESS
    assert new_state.active_domain == OrchestrationDomain.GENERAL.value
    assert new_state.product_state.domain_status == DomainWorkflowStatus.SUSPENDED
    assert OrchestrationDomain.PRODUCT in new_state.suspended_domains
    
def test_order_workflow_general_conversational():
    state = create_base_state(domain="order", status=WorkflowStatus.IN_PROGRESS)
    decision = Supervisor.decide("general", "small_talk", state, "how are you")
    assert decision.action == SupervisorAction.CONTINUE
    assert decision.target_domain == OrchestrationDomain.ORDER

def test_order_workflow_general_unrelated():
    state = create_base_state(domain="order", status=WorkflowStatus.IN_PROGRESS)
    decision = Supervisor.decide("general", "complaint", state, "i hate this company")
    assert decision.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert decision.target_domain == OrchestrationDomain.GENERAL
    
    new_state = Supervisor.apply_decision(state, decision)
    assert new_state.order_state.domain_status == DomainWorkflowStatus.SUSPENDED

def test_payment_workflow_general_unrelated():
    state = create_base_state(domain="payment", status=WorkflowStatus.AWAITING_INPUT)
    decision = Supervisor.decide("general", "shipping_info", state, "how long to ship")
    assert decision.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert decision.target_domain == OrchestrationDomain.GENERAL

def test_completed_product_general_request():
    state = create_base_state(domain="product", status=WorkflowStatus.COMPLETED)
    decision = Supervisor.decide("general", "policy_inquiry", state, "return policy")
    assert decision.action == SupervisorAction.START_NEW
    assert decision.target_domain == OrchestrationDomain.GENERAL
    
def test_general_active_workflow_product_switch():
    state = create_base_state(domain="general", status=WorkflowStatus.IN_PROGRESS)
    decision = Supervisor.decide("product", "support", state, "phone broken")
    assert decision.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert decision.target_domain == OrchestrationDomain.PRODUCT

def test_product_workflow_order_message():
    state = create_base_state(domain="product", status=WorkflowStatus.IN_PROGRESS)
    decision = Supervisor.decide("order", "track", state, "where is my order")
    assert decision.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert decision.target_domain == OrchestrationDomain.ORDER
    assert decision.transition_metadata is not None
    assert decision.transition_metadata.suspended_domain == OrchestrationDomain.PRODUCT
    
    new_state = Supervisor.apply_decision(state, decision)
    assert new_state.active_domain == OrchestrationDomain.ORDER.value
    assert new_state.product_state.domain_status == DomainWorkflowStatus.SUSPENDED
    assert new_state.order_state.domain_status == DomainWorkflowStatus.IN_PROGRESS
    
def test_resume_suspended_workflow():
    state = create_base_state(domain="product", status=WorkflowStatus.SUSPENDED)
    state.suspended_domains = [OrchestrationDomain.PRODUCT]
    decision = Supervisor.decide("product", "support", state, "back to my broken screen")
    assert decision.action == SupervisorAction.RESUME
    assert decision.target_domain == OrchestrationDomain.PRODUCT
    assert decision.transition_metadata.resumed_domain == OrchestrationDomain.PRODUCT
    
    new_state = Supervisor.apply_decision(state, decision)
    assert new_state.active_domain == "product"
    assert new_state.product_state.domain_status == DomainWorkflowStatus.IN_PROGRESS
    assert OrchestrationDomain.PRODUCT not in new_state.suspended_domains
    
def test_completed_workflow():
    state = create_base_state(domain="product", status=WorkflowStatus.COMPLETED)
    decision = Supervisor.decide("product", "support", state, "another issue")
    assert decision.action == SupervisorAction.START_NEW
    assert decision.target_domain == OrchestrationDomain.PRODUCT
    
    new_state = Supervisor.apply_decision(state, decision)
    assert new_state.product_state.domain_status == DomainWorkflowStatus.IN_PROGRESS
    
def test_escalated_workflow():
    state = create_base_state(domain="product", status=WorkflowStatus.ESCALATED)
    decision = Supervisor.decide("product", "support", state, "help")
    assert decision.action == SupervisorAction.ESCALATE
    
    new_state = Supervisor.apply_decision(state, decision)
    assert new_state.global_status == GlobalWorkflowStatus.ESCALATED
    assert new_state.product_state.domain_status == DomainWorkflowStatus.ESCALATED
    
def test_idle_workflow():
    state = create_base_state()
    decision = Supervisor.decide("product", "support", state, "help")
    assert decision.action == SupervisorAction.START_NEW
    assert decision.target_domain == OrchestrationDomain.PRODUCT
    
def test_invalid_semantic_domain():
    state = create_base_state(domain="product", status=WorkflowStatus.IN_PROGRESS)
    for invalid_domain in ["", "banana", "unknown"]:
        decision = Supervisor.decide(invalid_domain, "", state, "ummm")
        assert decision.action == SupervisorAction.REQUEST_CLARIFICATION
        assert decision.target_domain == OrchestrationDomain.PRODUCT

@pytest.mark.parametrize("status", [
    WorkflowStatus.IN_PROGRESS,
    WorkflowStatus.AWAITING_INPUT,
    WorkflowStatus.SUSPENDED,
    WorkflowStatus.RESUMED,
    WorkflowStatus.ESCALATED,
    WorkflowStatus.COMPLETED,
    WorkflowStatus.IDLE
])
def test_invalid_active_domain_all_statuses(status):
    with pytest.raises(ValueError):
        create_base_state(domain="banana", status=status)

def test_invalid_semantic_and_invalid_workflow_domain():
    with pytest.raises(ValueError):
        create_base_state(domain="banana", status=WorkflowStatus.IN_PROGRESS)
    
def test_apply_decision_clarification():
    state = create_base_state(domain="general", status=WorkflowStatus.IN_PROGRESS)
    decision = SupervisorDecision(
        action=SupervisorAction.REQUEST_CLARIFICATION,
        reason="Test clarification",
        transition_reason="Test clarification",
        target_domain=OrchestrationDomain.GENERAL,
    )
    new_state = Supervisor.apply_decision(state, decision)
    assert new_state.active_domain == OrchestrationDomain.GENERAL
    
def test_ticket_id_does_not_override_switch():
    state = create_base_state(domain="product", status=WorkflowStatus.IN_PROGRESS, ticket_id=999)
    decision = Supervisor.decide("order", "track", state, "where is it")
    assert decision.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert decision.target_domain == OrchestrationDomain.ORDER
    
def test_unknown_invalid_state():
    state = create_base_state(domain="product")
    state.global_status = "UNKNOWN_WEIRD_STATE" 
    decision = Supervisor.decide("product", "support", state, "help")
    assert decision.action == SupervisorAction.REQUEST_CLARIFICATION

def test_deterministic_repeatability():
    state = create_base_state(domain="product", status=WorkflowStatus.IN_PROGRESS)
    d1 = Supervisor.decide("order", "track", state, "where is it")
    d2 = Supervisor.decide("order", "track", state, "where is it")
    assert d1.action == d2.action
    assert d1.target_domain == d2.target_domain

def test_mutation_boundaries():
    state = create_base_state(domain="product", status=WorkflowStatus.IN_PROGRESS, ticket_id=999)
    decision = Supervisor.decide("order", "track", state, "where is it")
    new_state = Supervisor.apply_decision(state, decision)
    
    assert state.active_domain == "product"
    assert state.product_state.domain_status == DomainWorkflowStatus.IN_PROGRESS
    
    assert new_state.active_domain == "order"
    assert new_state.global_status == GlobalWorkflowStatus.IN_PROGRESS
    
    assert new_state.product_state.product_id == 123
    assert new_state.product_state.product_name == "Test Product"
    assert new_state.product_state.active_ticket_id == 999
    assert new_state.customer_id == 999

def test_approval_not_silently_authorized():
    state = create_base_state(domain="product", status=WorkflowStatus.AWAITING_INPUT)
    decision = Supervisor.decide("product", "confirm", state, "yes do it")
    assert decision.action == SupervisorAction.CONTINUE
    assert not hasattr(decision, 'proceed_approval')
