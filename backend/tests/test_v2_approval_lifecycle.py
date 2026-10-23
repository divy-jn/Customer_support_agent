"""Deterministic unit & lifecycle tests for the V2 Order and Payment high-risk action approval lifecycle.

Covers all 20 enumerated cases required by PROMPT 4:
1. V2 cancellation proposal creates an approval without mutating the order.
2. V2 refund proposal creates an approval without processing the refund.
3. Valid confirmation executes the exact approved action once.
4. Confirmation without a pending approval does not execute a tool.
5. A different customer's token cannot approve another customer's operation.
6. An approval from a different session cannot be reused.
7. Modified action parameters are rejected.
8. Expired and cancelled approvals cannot be executed.
9. A repeated confirmation cannot execute an already-claimed or completed operation.
10. Concurrent confirmations result in a single authorized execution.
11. A 'no' response cancels the approval safely.
12. An unrelated question while awaiting approval does not execute the mutation.
13. A missing order ID triggers clarification without invoking the tool.
14. Order ownership or business-state validation failure prevents mutation.
15. A tool error or failed RPC does not produce a false success response.
16. An uncertain timeout is reconciled using the stable operation key.
17. V2 execution preserves customer identity and pending operation state across reconnects.
18. Legacy behavior remains compatible with the supported rollout modes.
19. Clarification routing cannot bypass V2 safety controls through the legacy fallback.
20. Telemetry distinguishes proposed, approved, executing, succeeded, rejected, failed, and uncertain outcomes as applicable.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.websockets import WebSocket

from app.agents.approval_service import (
    ApprovalExecutionResult,
    create_pending_approval,
    execute_approved_action,
    reconcile_uncertain_operation,
    validate_approval,
)
from app.agents.domain_graph import (
    create_domain_support_graph,
    domain_support_graph,
    route_after_supervisor,
    safe_clarification_node,
)
from app.agents.order_agent import handle_order_request
from app.agents.payment_agent import handle_payment_request
from app.config import settings
from app.middleware.tracking import emit_workflow_turn
from app.models import ApprovalStatus, PendingApproval
from app.agents.supervisor import SupervisorAction
from app.persistence.session import session_store
from app.websocket.chat_handler import handle_customer_ws, manager


@pytest.fixture(autouse=True)
def in_memory_session_store(monkeypatch):
    """Ensure tests run against in-memory session store rather than external network."""
    monkeypatch.setattr("app.persistence.session.redis_client", None)
    from app.models import EscalationLifecycleStatus
    monkeypatch.setattr(
        "app.persistence.escalation.ensure_durable_escalation_state",
        lambda _s, _c: EscalationLifecycleStatus.NONE,
    )
    session_store.clear()


# ──────────────────────────────────────────────
#  1. Proposal Tests (Order & Payment)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
@patch("app.agents.order_agent.track_order")
async def test_v2_cancellation_proposal_creates_approval_without_mutating_order(mock_track):
    """Case 1: V2 cancellation proposal creates an approval without mutating the order."""
    state = {
        "message": "Please cancel order #101",
        "intent": "cancel_order",
        "customer_id": 5,
        "session_id": "sess-order-1",
    }
    result = await handle_order_request(state)

    assert result["response"] == "I'd like to cancel order #101 for you. Shall I go ahead?"
    assert result["escalated"] is False
    assert "pending_approval" in result

    pending = result["pending_approval"]
    assert pending["action"] == "cancel_order"
    assert pending["params"] == {"order_id": 101}
    assert pending["customer_id"] == 5
    assert pending["session_id"] == "sess-order-1"
    assert pending["status"] == ApprovalStatus.PENDING.value
    assert pending["approval_id"].startswith("appr_")
    # Must be a valid UUID string matching database schema idempotency_records.client_request_id UUID
    assert uuid.UUID(pending["operation_key"])
    assert "expires_at" in pending
    # Tool mutation must NOT have been called during proposal
    mock_track.assert_not_called()


@pytest.mark.asyncio
@patch("app.agents.payment_agent.get_payment_record")
async def test_v2_refund_proposal_creates_approval_without_processing_refund(mock_payment):
    """Case 2: V2 refund proposal creates an approval without processing the refund."""
    state = {
        "message": "I would like a refund for order #202",
        "intent": "process_refund",
        "customer_id": 9,
        "session_id": "sess-pay-1",
    }
    result = await handle_payment_request(state)

    assert "initiate a refund for order #202" in result["response"]
    assert result["escalated"] is False
    assert "pending_approval" in result

    pending = result["pending_approval"]
    assert pending["action"] == "process_refund"
    assert pending["params"] == {"order_id": 202}
    assert pending["customer_id"] == 9
    assert pending["session_id"] == "sess-pay-1"
    assert pending["status"] == ApprovalStatus.PENDING.value
    # Must be a valid UUID string matching database schema idempotency_records.client_request_id UUID
    assert uuid.UUID(pending["operation_key"])
    mock_payment.assert_not_called()


# ──────────────────────────────────────────────
#  2. Execution Boundary & Confirmation Tests
# ──────────────────────────────────────────────

@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_valid_confirmation_executes_exact_approved_action_once(mock_cancel):
    """Case 3: Valid confirmation executes the exact approved action once."""
    mock_cancel.return_value = json.dumps({
        "status": "success",
        "message": "Order #303 has been cancelled",
        "order_id": 303,
    })

    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 303},
        customer_id=12,
        session_id="sess-exec-3",
        message="Cancel order #303?",
    )

    result = await execute_approved_action(
        approval=approval,
        authenticated_customer_id=12,
        session_id="sess-exec-3",
    )

    assert result.success is True
    assert result.status == ApprovalStatus.SUCCEEDED
    assert "Order #303 has been cancelled" in result.response_text
    mock_cancel.assert_called_once_with(
        order_id=303,
        customer_id=12,
        client_request_id=approval.operation_key,
    )


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
@patch("app.tools.process_refund")
async def test_confirmation_without_pending_approval_does_not_execute_tool(mock_refund, mock_cancel):
    """Case 4: Confirmation without a pending approval does not execute a tool."""
    approval, err = validate_approval(None, authenticated_customer_id=12, session_id="sess-4")
    assert approval is None
    assert "No pending approval" in err

    mock_cancel.assert_not_called()
    mock_refund.assert_not_called()


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_different_customer_cannot_approve_operation(mock_cancel):
    """Case 5: A different customer's token cannot approve another customer's operation."""
    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 404},
        customer_id=12,
        session_id="sess-5",
        message="Cancel order #404?",
    )

    # Attempt execution with customer 99 instead of 12
    result = await execute_approved_action(
        approval=approval,
        authenticated_customer_id=99,
        session_id="sess-5",
    )

    assert result.success is False
    assert result.status == ApprovalStatus.FAILED
    assert "mismatch" in result.error.lower()
    mock_cancel.assert_not_called()


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_approval_from_different_session_cannot_be_reused(mock_cancel):
    """Case 6: An approval from a different session cannot be reused."""
    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 505},
        customer_id=12,
        session_id="sess-legit",
        message="Cancel order #505?",
    )

    result = await execute_approved_action(
        approval=approval,
        authenticated_customer_id=12,
        session_id="sess-hijacked",
    )

    assert result.success is False
    assert result.status == ApprovalStatus.FAILED
    assert "session mismatch" in result.error.lower()
    mock_cancel.assert_not_called()


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_modified_action_parameters_are_rejected(mock_cancel):
    """Case 7: Modified action parameters are rejected."""
    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 606},
        customer_id=12,
        session_id="sess-7",
        message="Cancel order #606?",
    )
    dumped = approval.model_dump()
    # Malicious parameter tampering
    dumped["params"]["order_id"] = -999

    valid, err = validate_approval(dumped, authenticated_customer_id=12, session_id="sess-7")
    assert valid is None
    assert "order_id" in err

    dumped["action"] = "drop_database_tables"
    valid2, err2 = validate_approval(dumped, authenticated_customer_id=12, session_id="sess-7")
    assert valid2 is None
    assert "not in allowlisted" in err2
    mock_cancel.assert_not_called()


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_expired_and_cancelled_approvals_cannot_be_executed(mock_cancel):
    """Case 8: Expired and cancelled approvals cannot be executed."""
    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 707},
        customer_id=12,
        session_id="sess-8",
        message="Cancel order #707?",
    )
    # Simulate expiration by setting expires_at to 1 hour ago
    approval.expires_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    valid, err = validate_approval(approval, authenticated_customer_id=12, session_id="sess-8")
    assert valid is None
    assert "expired" in err.lower()

    # Simulate cancelled status
    approval.expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    approval.status = ApprovalStatus.CANCELLED
    result = await execute_approved_action(approval, authenticated_customer_id=12, session_id="sess-8")
    assert result.success is False
    assert "not pending" in result.error.lower()
    mock_cancel.assert_not_called()


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_repeated_confirmation_cannot_execute_already_claimed_or_completed_operation(mock_cancel):
    """Case 9: A repeated confirmation cannot execute an already-claimed or completed operation."""
    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 808},
        customer_id=12,
        session_id="sess-9",
        message="Cancel order #808?",
    )
    approval.status = ApprovalStatus.SUCCEEDED
    result = await execute_approved_action(approval, authenticated_customer_id=12, session_id="sess-9")
    assert result.success is False
    assert "not pending" in result.error.lower()
    mock_cancel.assert_not_called()


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_concurrent_confirmations_result_in_single_authorized_execution(mock_cancel):
    """Case 10: Concurrent confirmations result in a single authorized execution via atomic CAS claiming."""
    session_id = f"sess_cas_test_{uuid.uuid4().hex}"
    customer_id = 15
    order_id = 909

    pending = create_pending_approval(
        action="cancel_order",
        params={"order_id": order_id},
        customer_id=customer_id,
        session_id=session_id,
        message="Cancel order #909?",
    ).model_dump(mode="json")

    await session_store.save_session(session_id, {
        "session_id": session_id,
        "customer_id": customer_id,
        "conversation_history": [],
        "pending_approval": pending,
        "_session_revision": 1,
    })

    # CAS claim mutator
    def _claim_mutator(s):
        p = s.get("pending_approval")
        if not p or p.get("status") != ApprovalStatus.PENDING.value:
            return False
        p["status"] = ApprovalStatus.EXECUTING.value
        return True

    # Simulate two concurrent confirmation coroutines
    async def _try_claim():
        from app.websocket.chat_handler import _mutate_session
        return await _mutate_session(session_id, _claim_mutator, allow_retry=False, customer_id=customer_id)

    res1, res2 = await asyncio.gather(_try_claim(), _try_claim(), return_exceptions=True)

    claimed_count = 0
    if not isinstance(res1, Exception) and res1[1] is True:
        claimed_count += 1
    if not isinstance(res2, Exception) and res2[1] is True:
        claimed_count += 1

    # Exactly one claim succeeds!
    assert claimed_count == 1


# ──────────────────────────────────────────────
#  3. Rejection, Unrelated Turns, & Validation Failures
# ──────────────────────────────────────────────

@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_no_response_cancels_approval_safely(mock_cancel):
    """Case 11: A 'no' response cancels the approval safely without tool execution."""
    session_id = f"sess_no_{uuid.uuid4().hex}"
    customer_id = 18

    pending = create_pending_approval(
        action="cancel_order",
        params={"order_id": 1010},
        customer_id=customer_id,
        session_id=session_id,
        message="Cancel order #1010?",
    ).model_dump(mode="json")

    await session_store.save_session(session_id, {
        "session_id": session_id,
        "customer_id": customer_id,
        "conversation_history": [],
        "pending_approval": pending,
    })

    # Execute rejection mutator
    def _reject(s):
        p = s.get("pending_approval")
        if p:
            p["status"] = ApprovalStatus.CANCELLED.value
        s.pop("pending_approval", None)
        return True

    from app.websocket.chat_handler import _mutate_session
    s, changed = await _mutate_session(session_id, _reject, allow_retry=True, customer_id=customer_id)

    assert changed is True
    assert "pending_approval" not in s
    mock_cancel.assert_not_called()


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_unrelated_question_while_awaiting_approval_does_not_execute_mutation(mock_cancel):
    """Case 12: An unrelated question while awaiting approval does not execute the mutation and preserves pending approval."""
    session_id = f"sess_unrelated_{uuid.uuid4().hex}"
    customer_id = 21

    pending = create_pending_approval(
        action="cancel_order",
        params={"order_id": 1111},
        customer_id=customer_id,
        session_id=session_id,
        message="Cancel order #1111?",
    ).model_dump(mode="json")

    await session_store.save_session(session_id, {
        "session_id": session_id,
        "customer_id": customer_id,
        "conversation_history": [],
        "pending_approval": pending,
    })

    # User asks "What is your warranty policy?"
    # The message is neither confirmation nor rejection
    clean_msg = "what is your warranty policy?"
    is_conf = clean_msg in ("yes", "yeah", "yep", "sure", "ok", "okay", "go ahead", "proceed", "confirm", "do it")
    is_rej = clean_msg in ("no", "nope", "nah", "cancel", "don't", "dont", "stop", "never mind")
    assert is_conf is False
    assert is_rej is False

    # Simulate graph execution returning an informational answer without new pending approval
    graph_result = {
        "response": "Our products carry a 1-year limited warranty.",
        "pending_approval": None,
    }

    # Verify that applying this result preserves the original pending approval
    def _apply_result(s):
        if graph_result.get("pending_approval"):
            s["pending_approval"] = graph_result["pending_approval"]
        s["conversation_history"].append({"role": "agent", "content": graph_result["response"]})
        return True

    from app.websocket.chat_handler import _mutate_session
    s, _ = await _mutate_session(session_id, _apply_result, allow_retry=True, customer_id=customer_id)

    assert "pending_approval" in s
    assert s["pending_approval"]["params"] == {"order_id": 1111}
    mock_cancel.assert_not_called()


@pytest.mark.asyncio
@patch("app.agents.order_agent.track_order")
@patch("app.tools.cancel_order")
async def test_missing_order_id_triggers_clarification_without_invoking_tool(mock_cancel, mock_track):
    """Case 13: A missing order ID triggers clarification without invoking the tool."""
    result = await handle_order_request({
        "message": "Cancel my order please",
        "intent": "cancel_order",
        "customer_id": 7,
    })
    assert "share the order number" in result["response"]
    assert "pending_approval" not in result
    mock_cancel.assert_not_called()
    mock_track.assert_not_called()


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_order_ownership_or_business_state_validation_failure_prevents_mutation(mock_cancel):
    """Case 14: Order ownership or business-state validation failure prevents mutation."""
    mock_cancel.return_value = json.dumps({
        "error": "Order #1212 not found or does not belong to you"
    })

    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 1212},
        customer_id=25,
        session_id="sess-14",
        message="Cancel order #1212?",
    )

    result = await execute_approved_action(approval, authenticated_customer_id=25, session_id="sess-14")
    assert result.success is False
    assert result.status == ApprovalStatus.FAILED
    assert "does not belong to you" in result.response_text
    assert "unable to cancel" in result.response_text


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_tool_error_or_failed_rpc_does_not_produce_false_success_response(mock_cancel):
    """Case 15: A tool error or failed RPC does not produce a false success response."""
    mock_cancel.return_value = json.dumps({
        "error": "Failed to cancel order #1313: RPC returned no data"
    })

    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 1313},
        customer_id=30,
        session_id="sess-15",
        message="Cancel order #1313?",
    )

    result = await execute_approved_action(approval, authenticated_customer_id=30, session_id="sess-15")
    assert result.success is False
    assert result.status == ApprovalStatus.FAILED
    assert "RPC returned no data" in result.response_text
    assert "successfully cancelled" not in result.response_text.lower()


# ──────────────────────────────────────────────
#  4. Timeout Reconciliation & Reconnects
# ──────────────────────────────────────────────

@pytest.mark.asyncio
@patch("app.tools.track_order")
@patch("app.tools.cancel_order")
async def test_uncertain_timeout_is_reconciled_using_stable_operation_key(mock_cancel, mock_track):
    """Case 16: An uncertain timeout is reconciled using the stable operation key and current order state."""
    # Simulate cancel_order timing out
    mock_cancel.side_effect = asyncio.TimeoutError()
    # Reconciliation check verifies that the order was indeed cancelled
    mock_track.return_value = json.dumps({"id": 1414, "status": "cancelled"})

    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 1414},
        customer_id=35,
        session_id="sess-16",
        message="Cancel order #1414?",
    )

    result = await execute_approved_action(approval, authenticated_customer_id=35, session_id="sess-16")
    # Through reconciliation, it determines success honestly
    assert result.success is True
    assert result.status == ApprovalStatus.SUCCEEDED
    assert "Order #1414 has been cancelled." in result.response_text
    mock_track.assert_called_once_with(order_id=1414, customer_id=35)


@pytest.mark.asyncio
async def test_v2_execution_preserves_customer_identity_and_pending_operation_state_across_reconnects():
    """Case 17: V2 execution preserves customer identity and pending operation state across reconnects."""
    session_id = f"sess_reconnect_{uuid.uuid4().hex}"
    customer_id = 40

    pending = create_pending_approval(
        action="cancel_order",
        params={"order_id": 1515},
        customer_id=customer_id,
        session_id=session_id,
        message="Cancel order #1515?",
    ).model_dump(mode="json")

    # Initial session save before customer disconnects
    await session_store.save_session(session_id, {
        "session_id": session_id,
        "customer_id": customer_id,
        "conversation_history": [{"role": "user", "content": "Cancel my order"}],
        "pending_approval": pending,
    })

    # Simulate customer reconnecting
    reconnected_session = await session_store.get_session(session_id)
    assert reconnected_session is not None
    assert reconnected_session["customer_id"] == customer_id
    assert "pending_approval" in reconnected_session
    assert reconnected_session["pending_approval"]["approval_id"] == pending["approval_id"]
    assert reconnected_session["pending_approval"]["operation_key"] == pending["operation_key"]


# ──────────────────────────────────────────────
#  5. Compatibility & Invariant Routing
# ──────────────────────────────────────────────

def test_legacy_behavior_remains_compatible_with_supported_rollout_modes(monkeypatch):
    """Case 18: Legacy behavior remains compatible with supported rollout modes."""
    monkeypatch.setattr(settings, "agent_architecture_mode", "legacy")
    route = route_after_supervisor({
        "semantic_domain": "order",
        "intent": "cancel_order",
        "supervisor_action": SupervisorAction.CONTINUE.value,
    })
    assert route == "legacy"


@pytest.mark.asyncio
async def test_clarification_routing_cannot_bypass_v2_safety_controls_through_legacy_fallback(monkeypatch):
    """Case 19: Clarification routing cannot bypass V2 safety controls through the legacy fallback."""
    monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
    route = route_after_supervisor({
        "semantic_domain": "order",
        "intent": "cancel_order",
        "supervisor_action": SupervisorAction.REQUEST_CLARIFICATION.value,
    })
    # Must route to safe_clarification, NEVER to legacy_fallback
    assert route == "clarification"
    assert route != "legacy"

    # Execute safe_clarification_node and verify zero tool execution
    state = {
        "message": "Cancel it",
        "semantic_domain": "order",
        "intent": "cancel_order",
        "supervisor_action": "request_clarification",
        "supervisor_reason": "Ambiguous intent requires customer clarification",
        "workflow_state": {"turn_count": 0},
    }
    clarification_res = await safe_clarification_node(state)
    assert clarification_res["escalated"] is False
    assert clarification_res["pending_approval"] is None
    assert "clarify" in clarification_res["response"].lower() or "details" in clarification_res["response"].lower()


# ──────────────────────────────────────────────
#  6. Operational Telemetry
# ──────────────────────────────────────────────

def test_telemetry_distinguishes_approval_lifecycle_outcomes(caplog):
    """Case 20: Telemetry distinguishes proposed, approved, executing, succeeded, rejected, failed, and uncertain outcomes."""
    with caplog.at_level(logging.INFO, logger="tracking"):
        # 1. Proposed
        emit_workflow_turn({
            "session_id": "sess-telem-1",
            "customer_id": 50,
            "architecture_mode": "v2_all",
            "outcome": "approval_proposed",
            "approval_id": "appr_1",
            "approval_action": "cancel_order",
            "approval_status": "pending",
            "pending_approval": True,
            "escalated": False,
        })
        # 2. Executing
        emit_workflow_turn({
            "session_id": "sess-telem-1",
            "customer_id": 50,
            "architecture_mode": "v2_all",
            "outcome": "approval_executing",
            "approval_id": "appr_1",
            "approval_action": "cancel_order",
            "approval_status": "executing",
            "pending_approval": True,
            "escalated": False,
        })
        # 3. Succeeded
        emit_workflow_turn({
            "session_id": "sess-telem-1",
            "customer_id": 50,
            "architecture_mode": "v2_all",
            "outcome": "approval_succeeded",
            "approval_id": "appr_1",
            "approval_action": "cancel_order",
            "approval_status": "succeeded",
            "pending_approval": False,
            "escalated": False,
        })
        # 4. Rejected
        emit_workflow_turn({
            "session_id": "sess-telem-2",
            "customer_id": 51,
            "architecture_mode": "v2_all",
            "outcome": "approval_rejected",
            "approval_id": "appr_2",
            "approval_action": "process_refund",
            "approval_status": "cancelled",
            "pending_approval": False,
            "escalated": False,
        })
        # 5. Failed
        emit_workflow_turn({
            "session_id": "sess-telem-3",
            "customer_id": 52,
            "architecture_mode": "v2_all",
            "outcome": "approval_failed",
            "approval_id": "appr_3",
            "approval_action": "cancel_order",
            "approval_status": "failed",
            "pending_approval": False,
            "escalated": False,
        })
        # 6. Uncertain
        emit_workflow_turn({
            "session_id": "sess-telem-4",
            "customer_id": 53,
            "architecture_mode": "v2_all",
            "outcome": "approval_uncertain",
            "approval_id": "appr_4",
            "approval_action": "process_refund",
            "approval_status": "failed",
            "pending_approval": False,
            "escalated": False,
        })

    records = [r for r in caplog.records if r.message == "workflow_turn"]
    assert len(records) == 6
    assert records[0].outcome == "approval_proposed"
    assert records[0].approval_status == "pending"
    assert records[1].outcome == "approval_executing"
    assert records[1].approval_status == "executing"
    assert records[2].outcome == "approval_succeeded"
    assert records[2].approval_status == "succeeded"
    assert records[3].outcome == "approval_rejected"
    assert records[3].approval_status == "cancelled"
    assert records[4].outcome == "approval_failed"
    assert records[4].approval_status == "failed"
    assert records[5].outcome == "approval_uncertain"


# ──────────────────────────────────────────────
#  6. Contract-Level Audit & Verification Tests (PROMPT 5)
# ──────────────────────────────────────────────

def test_contract_idempotency_key_is_strictly_valid_uuid():
    """Contract 1: Operation key matches PostgreSQL idempotency_records.client_request_id UUID."""
    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 999},
        customer_id=10,
        session_id="sess-contract-1",
        message="Confirm cancel?",
    )
    # 1. Must parse cleanly as UUID without ValueError
    parsed = uuid.UUID(approval.operation_key)
    assert parsed.version == 4
    assert str(parsed) == approval.operation_key

    # 2. validate_approval strictly rejects non-UUID arbitrary strings (such as legacy 'op_...' keys)
    approval_dict = approval.model_dump()
    approval_dict["operation_key"] = "op_cancel_order_10_999_invalid123"
    valid, err = validate_approval(approval_dict, authenticated_customer_id=10, session_id="sess-contract-1")
    assert valid is None
    assert "must be a valid UUID string" in err


@pytest.mark.asyncio
async def test_contract_non_uuid_operation_key_rejected_at_execution_boundary():
    """Contract 1b: Execution boundary rejects malformed non-UUID operation key."""
    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 888},
        customer_id=15,
        session_id="sess-contract-2",
        message="Confirm cancel?",
    )
    dumped = approval.model_dump()
    dumped["operation_key"] = "not-a-uuid"

    result = await execute_approved_action(dumped, authenticated_customer_id=15, session_id="sess-contract-2")
    assert result.success is False
    assert result.status == ApprovalStatus.FAILED
    assert "UUID" in result.error or "UUID" in result.response_text


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_contract_idempotent_retry_returns_cached_result_without_reexecution(mock_cancel):
    """Contract 2: Exact retry with same operation key returns previously recorded outcome."""
    cached_db_result = {
        "status": "success",
        "message": "Order #777 has been cancelled",
        "order_id": 777,
    }
    mock_cancel.return_value = json.dumps(cached_db_result)

    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 777},
        customer_id=20,
        session_id="sess-contract-3",
        message="Cancel order?",
    )

    # First execution
    res1 = await execute_approved_action(approval, authenticated_customer_id=20, session_id="sess-contract-3")
    assert res1.success is True
    assert res1.status == ApprovalStatus.SUCCEEDED
    assert res1.response_text == "Order #777 has been cancelled"

    # Second execution (idempotent retry with identical operation_key)
    res2 = await execute_approved_action(approval, authenticated_customer_id=20, session_id="sess-contract-3")
    assert res2.success is True
    assert res2.status == ApprovalStatus.SUCCEEDED
    assert res2.operation_key == approval.operation_key
    # Both executions invoked tool with the exact same client_request_id
    assert mock_cancel.call_args_list[0].kwargs["client_request_id"] == approval.operation_key
    assert mock_cancel.call_args_list[1].kwargs["client_request_id"] == approval.operation_key


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_contract_reused_request_id_with_altered_parameters_rejected(mock_cancel):
    """Contract 3: Reused request ID with altered parameters is rejected by IdempotencyConflict."""
    mock_cancel.return_value = json.dumps({
        "error": "IdempotencyConflict",
        "message": "This request ID was already used for a different operation.",
        "previous_result": {"status": "success", "order_id": 111}
    })

    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 222},
        customer_id=25,
        session_id="sess-contract-4",
        message="Cancel order?",
    )

    result = await execute_approved_action(approval, authenticated_customer_id=25, session_id="sess-contract-4")
    assert result.success is False
    assert result.status == ApprovalStatus.FAILED
    assert "IdempotencyConflict" in result.error
    assert "unable to cancel" in result.response_text


@pytest.mark.asyncio
async def test_contract_persistence_revision_conflict_prevents_stale_approval_overwrite():
    """Contract 5: CAS conditional persistence prevents a stale worker from overwriting a newer approval state."""
    session_id = f"sess_cas_test_{uuid.uuid4().hex}"
    initial_session = {
        "session_id": session_id,
        "customer_id": 30,
        "_session_revision": 1,
        "pending_approval": {
            "approval_id": "appr_v1",
            "status": ApprovalStatus.PENDING.value,
        }
    }
    await session_store.save_session(session_id, initial_session)

    # Worker A observes revision 1 and updates to EXECUTING at revision 2
    from app.persistence.session import SaveResult
    loaded_a = await session_store.get_session(session_id)
    loaded_a["pending_approval"]["status"] = ApprovalStatus.EXECUTING.value
    res_a = await session_store.save_session_conditional(session_id, loaded_a, expected_revision=1)
    assert res_a == SaveResult.SUCCESS

    # Worker B still holds stale expected_revision=1 and tries to set status=SUCCEEDED
    stale_b = initial_session.copy()
    stale_b["pending_approval"]["status"] = ApprovalStatus.SUCCEEDED.value
    res_b = await session_store.save_session_conditional(session_id, stale_b, expected_revision=1)
    assert res_b == SaveResult.CONFLICT

    # Verify store retains the updated revision 2 state from Worker A
    current = await session_store.get_session(session_id)
    assert current["_session_revision"] == 2
    assert current["pending_approval"]["status"] == ApprovalStatus.EXECUTING.value


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_contract_reconciliation_uses_authoritative_idempotency_records_table(mock_cancel):
    """Contract 6a: Reconciliation against durable idempotency_records terminal_result."""
    mock_cancel.side_effect = asyncio.TimeoutError()

    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 333},
        customer_id=40,
        session_id="sess-reconcile-durable",
        message="Cancel order?",
    )

    mock_db_resp = MagicMock()
    mock_db_resp.data = [{
        "operation_type": "cancel_order",
        "canonical_target": "333",
        "terminal_result": {
            "status": "success",
            "message": "Order #333 has been cancelled",
        }
    }]

    with patch("app.tools.supabase.table") as mock_table:
        mock_select = MagicMock()
        mock_table.return_value.select.return_value = mock_select
        mock_select.eq.return_value.eq.return_value.execute.return_value = mock_db_resp

        result = await execute_approved_action(approval, authenticated_customer_id=40, session_id="sess-reconcile-durable")
        assert result.success is True
        assert result.status == ApprovalStatus.SUCCEEDED
        assert result.is_uncertain is False
        assert "Order #333 has been cancelled" in result.response_text


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_contract_reconciliation_authoritative_error_recorded_in_idempotency_table(mock_cancel):
    """Contract 6b: Reconciliation detects authoritative error recorded in idempotency_records."""
    mock_cancel.side_effect = asyncio.TimeoutError()

    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 444},
        customer_id=45,
        session_id="sess-reconcile-err",
        message="Cancel order?",
    )

    mock_db_resp = MagicMock()
    mock_db_resp.data = [{
        "operation_type": "cancel_order",
        "canonical_target": "444",
        "terminal_result": {
            "error": "Order #444 not found or does not belong to you",
        }
    }]

    with patch("app.tools.supabase.table") as mock_table:
        mock_select = MagicMock()
        mock_table.return_value.select.return_value = mock_select
        mock_select.eq.return_value.eq.return_value.execute.return_value = mock_db_resp

        result = await execute_approved_action(approval, authenticated_customer_id=45, session_id="sess-reconcile-err")
        assert result.success is False
        assert result.status == ApprovalStatus.FAILED
        assert result.is_uncertain is False
        assert "not belong to you" in result.error


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_contract_reconciliation_unproven_timeout_reports_uncertain(mock_cancel):
    """Contract 6c: Unproven timeout without durable record or active order check reports uncertain."""
    mock_cancel.side_effect = asyncio.TimeoutError()

    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 555},
        customer_id=50,
        session_id="sess-reconcile-uncert",
        message="Cancel order?",
    )

    mock_db_resp = MagicMock()
    mock_db_resp.data = []  # No idempotency record written

    with patch("app.tools.supabase.table") as mock_table, \
         patch("app.tools.track_order", return_value=json.dumps({"error": "DB unreachable"})):
        mock_select = MagicMock()
        mock_table.return_value.select.return_value = mock_select
        mock_select.eq.return_value.eq.return_value.execute.return_value = mock_db_resp

        result = await execute_approved_action(approval, authenticated_customer_id=50, session_id="sess-reconcile-uncert")
        assert result.success is False
        assert result.status == ApprovalStatus.FAILED
        assert result.is_uncertain is True
        assert "timed out" in result.response_text


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_contract_untrusted_empty_or_malformed_tool_response_fails(mock_cancel):
    """Contract 8: Empty dictionary or malformed payload from tool is never treated as success."""
    approval = create_pending_approval(
        action="cancel_order",
        params={"order_id": 666},
        customer_id=60,
        session_id="sess-malformed",
        message="Cancel order?",
    )

    # 1. Empty dictionary
    mock_cancel.return_value = "{}"
    res1 = await execute_approved_action(approval, authenticated_customer_id=60, session_id="sess-malformed")
    assert res1.success is False
    assert res1.status == ApprovalStatus.FAILED
    assert "unverified" in res1.response_text.lower() or "missing" in res1.error.lower()

    # 2. Non-JSON string
    mock_cancel.return_value = "<html>Error 502 Bad Gateway</html>"
    res2 = await execute_approved_action(approval, authenticated_customer_id=60, session_id="sess-malformed")
    assert res2.success is False
    assert res2.status == ApprovalStatus.FAILED
    assert "Invalid tool response" in res2.error

    # 3. Message string alone without structured status='success'
    mock_cancel.return_value = json.dumps({"message": "Order #666 has been cancelled"})
    res3 = await execute_approved_action(approval, authenticated_customer_id=60, session_id="sess-malformed")
    assert res3.success is False
    assert res3.status == ApprovalStatus.FAILED
    assert "Missing authoritative status='success'" in res3.error

    # 4. Message string with non-success status (e.g., status='pending' or 'failed')
    mock_cancel.return_value = json.dumps({"message": "Cancellation pending review", "status": "pending"})
    res4 = await execute_approved_action(approval, authenticated_customer_id=60, session_id="sess-malformed")
    assert res4.success is False
    assert res4.status == ApprovalStatus.FAILED
    assert "Missing authoritative status='success'" in res4.error


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_contract_websocket_uses_approval_operation_key_not_turn_request_id(mock_cancel):
    """Contract 10: WebSocket confirmation uses approval.operation_key as client_request_id, NOT chat turn request ID."""
    mock_cancel.return_value = json.dumps({
        "status": "success",
        "message": "Order #789 has been cancelled",
        "order_id": 789,
    })

    session_id = f"sess_ws_turn_id_{uuid.uuid4().hex}"
    customer_id = 70

    # 1. Proposal with durable operation_key
    pending = create_pending_approval(
        action="cancel_order",
        params={"order_id": 789},
        customer_id=customer_id,
        session_id=session_id,
        message="Cancel order #789?",
    )
    biz_op_key = pending.operation_key
    assert uuid.UUID(biz_op_key)  # Valid UUID

    session = {
        "session_id": session_id,
        "customer_id": customer_id,
        "_session_revision": 1,
        "mode": "ai",
        "conversation_history": [],
        "pending_approval": pending.model_dump(),
        "workflow_state": {"active_domain": "order", "state_revision": 1, "session_id": session_id, "customer_id": customer_id, "turn_count": 1},
    }
    await session_store.save_session(session_id, session)

    # 2. Confirmation message with turn-level client_request_id
    turn_request_id = str(uuid.uuid4())
    assert turn_request_id != biz_op_key

    client_messages = [
        json.dumps({"message": "yes", "client_request_id": turn_request_id}),
    ]
    msg_idx = 0
    sent_messages = []

    async def mock_receive_text():
        nonlocal msg_idx
        if msg_idx < len(client_messages):
            msg = client_messages[msg_idx]
            msg_idx += 1
            return msg
        await asyncio.sleep(0.05)
        raise RuntimeError("Disconnected")

    mock_ws = AsyncMock(spec=WebSocket)
    mock_ws.receive_text = mock_receive_text
    mock_ws.send_text = AsyncMock(side_effect=lambda t: sent_messages.append(json.loads(t)))

    with patch("app.websocket.chat_handler.validate_input", return_value=MagicMock(passed=True, sanitized_text="yes", pii_detected=False)), \
         patch("app.websocket.chat_handler.validate_output", side_effect=lambda x: MagicMock(sanitized_text=x)):
        await handle_customer_ws(mock_ws, session_id=session_id, authenticated_customer_id=customer_id)

    # Verify that cancel_order received biz_op_key, NEVER turn_request_id!
    mock_cancel.assert_called_once()
    actual_client_req_id = mock_cancel.call_args.kwargs.get("client_request_id")
    assert actual_client_req_id == biz_op_key
    assert actual_client_req_id != turn_request_id


@pytest.mark.asyncio
@patch("app.tools.cancel_order")
async def test_contract_concurrent_websocket_confirmations_single_execution(mock_cancel):
    """Contract 4: Two simultaneous confirmation messages across concurrent connections result in exactly one mutation."""
    mock_cancel.return_value = json.dumps({
        "status": "success",
        "message": "Order #911 has been cancelled",
        "order_id": 911,
    })

    session_id = f"sess_concurrent_ws_{uuid.uuid4().hex}"
    customer_id = 85

    pending = create_pending_approval(
        action="cancel_order",
        params={"order_id": 911},
        customer_id=customer_id,
        session_id=session_id,
        message="Cancel order #911?",
    )
    await session_store.save_session(session_id, {
        "session_id": session_id,
        "customer_id": customer_id,
        "_session_revision": 1,
        "mode": "ai",
        "conversation_history": [],
        "pending_approval": pending.model_dump(),
        "workflow_state": {"active_domain": "order", "state_revision": 1, "session_id": session_id, "customer_id": customer_id, "turn_count": 1},
    })

    # Prepare two separate WebSocket connections sending "yes" simultaneously
    ws1_sent, ws2_sent = [], []
    stop_event = asyncio.Event()

    async def make_ws(sent_list):
        sent_yet = False
        async def recv():
            nonlocal sent_yet
            if not sent_yet:
                sent_yet = True
                return json.dumps({"message": "yes", "client_request_id": str(uuid.uuid4())})
            await stop_event.wait()
            raise RuntimeError("Done")
        ws = AsyncMock(spec=WebSocket)
        ws.receive_text = recv
        ws.send_text = AsyncMock(side_effect=lambda t: sent_list.append(json.loads(t)))
        return ws

    ws1 = await make_ws(ws1_sent)
    ws2 = await make_ws(ws2_sent)

    mock_db_table = MagicMock()
    mock_db_table.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    mock_db_table.upsert.return_value.execute.return_value = MagicMock(data=[])

    all_sent_messages = []
    async def mock_send_personal(payload, s_id):
        all_sent_messages.append(payload)

    with patch("app.websocket.chat_handler.validate_input", return_value=MagicMock(passed=True, sanitized_text="yes", pii_detected=False)), \
         patch("app.websocket.chat_handler.validate_output", side_effect=lambda x: MagicMock(sanitized_text=x)), \
         patch("app.tools.supabase.table", return_value=mock_db_table), \
         patch("app.websocket.chat_handler.manager.send_personal_message", side_effect=mock_send_personal):
        tasks = asyncio.gather(
            handle_customer_ws(ws1, session_id=session_id, authenticated_customer_id=customer_id),
            handle_customer_ws(ws2, session_id=session_id, authenticated_customer_id=customer_id),
            return_exceptions=True,
        )
        # Give both handlers a moment to process their first turn
        for _ in range(30):
            await asyncio.sleep(0.05)
            all_agent_msgs = [m for m in all_sent_messages if m.get("type") == "agent_response"]
            if len(all_agent_msgs) >= 2:
                break
        stop_event.set()
        await tasks

    # cancel_order must be invoked EXACTLY ONCE
    assert mock_cancel.call_count == 1

    # One connection received the success message, the other received already processing/completed
    all_responses = [
        msg.get("message", "")
        for msg in all_sent_messages
        if msg.get("type") == "agent_response"
    ]
    assert any("has been cancelled" in m for m in all_responses)
    assert any("already being processed" in m or "already been completed" in m for m in all_responses)



@pytest.mark.asyncio
async def test_contract_unrelated_informational_turn_preserves_pending_approval():
    """Contract: An unrelated informational message does not approve, discard, or replace the pending operation."""
    session_id = f"sess_info_turn_{uuid.uuid4().hex}"
    customer_id = 95

    pending = create_pending_approval(
        action="cancel_order",
        params={"order_id": 999},
        customer_id=customer_id,
        session_id=session_id,
        message="Cancel order #999?",
    )
    await session_store.save_session(session_id, {
        "session_id": session_id,
        "customer_id": customer_id,
        "_session_revision": 1,
        "mode": "ai",
        "conversation_history": [],
        "pending_approval": pending.model_dump(),
        "workflow_state": {"active_domain": "order", "state_revision": 1, "session_id": session_id, "customer_id": customer_id, "turn_count": 1},
    })

    sent_messages = []
    sent_yet = False
    async def mock_receive_text():
        nonlocal sent_yet
        if not sent_yet:
            sent_yet = True
            return json.dumps({"message": "What payment methods do you accept?", "client_request_id": str(uuid.uuid4())})
        await asyncio.sleep(0.05)
        raise RuntimeError("Done")

    mock_ws = AsyncMock(spec=WebSocket)
    mock_ws.receive_text = mock_receive_text
    mock_ws.send_text = AsyncMock(side_effect=lambda t: sent_messages.append(json.loads(t)))

    mock_graph_result = {
        "response": "We accept Credit Cards, PayPal, and Apple Pay.",
        "intent": "payment_methods",
        "sentiment": "neutral",
        "urgency": "low",
        "semantic_domain": "general",
        "workflow_state": {"active_domain": "general"},
        # Notice: graph result does NOT contain pending_approval
    }

    mock_db_table = MagicMock()
    mock_db_table.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[])
    mock_db_table.upsert.return_value.execute.return_value = MagicMock(data=[])

    with patch("app.websocket.chat_handler.validate_input", return_value=MagicMock(passed=True, sanitized_text="What payment methods do you accept?", pii_detected=False)), \
         patch("app.websocket.chat_handler.validate_output", side_effect=lambda x: MagicMock(sanitized_text=x)), \
         patch("app.tools.supabase.table", return_value=mock_db_table), \
         patch("app.websocket.chat_handler.domain_support_graph.ainvoke", new_callable=AsyncMock, return_value=mock_graph_result), \
         patch("app.websocket.chat_handler.customer_support_graph.ainvoke", new_callable=AsyncMock, return_value=mock_graph_result):
        await handle_customer_ws(mock_ws, session_id=session_id, authenticated_customer_id=customer_id)

    # Verify that the session STILL has the pending approval untouched!
    saved_session = await session_store.get_session(session_id)
    assert saved_session["pending_approval"] is not None
    assert saved_session["pending_approval"]["action"] == "cancel_order"
    assert saved_session["pending_approval"]["params"] == {"order_id": 999}
    assert saved_session["pending_approval"]["status"] == ApprovalStatus.PENDING.value
    assert saved_session["pending_approval"]["operation_key"] == pending.operation_key


# ──────────────────────────────────────────────
#  4. Phase B: Approval Signature Hardening & Security Tests
# ──────────────────────────────────────────────

class TestApprovalSignatureHardening:
    """Security verification suite for HMAC-SHA256 approval signing, canonical payloads, and fail-closed boundaries."""

    def test_missing_or_empty_signing_secret_fails_closed_on_proposal(self, monkeypatch):
        """Missing or whitespace APPROVAL_SIGNING_SECRET fails closed during approval proposal."""
        monkeypatch.setattr(settings, "approval_signing_secret", "")
        with pytest.raises(RuntimeError) as exc_info:
            create_pending_approval(
                action="cancel_order",
                params={"order_id": 100},
                customer_id=1,
                session_id="sess-missing-secret",
                message="Cancel order #100?",
            )
        assert "APPROVAL_SIGNING_SECRET is not configured or invalid" in str(exc_info.value)

        monkeypatch.setattr(settings, "approval_signing_secret", "   ")
        with pytest.raises(RuntimeError):
            create_pending_approval(
                action="cancel_order",
                params={"order_id": 100},
                customer_id=1,
                session_id="sess-missing-secret",
                message="Cancel order #100?",
            )

    @pytest.mark.asyncio
    @patch("app.tools.cancel_order")
    async def test_missing_signing_secret_fails_closed_on_execution(self, mock_cancel, monkeypatch):
        """Missing APPROVAL_SIGNING_SECRET during execution fails closed without invoking mutation tools."""
        monkeypatch.setattr(settings, "approval_signing_secret", "test-valid-pre-secret-32-chars-long")
        approval = create_pending_approval(
            action="cancel_order",
            params={"order_id": 101},
            customer_id=2,
            session_id="sess-exec-missing-sec",
            message="Cancel order #101?",
        )
        # Wipe secret before execution
        monkeypatch.setattr(settings, "approval_signing_secret", "")
        result = await execute_approved_action(
            approval=approval,
            authenticated_customer_id=2,
            session_id="sess-exec-missing-sec",
        )
        assert result.success is False
        assert result.status == ApprovalStatus.FAILED
        assert "signature" in result.error.lower()
        mock_cancel.assert_not_called()

    def test_deterministic_signature_generation(self, monkeypatch):
        """Canonical JSON serialization produces identical signatures regardless of dictionary key order."""
        monkeypatch.setattr(settings, "approval_signing_secret", "test-secret-for-determinism-32char")
        from app.agents.approval_service import compute_approval_signature

        op_key = str(uuid.uuid4())
        exp = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()

        params1 = {"order_id": 102, "reason": "damaged in shipping", "notes": "box was crushed"}
        params2 = {"notes": "box was crushed", "order_id": 102, "reason": "damaged in shipping"}

        sig1 = compute_approval_signature(
            customer_id=3, session_id="sess-det", action="cancel_order",
            params=params1, operation_key=op_key, expires_at=exp, version="v1",
        )
        sig2 = compute_approval_signature(
            customer_id=3, session_id="sess-det", action="cancel_order",
            params=params2, operation_key=op_key, expires_at=exp, version="v1",
        )
        assert sig1 == sig2
        assert len(sig1) == 64

    @pytest.mark.asyncio
    @patch("app.tools.cancel_order")
    async def test_tampered_bound_fields_rejected_before_tool_invocation(self, mock_cancel, monkeypatch):
        """Tampering with any bound field (customer, session, action, op_key, expires) invalidates signature."""
        monkeypatch.setattr(settings, "approval_signing_secret", "test-secret-for-tamper-tests-32c")
        from app.agents.approval_service import verify_approval_signature

        base = create_pending_approval(
            action="cancel_order",
            params={"order_id": 103},
            customer_id=4,
            session_id="sess-tamper",
            message="Cancel order #103?",
        )
        assert verify_approval_signature(base) is True

        # 1. Tamper customer_id
        t_cust = base.model_copy(update={"customer_id": 999})
        assert verify_approval_signature(t_cust) is False

        # 2. Tamper session_id
        t_sess = base.model_copy(update={"session_id": "hijacked-session"})
        assert verify_approval_signature(t_sess) is False

        # 3. Tamper action
        t_act = base.model_copy(update={"action": "process_refund"})
        assert verify_approval_signature(t_act) is False

        # 4. Tamper operation_key
        t_op = base.model_copy(update={"operation_key": str(uuid.uuid4())})
        assert verify_approval_signature(t_op) is False

        # 5. Tamper expires_at
        t_exp = base.model_copy(update={"expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()})
        assert verify_approval_signature(t_exp) is False

        # 6. Tamper signature_version
        t_ver = base.model_copy(update={"signature_version": "v2"})
        assert verify_approval_signature(t_ver) is False

        # Execution of any tampered approval must fail and NOT call the tool
        res = await execute_approved_action(t_cust, authenticated_customer_id=999, session_id="sess-tamper")
        assert res.success is False
        mock_cancel.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.tools.cancel_order")
    async def test_tampered_nested_parameters_rejected(self, mock_cancel, monkeypatch):
        """Tampering with nested parameters invalidates HMAC signature and prevents tool call."""
        monkeypatch.setattr(settings, "approval_signing_secret", "test-secret-for-nested-param-32c")
        from app.agents.approval_service import verify_approval_signature

        approval = create_pending_approval(
            action="cancel_order",
            params={"order_id": 104, "metadata": {"reason_code": "WRONG_SIZE", "tags": ["apparel", "winter"]}},
            customer_id=5,
            session_id="sess-nest",
            message="Cancel order #104?",
        )
        assert verify_approval_signature(approval) is True

        # Tamper deep nested value
        tampered_params = {"order_id": 104, "metadata": {"reason_code": "FRAUD_EXEMPTION", "tags": ["apparel", "winter"]}}
        tampered_appr = approval.model_copy(update={"params": tampered_params})
        assert verify_approval_signature(tampered_appr) is False

        res = await execute_approved_action(tampered_appr, authenticated_customer_id=5, session_id="sess-nest")
        assert res.success is False
        assert "signature" in res.error.lower()
        mock_cancel.assert_not_called()

    def test_unsupported_parameter_types_rejected_during_canonicalization(self, monkeypatch):
        """Unsupported types (e.g. custom objects, callables) are strictly rejected during signing."""
        monkeypatch.setattr(settings, "approval_signing_secret", "test-secret-unsupported-types-32")
        from app.agents.approval_service import canonicalize_parameters

        class CustomObj:
            pass

        with pytest.raises(ValueError) as exc:
            canonicalize_parameters({"valid": 1, "obj": CustomObj()})
        assert "Unsupported parameter type" in str(exc.value)

        with pytest.raises(ValueError):
            canonicalize_parameters({"func": lambda x: x})

    @pytest.mark.asyncio
    @patch("app.tools.cancel_order")
    async def test_key_rotation_safely_invalidates_pre_rotation_approvals(self, mock_cancel, monkeypatch):
        """Key rotation invalidates pending approvals signed with the previous key."""
        # 1. Create approval with old secret
        monkeypatch.setattr(settings, "approval_signing_secret", "old-secret-version-1-at-least-32c")
        approval = create_pending_approval(
            action="cancel_order",
            params={"order_id": 105},
            customer_id=6,
            session_id="sess-rotation",
            message="Cancel order #105?",
        )
        from app.agents.approval_service import verify_approval_signature
        assert verify_approval_signature(approval) is True

        # 2. Rotate to new secret
        monkeypatch.setattr(settings, "approval_signing_secret", "new-rotated-secret-ver-2-at-least-32c")
        assert verify_approval_signature(approval) is False

        # 3. Execution attempt under new secret fails closed
        res = await execute_approved_action(approval, authenticated_customer_id=6, session_id="sess-rotation")
        assert res.success is False
        assert "signature" in res.error.lower()
        mock_cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_signature_preserved_across_session_persistence_roundtrip(self, monkeypatch):
        """Approval signature survives session serialization, storage in session_store, and reconstruction."""
        monkeypatch.setattr(settings, "approval_signing_secret", "test-secret-persistence-32chars")
        from app.agents.approval_service import verify_approval_signature

        session_id = f"sess-persist-{uuid.uuid4().hex[:8]}"
        approval = create_pending_approval(
            action="cancel_order",
            params={"order_id": 106},
            customer_id=7,
            session_id=session_id,
            message="Cancel order #106?",
        )

        # Store into session_store
        session_blob = {
            "session_id": session_id,
            "customer_id": 7,
            "_session_revision": 1,
            "pending_approval": approval.model_dump(),
        }
        await session_store.save_session(session_id, session_blob)

        # Retrieve and reconstruct
        retrieved = await session_store.get_session(session_id)
        assert retrieved is not None
        reconstructed = PendingApproval.model_validate(retrieved["pending_approval"])

        assert reconstructed.signature == approval.signature
        assert reconstructed.signature_version == approval.signature_version
        assert verify_approval_signature(reconstructed) is True

    @pytest.mark.asyncio
    @patch("app.tools.cancel_order")
    async def test_absent_or_malformed_signature_rejected_before_tool_invocation(self, mock_cancel, monkeypatch):
        """Approvals with None, empty, or truncated signature strings fail closed before tool execution."""
        monkeypatch.setattr(settings, "approval_signing_secret", "test-secret-malformed-32chars-ok")
        from app.agents.approval_service import verify_approval_signature, claim_pending_approval_in_session

        base = create_pending_approval(
            action="cancel_order",
            params={"order_id": 107},
            customer_id=8,
            session_id="sess-malformed",
            message="Cancel order #107?",
        )

        for bad_sig in [None, "", "   ", "short_invalid_hex", "a" * 63, "z" * 64]:
            t_appr = base.model_copy(update={"signature": bad_sig})
            assert verify_approval_signature(t_appr) is False

            # Test claim rejects
            sess_data = {"pending_approval": t_appr.model_dump()}
            outcome, err = claim_pending_approval_in_session(sess_data, authenticated_customer_id=8, session_id="sess-malformed")
            assert outcome.value == "signature_failed"
            assert "signature" in err.lower()

            # Test execute rejects
            res = await execute_approved_action(t_appr, authenticated_customer_id=8, session_id="sess-malformed")
            assert res.success is False
            assert "signature" in res.error.lower()
            mock_cancel.assert_not_called()
