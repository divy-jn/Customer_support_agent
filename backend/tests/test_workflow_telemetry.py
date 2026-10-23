import logging

from app.middleware.tracking import emit_workflow_turn


def test_workflow_turn_logs_only_bounded_operational_fields(caplog):
    with caplog.at_level(logging.INFO, logger="tracking"):
        emit_workflow_turn({
            "session_id": "session-1", "domain": "order", "guardian_action": "resolve",
            "message": "private customer text must not be logged", "tool_payload": {"secret": "x"},
            "access_token": "bearer-secret-token", "password": "super-secret-password",
        })
    record = caplog.records[-1]
    assert record.message == "workflow_turn"
    assert record.session_id == "session-1"
    assert record.domain == "order"
    assert not hasattr(record, "tool_payload")
    assert not hasattr(record, "access_token")
    assert not hasattr(record, "password")


def test_workflow_turn_records_all_v2_operational_dimensions(caplog):
    with caplog.at_level(logging.INFO, logger="tracking"):
        emit_workflow_turn({
            "session_id": "sess-v2-42",
            "customer_id": 7,
            "architecture_mode": "v2_core",
            "domain": "product",
            "intent": "product_warranty",
            "supervisor_action": "continue",
            "supervisor_reason": "Customer inquiry matches active product skill.",
            "guardian_action": "update_existing_ticket",
            "guardian_reason": "An active ticket already covers this domain and intent; do not create a duplicate.",
            "ticket_id": 786,
            "guardian_ticket_id": 786,
            "pending_approval": False,
            "escalated": False,
            "latency_ms": 124.5,
            "outcome": "response_generated",
        })
    record = caplog.records[-1]
    assert record.message == "workflow_turn"
    assert record.session_id == "sess-v2-42"
    assert record.customer_id == 7
    assert record.architecture_mode == "v2_core"
    assert record.domain == "product"
    assert record.intent == "product_warranty"
    assert record.supervisor_action == "continue"
    assert record.supervisor_reason == "Customer inquiry matches active product skill."
    assert record.guardian_action == "update_existing_ticket"
    assert "duplicate" in record.guardian_reason
    assert record.ticket_id == 786
    assert record.guardian_ticket_id == 786
    assert record.pending_approval is False
    assert record.escalated is False
    assert record.latency_ms == 124.5
    assert record.outcome == "response_generated"


def test_workflow_turn_records_failure_and_timeout_outcomes(caplog):
    with caplog.at_level(logging.INFO, logger="tracking"):
        emit_workflow_turn({
            "session_id": "sess-err-99",
            "customer_id": 12,
            "architecture_mode": "v2_all",
            "escalated": False,
            "latency_ms": 30005.2,
            "outcome": "timeout",
        })
        emit_workflow_turn({
            "session_id": "sess-err-100",
            "customer_id": 12,
            "architecture_mode": "v2_all",
            "escalated": False,
            "latency_ms": 45.1,
            "outcome": "error",
        })
    assert len(caplog.records) == 2
    assert caplog.records[0].outcome == "timeout"
    assert caplog.records[0].latency_ms == 30005.2
    assert caplog.records[1].outcome == "error"
    assert caplog.records[1].latency_ms == 45.1
