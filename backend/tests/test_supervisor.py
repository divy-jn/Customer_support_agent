import pytest
from app.models import WorkflowState, WorkflowStatus
from app.agents.supervisor import Supervisor, SupervisorAction, SupervisorDecision, OrchestrationDomain

def create_base_state(domain=None, status=WorkflowStatus.IDLE, ticket_id=None) -> WorkflowState:
    return WorkflowState(
        session_id="test_session",
        customer_id=999,
        active_domain=domain,
        workflow_status=status,
        active_ticket_id=ticket_id,
        product_id=123,
        product_name="Test Product"
    )

def test_product_workflow_product_message():
    state = create_base_state(domain="product", status=WorkflowStatus.IN_PROGRESS)
    decision = Supervisor.decide("product", "support", state, "my phone is broken")
    assert decision.action == SupervisorAction.CONTINUE
    assert decision.target_domain == OrchestrationDomain.PRODUCT
    assert decision.resulting_workflow_status == WorkflowStatus.IN_PROGRESS
    
def test_product_workflow_general_message():
    state = create_base_state(domain="product", status=WorkflowStatus.AWAITING_INPUT)
    decision = Supervisor.decide("general", "hello", state, "hi")
    assert decision.action == SupervisorAction.CONTINUE
    assert decision.target_domain == OrchestrationDomain.PRODUCT
    assert decision.resulting_workflow_status == WorkflowStatus.AWAITING_INPUT

def test_product_workflow_order_message():
    state = create_base_state(domain="product", status=WorkflowStatus.IN_PROGRESS)
    decision = Supervisor.decide("order", "track", state, "where is my order")
    assert decision.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert decision.target_domain == OrchestrationDomain.ORDER
    assert decision.resulting_workflow_status == WorkflowStatus.IN_PROGRESS
    assert decision.transition_metadata is not None
    assert decision.transition_metadata.suspended_domain == OrchestrationDomain.PRODUCT
    
def test_resume_suspended_workflow():
    state = create_base_state(domain="product", status=WorkflowStatus.SUSPENDED)
    decision = Supervisor.decide("product", "support", state, "back to my broken screen")
    assert decision.action == SupervisorAction.RESUME
    assert decision.target_domain == OrchestrationDomain.PRODUCT
    assert decision.resulting_workflow_status == WorkflowStatus.RESUMED
    assert decision.transition_metadata.resumed_domain == OrchestrationDomain.PRODUCT
    
    new_state = Supervisor.apply_decision(state, decision)
    assert new_state.active_domain == "product"
    assert new_state.workflow_status == WorkflowStatus.RESUMED

def test_resumed_to_in_progress():
    state = create_base_state(domain="product", status=WorkflowStatus.RESUMED)
    decision = Supervisor.decide("product", "support", state, "next steps")
    assert decision.action == SupervisorAction.CONTINUE
    assert decision.resulting_workflow_status == WorkflowStatus.IN_PROGRESS

def test_completed_workflow():
    state = create_base_state(domain="product", status=WorkflowStatus.COMPLETED)
    decision = Supervisor.decide("product", "support", state, "another issue")
    assert decision.action == SupervisorAction.START_NEW
    assert decision.target_domain == OrchestrationDomain.PRODUCT
    assert decision.resulting_workflow_status == WorkflowStatus.IN_PROGRESS
    
def test_escalated_workflow():
    state = create_base_state(domain="product", status=WorkflowStatus.ESCALATED)
    decision = Supervisor.decide("product", "support", state, "help")
    assert decision.action == SupervisorAction.ESCALATE
    assert decision.resulting_workflow_status == WorkflowStatus.ESCALATED
    
def test_idle_workflow():
    state = create_base_state()
    decision = Supervisor.decide("product", "support", state, "help")
    assert decision.action == SupervisorAction.START_NEW
    assert decision.target_domain == OrchestrationDomain.PRODUCT
    assert decision.resulting_workflow_status == WorkflowStatus.IN_PROGRESS
    
def test_invalid_semantic_domain():
    state = create_base_state(domain="product", status=WorkflowStatus.IN_PROGRESS)
    
    for invalid_domain in ["", "banana", "unknown"]:
        decision = Supervisor.decide(invalid_domain, "", state, "ummm")
        assert decision.action == SupervisorAction.REQUEST_CLARIFICATION
        assert decision.target_domain == OrchestrationDomain.PRODUCT
        assert decision.resulting_workflow_status == WorkflowStatus.IN_PROGRESS

# --- INVALID active_domain TESTS ---
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
    state = create_base_state(domain="banana", status=status)
    decision = Supervisor.decide("product", "support", state, "hello")
    assert decision.action == SupervisorAction.REQUEST_CLARIFICATION
    assert decision.target_domain == OrchestrationDomain.GENERAL
    assert decision.resulting_workflow_status == WorkflowStatus.IDLE

def test_invalid_semantic_and_invalid_workflow_domain():
    state = create_base_state(domain="banana", status=WorkflowStatus.IN_PROGRESS)
    decision = Supervisor.decide("apple", "support", state, "hello")
    assert decision.action == SupervisorAction.REQUEST_CLARIFICATION
    assert decision.target_domain == OrchestrationDomain.GENERAL
    assert decision.resulting_workflow_status == WorkflowStatus.IDLE

# --- BOUNDARIES ---
def test_ticket_id_does_not_override_switch():
    state = create_base_state(domain="product", status=WorkflowStatus.IN_PROGRESS, ticket_id=999)
    decision = Supervisor.decide("order", "track", state, "where is it")
    assert decision.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert decision.target_domain == OrchestrationDomain.ORDER
    
def test_unknown_invalid_state():
    state = create_base_state(domain="product")
    state.workflow_status = "UNKNOWN_WEIRD_STATE"  # Bypass enum type check for test
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
    
    # Assert old state unchanged
    assert state.active_domain == "product"
    assert state.workflow_status == WorkflowStatus.IN_PROGRESS
    
    # Assert new state metadata changed
    assert new_state.active_domain == "order"
    assert new_state.workflow_status == WorkflowStatus.IN_PROGRESS
    
    # Assert domain facts MUST NOT mutate
    assert new_state.product_id == 123
    assert new_state.product_name == "Test Product"
    assert new_state.active_ticket_id == 999
    assert new_state.customer_id == 999

def test_approval_not_silently_authorized():
    state = create_base_state(domain="product", status=WorkflowStatus.AWAITING_INPUT)
    decision = Supervisor.decide("product", "confirm", state, "yes do it")
    assert decision.action == SupervisorAction.CONTINUE
    assert decision.resulting_workflow_status == WorkflowStatus.AWAITING_INPUT
    assert not hasattr(decision, 'proceed_approval')
