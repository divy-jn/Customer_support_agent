import json
from pathlib import Path
from unittest.mock import MagicMock, Mock
from uuid import uuid4, uuid5

import pytest

from app import tools
from app.agents import graph, payment_agent
from app.escalation import service
from app.outbox import worker
from app.websocket import chat_handler


REFUND_REVIEW = "Refund processing is unavailable; payment specialist review required"


@pytest.mark.asyncio
async def test_compound_handoff_replay_uses_same_child_key_and_config_is_not_hashed(monkeypatch):
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {"status": "success"}
    monkeypatch.setattr(service, "supabase", db)
    turn_id = uuid4()
    result = {
        "guardian_action": "create_ticket", "guardian_ticket_id": 42,
        "workflow_state": {"active_domain": "product", "product_state": {}},
    }
    for email in ("support@one.company", "support@two.company"):
        monkeypatch.setattr(service.settings, "support_team_email", email)
        assert await chat_handler._confirm_graph_handoff(
            result, "session-1", 7, str(turn_id), "Help"
        ) == (True, None)

    first, second = [call.args[1] for call in db.rpc.call_args_list]
    child = str(uuid5(turn_id, "escalate_session:session-1"))
    assert child != str(turn_id)
    assert first["p_client_request_id"] == second["p_client_request_id"] == child
    assert first["p_payload_hash"] == second["p_payload_hash"]
    assert first["p_payload"]["ticket_id"] == second["p_payload"]["ticket_id"] == 42
    assert first["p_payload"]["support_team_email"] == "support@one.company"
    assert second["p_payload"]["support_team_email"] == "support@two.company"
    db.table.assert_not_called()


@pytest.mark.parametrize(("tool_name", "status", "terminal"), [
    ("cancel_order", "cancelled", {"status": "success", "message": "Order #5 has been cancelled"}),
    ("process_refund", "refunded", {"error": REFUND_REVIEW}),
])
def test_order_replay_reaches_rpc_after_status_changes(monkeypatch, tool_name, status, terminal):
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"customer_id": 7, "status": status}
    ]
    db.rpc.return_value.execute.return_value.data = terminal
    monkeypatch.setattr(tools, "supabase", db)
    request_id = str(uuid4())
    assert json.loads(getattr(tools, tool_name)(5, 7, request_id)) == terminal
    assert db.rpc.call_args.args[1]["p_client_request_id"] == request_id
    assert db.rpc.call_args.args[1]["p_operation_type"] == tool_name


def test_historical_order_flag_cannot_confirm_refund(monkeypatch):
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"customer_id": 7, "status": "refunded"}
    ]
    db.rpc.return_value.execute.return_value.data = {
        "status": "success", "message": "The refund will be processed within 5-7 business days."
    }
    monkeypatch.setattr(tools, "supabase", db)
    result = json.loads(tools.process_refund(5, 7, str(uuid4())))
    assert result == {"error": "Refund could not be verified; payment specialist review required"}
    db.rpc.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(("rows", "error"), [
    ([], "No verified payment record is available"),
    ([{"customer_id": 8, "status": "captured"}], "No verified payment record is available"),
    (None, "Payment verification is temporarily unavailable"),
])
async def test_payment_lookup_failure_goes_to_specialist_without_order_fallback(monkeypatch, rows, error):
    db = MagicMock()
    query = db.table.return_value.select.return_value.eq.return_value.limit.return_value
    if rows is None:
        query.execute.side_effect = TimeoutError("ledger unavailable")
    else:
        query.execute.return_value.data = rows
    monkeypatch.setattr(tools, "supabase", db)
    assert json.loads(tools.get_payment_record(5, 7)) == {"error": error}
    db.table.assert_called_once_with("payment_records")

    result = await payment_agent.handle_payment_request({
        "message": "Charged twice for order #5", "intent": "billing_issue", "customer_id": 7,
    })
    assert result["requires_human"]
    assert "specialist" in result["response"]
    assert "captured" not in result["response"]
    assert "tool_results" not in result


def test_owned_payment_lookup_preserves_ledger_result(monkeypatch):
    db = MagicMock()
    row = {"customer_id": 7, "status": "authorized", "currency": "INR"}
    db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [row]
    monkeypatch.setattr(tools, "supabase", db)
    assert json.loads(tools.get_payment_record(5, 7)) == row


@pytest.mark.asyncio
async def test_legacy_refund_routes_to_handoff_without_approval(monkeypatch):
    monkeypatch.setattr(graph.db_agent, "determine_db_action", Mock(return_value={
        "action": "process_refund", "params": {"order_id": 5},
    }))
    result = await graph.db_plan_node({"customer_id": 7, "message": "Refund order #5", "intent": "refund"})
    assert result["escalated"]
    assert result["pending_approval"] is None
    assert "specialist" in result["response"]


def test_ticket_create_replay_only_calls_transaction(monkeypatch):
    import app.email_service

    inline = Mock()
    monkeypatch.setattr(app.email_service, "send_ticket_created_email", inline)
    db = MagicMock()
    db.rpc.return_value.execute.return_value.data = {"status": "success", "ticket_id": 42}
    monkeypatch.setattr(tools, "supabase", db)
    request_id = str(uuid4())

    for _ in range(2):
        assert json.loads(tools.create_ticket(7, "Subject", "Details <here>", client_request_id=request_id))["ticket_id"] == 42
    assert db.rpc.call_count == 2
    first, second = (call.args for call in db.rpc.call_args_list)
    assert first == second and first[0] == "execute_ticket_mutation_with_outbox"
    params = first[1]
    assert params["p_outbox_notification_type"] == "TICKET_CREATED"
    subject, body = worker.render_notification("TICKET_CREATED", {
        **params["p_outbox_payload_template"], "ticket_id": 42, "customer_name": "A & B",
    })
    assert subject == "Subject"
    assert all(text in body for text in ("Ticket #42", "A &amp; B", "Details &lt;here&gt;", "Priority:"))
    inline.assert_not_called()
    db.table.assert_not_called()


def test_outbox_claims_only_after_previous_row_has_finished(monkeypatch):
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    remaining = [{"event_id": str(index)} for index in range(3)]
    cursor.fetchone.side_effect = lambda: remaining.pop(0)
    monkeypatch.setattr(worker.psycopg2, "connect", Mock(return_value=conn))
    runner = worker.OutboxWorker("postgresql://offline", Mock(), batch_size=2)

    def process_row(connection, row):
        assert connection is conn
        assert conn.commit.call_count == int(row["event_id"]) + 1
        assert len(remaining) == 2 - int(row["event_id"])

    process = Mock(side_effect=process_row)
    monkeypatch.setattr(runner, "_process_row", process)
    assert runner._claim_and_process() == 2
    assert [call.args for call in cursor.execute.call_args_list] == [(worker.CLAIM_SQL, (1,))] * 2
    assert remaining == [{"event_id": "2"}]
    assert process.call_count == 2


def test_sql_escalation_uses_configured_address_and_refund_never_updates_orders():
    schema = Path(__file__).resolve().parents[1].joinpath("scripts", "schema.sql").read_text()
    refund = schema.split("ELSIF p_operation_type = 'process_refund' THEN", 1)[1].split("-- 4. Save the terminal result", 1)[0]
    assert REFUND_REVIEW in refund and "UPDATE orders" not in refund
    assert "v_support_email TEXT := NULLIF(p_payload->>'support_team_email', '');" in schema
    assert "IF v_support_email IS NULL THEN" in schema
    assert "support@example.com" not in schema


def test_team_handoff_notification_supports_no_linked_ticket():
    _, body = worker.render_notification("ESCALATION_TEAM", {"session_id": "a&b", "ticket_id": None})
    assert "a&amp;b" in body
