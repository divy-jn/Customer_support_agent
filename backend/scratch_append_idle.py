
def test_legacy_status_translation_idle():
    legacy_dict = {"session_id": "test", "workflow_status": "idle"}
    state = WorkflowState.from_legacy(legacy_dict)
    assert state.global_status == GlobalWorkflowStatus.IDLE
