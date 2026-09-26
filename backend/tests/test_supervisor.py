import pytest
from app.models import WorkflowState, WorkflowStatus
from app.agents.supervisor import Supervisor, SupervisorAction, SupervisorDecision

def create_base_state(domain=None, status=WorkflowStatus.IDLE, ticket_id=None, suspended=None) -> WorkflowState:
    return WorkflowState(
        session_id="test_session",
        active_domain=domain,
        workflow_status=status,
        active_ticket_id=ticket_id,
        suspended_domains=suspended or [],
        product_id=123,
        product_name="Test Product"
    )

def test_product_workflow_product_message():
    state = create_base_state(domain="product", status=WorkflowStatus.IN_PROGRESS)
    decision = Supervisor.decide("product", "support", state, "my phone is broken")
    assert decision.action == SupervisorAction.CONTINUE
    assert decision.target_domain == "product"
    
def test_product_workflow_order_message():
    state = create_base_state(domain="product", status=WorkflowStatus.IN_PROGRESS)
    decision = Supervisor.decide("order", "track", state, "where is my order")
    assert decision.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert decision.target_domain == "order"
    
    new_state = Supervisor.apply_decision(state, decision)
    assert new_state.active_domain == "order"
    assert "product" in new_state.suspended_domains
    
def test_order_workflow_product_message():
    state = create_base_state(domain="order", status=WorkflowStatus.IN_PROGRESS)
    decision = Supervisor.decide("product", "support", state, "broken screen")
    assert decision.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert decision.target_domain == "product"

def test_product_workflow_payment_message():
    state = create_base_state(domain="product", status=WorkflowStatus.AWAITING_INPUT)
    decision = Supervisor.decide("payment", "refund", state, "refund me")
    assert decision.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert decision.target_domain == "payment"
    
def test_resume_suspended_product():
    state = create_base_state(domain="order", status=WorkflowStatus.IN_PROGRESS, suspended=["product"])
    decision = Supervisor.decide("product", "support", state, "back to my broken screen")
    assert decision.action == SupervisorAction.RESUME
    assert decision.target_domain == "product"
    assert decision.resulting_workflow_status == WorkflowStatus.RESUMED
    
    new_state = Supervisor.apply_decision(state, decision)
    assert new_state.active_domain == "product"
    assert "order" in new_state.suspended_domains
    assert "product" not in new_state.suspended_domains
    assert new_state.workflow_status == WorkflowStatus.RESUMED

def test_completed_product_new_product_issue():
    state = create_base_state(domain="product", status=WorkflowStatus.COMPLETED)
    decision = Supervisor.decide("product", "support", state, "another issue")
    assert decision.action == SupervisorAction.START_NEW
    assert decision.target_domain == "product"
    assert decision.resulting_workflow_status == WorkflowStatus.IN_PROGRESS
    
def test_completed_product_order_message():
    state = create_base_state(domain="product", status=WorkflowStatus.COMPLETED)
    decision = Supervisor.decide("order", "track", state, "track order")
    assert decision.action == SupervisorAction.START_NEW
    assert decision.target_domain == "order"
    assert decision.resulting_workflow_status == WorkflowStatus.IN_PROGRESS
    
def test_escalated_workflow():
    state = create_base_state(domain="product", status=WorkflowStatus.ESCALATED)
    decision = Supervisor.decide("product", "support", state, "help")
    assert decision.action == SupervisorAction.ESCALATE
    assert decision.resulting_workflow_status == WorkflowStatus.ESCALATED
    
def test_no_active_workflow_product():
    state = create_base_state()
    decision = Supervisor.decide("product", "support", state, "help")
    assert decision.action == SupervisorAction.START_NEW
    assert decision.target_domain == "product"
    assert decision.resulting_workflow_status == WorkflowStatus.IN_PROGRESS
    
def test_ambiguous_semantic_domain():
    state = create_base_state(domain="product", status=WorkflowStatus.IN_PROGRESS)
    decision = Supervisor.decide("", "", state, "ummm")
    assert decision.action == SupervisorAction.REQUEST_CLARIFICATION
    
def test_ticket_id_does_not_override_switch():
    state = create_base_state(domain="product", status=WorkflowStatus.IN_PROGRESS, ticket_id=999)
    decision = Supervisor.decide("order", "track", state, "where is it")
    assert decision.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert decision.target_domain == "order"
    
def test_unknown_invalid_state():
    # Coerce an invalid state
    state = create_base_state(domain="product")
    state.workflow_status = "UNKNOWN_WEIRD_STATE"
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
