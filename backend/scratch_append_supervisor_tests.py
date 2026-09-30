from app.models import GlobalWorkflowStatus

def test_legacy_integration_not_idle():
    legacy_dict = {
        "session_id": "test",
        "active_domain": "product",
        "workflow_status": "in_progress"
    }
    state = WorkflowState.from_legacy(legacy_dict)
    
    # Prove it's not IDLE
    assert state.global_status == GlobalWorkflowStatus.IN_PROGRESS
    
    # Pass to supervisor
    decision = Supervisor.decide(
        semantic_domain="general",
        semantic_intent="general_chat",
        state=state,
        message="Hello"
    )
    
    # Since active is PRODUCT (in_progress) and we get a general conversational message,
    # it should SUSPEND the product workflow, not return IDLE response.
    assert decision.action == SupervisorAction.SUSPEND_AND_SWITCH
    assert decision.target_domain == OrchestrationDomain.GENERAL
    assert decision.transition_metadata.suspended_domain == OrchestrationDomain.PRODUCT
