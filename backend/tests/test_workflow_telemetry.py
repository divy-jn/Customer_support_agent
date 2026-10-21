import logging

from app.middleware.tracking import emit_workflow_turn


def test_workflow_turn_logs_only_bounded_operational_fields(caplog):
    with caplog.at_level(logging.INFO, logger="tracking"):
        emit_workflow_turn({
            "session_id": "session-1", "domain": "order", "guardian_action": "resolve",
            "message": "private customer text must not be logged", "tool_payload": {"secret": "x"},
        })
    record = caplog.records[-1]
    assert record.message == "workflow_turn"
    assert record.session_id == "session-1"
    assert record.domain == "order"
    assert not hasattr(record, "message") or record.getMessage() == "workflow_turn"
    assert not hasattr(record, "tool_payload")
