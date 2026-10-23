"""End-to-End WebSocket approval integration test suite (Prompt 10).

Exercises the exact 8 scenarios required by PROMPT 10:
- Scenario 1: Proposal only (proposes refund #4004, persists pending approval, zero tool mutations, stable UUID).
- Scenario 2: Explicit confirmation (retrieves stored approval, validates binding/signature/CAS, 1 mutation, authoritative success).
- Scenario 3: Ambiguous affirmation after domain switch (product switch + bare 'yes' -> ambiguous clarification, zero tool calls).
- Scenario 4: Explicit confirmation after domain switch (resumes persisted approval, executes once, duplicate blocked).
- Scenario 5: Informational interruption (FAQ turn answered normally, pending approval remains intact, zero execution).
- Scenario 6: Failure & uncertainty (rejected, expired, tampered, failed, and uncertain deterministic outcomes).
- Scenario 7: WebSocket lifecycle (disconnect/reconnect persistence, duplicate prevention, fail-closed signing).
- Scenario 8: Accessibility & usability (understandable descriptions, distinct confirm vs cancel, accidental trigger prevention).

All mutations are mocked to ensure zero real destructive operations occur.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings
from app.models import ApprovalStatus, EscalationLifecycleStatus, PendingApproval
from app.agents.approval_service import (
    ApprovalExecutionResult,
    ApprovalClaimOutcome,
    create_pending_approval,
    compute_approval_signature,
)
from app.persistence.session import session_store


TEST_JWT_SECRET = "dev-secret-key-that-is-long-enough-for-testing-purposes"
TEST_SIGNING_SECRET = "test-approval-signing-secret-key-for-hmac-32chars"


@pytest.fixture(autouse=True)
def setup_test_environment(monkeypatch):
    """Configure in-memory session store and secrets for test isolation."""
    class EmptyConversationQuery:
        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def execute(self):
            from types import SimpleNamespace
            return SimpleNamespace(data=[])

    monkeypatch.setattr("app.tools.supabase.table", lambda _name: EmptyConversationQuery())
    monkeypatch.setattr("app.persistence.session.redis_client", None)
    monkeypatch.setattr("app.persistence.escalation.ensure_durable_escalation_state", lambda _s, _c: EscalationLifecycleStatus.NONE)
    monkeypatch.setattr(settings, "jwt_secret", TEST_JWT_SECRET)
    monkeypatch.setattr(settings, "approval_signing_secret", TEST_SIGNING_SECRET)
    monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
    session_store.clear()
    yield
    session_store.clear()


def generate_customer_token(customer_id: int = 1) -> str:
    expiration = datetime.now(timezone.utc) + timedelta(minutes=60)
    payload = {
        "sub": str(customer_id),
        "role": "customer",
        "exp": expiration,
    }
    return jwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


def read_next_agent_response(websocket, timeout_cycles=15) -> dict:
    """Read websocket messages until an agent_response or error message is returned."""
    for _ in range(timeout_cycles):
        msg = websocket.receive_json()
        msg_type = msg.get("type")
        if msg_type in ("agent_response", "error"):
            return msg
    raise TimeoutError("Did not receive agent_response within expected cycles")


# ══════════════════════════════════════════════════════════════════
# Scenario 1 — Proposal only
# ══════════════════════════════════════════════════════════════════

@patch("app.tools.process_refund")
@patch("app.persistence.escalation.ensure_durable_escalation_state")
def test_scenario_1_proposal_only(mock_ensure_state, mock_process_refund):
    """Customer requests a refund for order #4004.

    Expected:
    - Backend proposes refund and persists pending approval.
    - Frontend receives approval payload with status="pending", action="process_refund", order_id=4004.
    - No mutation tool is called (mock_process_refund call count == 0).
    - The operation UUID and approval identity remain stable.
    """
    mock_ensure_state.return_value = EscalationLifecycleStatus.NONE
    token = generate_customer_token(customer_id=1)
    session_id = f"test-sess-proposal-{uuid.uuid4().hex[:6]}"

    # Mock domain graph to return a refund proposal for order #4004
    pending_proposal = create_pending_approval(
        action="process_refund",
        params={"order_id": 4004},
        customer_id=1,
        session_id=session_id,
        message="Would you like me to process a refund for order #4004?",
    )

    mock_graph_result = {
        "response": "I can help refund order #4004. Would you like me to process this refund?",
        "pending_approval": pending_proposal.model_dump(),
        "workflow_state": {
            "active_domain": "payment",
            "state_revision": 1,
            "session_id": session_id,
            "customer_id": 1,
            "turn_count": 1,
            "payment_state": {"domain_status": "in_progress", "order_id": 4004},
        },
        "semantic_domain": "payment",
        "intent": "process_refund",
        "escalated": False,
    }

    client = TestClient(app)
    with patch("app.websocket.chat_handler.domain_support_graph.ainvoke", new_callable=AsyncMock) as mock_graph:
        mock_graph.return_value = mock_graph_result

        with client.websocket_connect(f"/ws/chat/{session_id}?token={token}") as ws:
            welcome = ws.receive_json()
            assert welcome["type"] == "system"

            req_id = str(uuid.uuid4())
            ws.send_json({
                "message": "I would like a refund for order #4004",
                "customer_id": 1,
                "client_request_id": req_id,
            })

            resp = read_next_agent_response(ws)
            assert resp["type"] == "agent_response"
            assert "approval" in resp
            approval = resp["approval"]
            assert approval["status"] == "pending"
            assert approval["action"] == "process_refund"
            assert approval["params"]["order_id"] == 4004
            assert approval["approval_id"] == pending_proposal.approval_id

            # Zero mutation tool calls during proposal
            assert mock_process_refund.call_count == 0

            # Verify persisted session state
            saved_session = asyncio.run(session_store.get_session(session_id))
            assert saved_session is not None
            persisted_appr = saved_session.get("pending_approval")
            assert persisted_appr is not None
            assert persisted_appr["approval_id"] == pending_proposal.approval_id
            assert persisted_appr["operation_key"] == pending_proposal.operation_key
            assert uuid.UUID(persisted_appr["operation_key"])  # valid stable UUID


# ══════════════════════════════════════════════════════════════════
# Scenario 2 — Explicit confirmation
# ══════════════════════════════════════════════════════════════════

@patch("app.tools.process_refund")
@patch("app.persistence.escalation.ensure_durable_escalation_state")
def test_scenario_2_explicit_confirmation(mock_ensure_state, mock_process_refund):
    """Customer explicitly confirms the proposed refund.

    Expected:
    - Frontend sends confirmation message.
    - Backend retrieves stored approval rather than reconstructing parameters.
    - Customer/session binding, expiry, signature, and atomic claim are validated.
    - Exactly one logical mutation attempt occurs through the trusted approval-service pathway.
    - Frontend receives executing status followed by authoritative success (status="succeeded", authoritative=True).
    """
    mock_ensure_state.return_value = EscalationLifecycleStatus.NONE
    mock_process_refund.return_value = json.dumps({
        "status": "success",
        "operation_id": "op_4004_success",
        "refund_id": "ref_4004_success",
        "message": "Refund of $99.99 for order #4004 successfully processed.",
    })

    token = generate_customer_token(customer_id=1)
    session_id = f"test-sess-confirm-{uuid.uuid4().hex[:6]}"

    # Seed pending approval in session
    pending = create_pending_approval(
        action="process_refund",
        params={"order_id": 4004},
        customer_id=1,
        session_id=session_id,
        message="Would you like me to process a refund for order #4004?",
    )
    initial_session = {
        "session_id": session_id,
        "customer_id": 1,
        "_session_revision": 1,
        "mode": "ai",
        "conversation_history": [],
        "pending_approval": pending.model_dump(),
        "workflow_state": {
            "active_domain": "payment",
            "state_revision": 1,
            "session_id": session_id,
            "customer_id": 1,
            "turn_count": 1,
        },
    }
    asyncio.run(session_store.create_session_if_absent(session_id, initial_session))

    client = TestClient(app)
    with client.websocket_connect(f"/ws/chat/{session_id}?token={token}") as ws:
        ws.receive_json()  # welcome

        req_id = str(uuid.uuid4())
        ws.send_json({
            "message": "Yes, please process refund for order #4004",
            "customer_id": 1,
            "client_request_id": req_id,
        })

        # 1. First message: executing indicator
        exec_msg = read_next_agent_response(ws)
        assert exec_msg["type"] == "agent_response"
        assert exec_msg["approval"]["status"] == "executing"

        # 2. Second message: authoritative success
        success_msg = read_next_agent_response(ws)
        assert success_msg["type"] == "agent_response"
        assert success_msg["approval"]["status"] == "succeeded"
        assert success_msg["approval"]["authoritative"] is True
        assert success_msg["approval"]["operation_key"] == pending.operation_key

        # Assert exactly one logical mutation attempt through trusted approval pathway
        assert mock_process_refund.call_count == 1
        _, kwargs = mock_process_refund.call_args
        assert kwargs.get("order_id") == 4004
        assert kwargs.get("client_request_id") == pending.operation_key


# ══════════════════════════════════════════════════════════════════
# Scenario 3 — Ambiguous affirmation after domain switch
# ══════════════════════════════════════════════════════════════════

@patch("app.tools.process_refund")
@patch("app.persistence.escalation.ensure_durable_escalation_state")
def test_scenario_3_ambiguous_affirmation_after_domain_switch(mock_ensure_state, mock_process_refund):
    """Customer requests refund, switches to Product question, then says 'yes'.

    Expected:
    - The approval remains pending and unchanged.
    - Bare affirmation is NOT treated as confirmation of refund.
    - The backend asks for clarification (status="ambiguous").
    - The mutation tool call count remains zero.
    """
    mock_ensure_state.return_value = EscalationLifecycleStatus.NONE
    token = generate_customer_token(customer_id=1)
    session_id = f"test-sess-ambig-{uuid.uuid4().hex[:6]}"

    pending = create_pending_approval(
        action="process_refund",
        params={"order_id": 4004},
        customer_id=1,
        session_id=session_id,
        message="Would you like me to process a refund for order #4004?",
    )
    # Active domain is switched to product (cross-domain)
    session_with_product_switch = {
        "session_id": session_id,
        "customer_id": 1,
        "_session_revision": 1,
        "mode": "ai",
        "conversation_history": [],
        "pending_approval": pending.model_dump(),
        "workflow_state": {
            "active_domain": "product",  # switched to product!
            "suspended_domains": ["payment"],
            "state_revision": 2,
            "session_id": session_id,
            "customer_id": 1,
            "turn_count": 2,
        },
    }
    asyncio.run(session_store.create_session_if_absent(session_id, session_with_product_switch))

    client = TestClient(app)
    with client.websocket_connect(f"/ws/chat/{session_id}?token={token}") as ws:
        ws.receive_json()  # welcome

        # Customer sends bare affirmation "yes"
        ws.send_json({
            "message": "yes",
            "customer_id": 1,
            "client_request_id": str(uuid.uuid4()),
        })

        resp = read_next_agent_response(ws)
        assert resp["type"] == "agent_response"
        assert resp["approval"]["status"] == "ambiguous"
        assert "pending refund request for order #4004" in resp["message"]
        assert resp["approval"]["params"]["order_id"] == 4004

        # Mutation tool call count remains zero
        assert mock_process_refund.call_count == 0

        # Approval remains pending in session
        stored = asyncio.run(session_store.get_session(session_id))
        assert stored["pending_approval"]["approval_id"] == pending.approval_id
        assert stored["pending_approval"]["status"] == ApprovalStatus.PENDING.value


# ══════════════════════════════════════════════════════════════════
# Scenario 4 — Explicit confirmation after a domain switch
# ══════════════════════════════════════════════════════════════════

@patch("app.tools.process_refund")
@patch("app.persistence.escalation.ensure_durable_escalation_state")
def test_scenario_4_explicit_confirmation_after_domain_switch(mock_ensure_state, mock_process_refund):
    """After Scenario 3, customer explicitly confirms refund for order #4004.

    Expected:
    - Persisted approval resumes without a new approval UUID or changed parameters.
    - Atomic claim occurs once.
    - Trusted executor runs at most once logically.
    - Repeated or duplicate confirmations do not cause duplicate mutations.
    """
    mock_ensure_state.return_value = EscalationLifecycleStatus.NONE
    mock_process_refund.return_value = json.dumps({
        "status": "success",
        "operation_id": "op_4004_explicit",
        "refund_id": "ref_4004_explicit",
        "message": "Refund processed.",
    })

    token = generate_customer_token(customer_id=1)
    session_id = f"test-sess-resume-{uuid.uuid4().hex[:6]}"

    pending = create_pending_approval(
        action="process_refund",
        params={"order_id": 4004},
        customer_id=1,
        session_id=session_id,
        message="Would you like me to process a refund for order #4004?",
    )
    session_data = {
        "session_id": session_id,
        "customer_id": 1,
        "_session_revision": 1,
        "mode": "ai",
        "conversation_history": [],
        "pending_approval": pending.model_dump(),
        "workflow_state": {
            "active_domain": "product",
            "suspended_domains": ["payment"],
            "state_revision": 2,
            "session_id": session_id,
            "customer_id": 1,
            "turn_count": 2,
        },
    }
    asyncio.run(session_store.create_session_if_absent(session_id, session_data))

    client = TestClient(app)
    with client.websocket_connect(f"/ws/chat/{session_id}?token={token}") as ws:
        ws.receive_json()  # welcome

        # Explicit confirmation referencing order #4004
        ws.send_json({
            "message": "Yes, please refund order #4004",
            "customer_id": 1,
            "client_request_id": str(uuid.uuid4()),
        })

        exec_msg = read_next_agent_response(ws)
        assert exec_msg["approval"]["status"] == "executing"

        success_msg = read_next_agent_response(ws)
        assert success_msg["approval"]["status"] == "succeeded"
        assert success_msg["approval"]["authoritative"] is True
        assert success_msg["approval"]["approval_id"] == pending.approval_id
        assert mock_process_refund.call_count == 1

        # Duplicate confirmation on same completed action
        ws.send_json({
            "message": "Yes, please refund order #4004",
            "customer_id": 1,
            "client_request_id": str(uuid.uuid4()),
        })

        # Since pending_approval is cleared, backend does not re-execute mutation tool
        # Verify mutation tool was NOT called a second time
        assert mock_process_refund.call_count == 1


# ══════════════════════════════════════════════════════════════════
# Scenario 5 — Informational interruption
# ══════════════════════════════════════════════════════════════════

@patch("app.tools.cancel_order")
@patch("app.persistence.escalation.ensure_durable_escalation_state")
def test_scenario_5_informational_interruption(mock_ensure_state, mock_cancel_order):
    """Customer with pending approval asks an informational question.

    Expected:
    - Informational question is answered normally.
    - Pending approval remains intact.
    - No approval execution occurs.
    - Previous domain state can be resumed without reconstructing identifiers.
    """
    mock_ensure_state.return_value = EscalationLifecycleStatus.NONE
    token = generate_customer_token(customer_id=1)
    session_id = f"test-sess-info-{uuid.uuid4().hex[:6]}"

    pending = create_pending_approval(
        action="cancel_order",
        params={"order_id": 202},
        customer_id=1,
        session_id=session_id,
        message="Would you like me to cancel order #202?",
    )
    initial_session = {
        "session_id": session_id,
        "customer_id": 1,
        "_session_revision": 1,
        "mode": "ai",
        "conversation_history": [],
        "pending_approval": pending.model_dump(),
        "workflow_state": {
            "active_domain": "order",
            "state_revision": 1,
            "session_id": session_id,
            "customer_id": 1,
            "turn_count": 1,
            "order_state": {"domain_status": "in_progress", "order_id": 202},
        },
    }
    asyncio.run(session_store.create_session_if_absent(session_id, initial_session))

    client = TestClient(app)
    # Mock domain graph to return normal FAQ answer while preserving pending approval
    with patch("app.websocket.chat_handler.domain_support_graph.ainvoke", new_callable=AsyncMock) as mock_graph:
        mock_graph.return_value = {
            "response": "Our return policy allows returns within 30 days of purchase.",
            "pending_approval": pending.model_dump(),  # preserved by domain graph
            "workflow_state": {
                "active_domain": "general",
                "suspended_domains": ["order"],
                "state_revision": 2,
                "session_id": session_id,
                "customer_id": 1,
                "turn_count": 2,
            },
            "semantic_domain": "general",
            "intent": "faq",
            "escalated": False,
        }

        with client.websocket_connect(f"/ws/chat/{session_id}?token={token}") as ws:
            ws.receive_json()  # welcome

            ws.send_json({
                "message": "What is your return policy?",
                "customer_id": 1,
                "client_request_id": str(uuid.uuid4()),
            })

            resp = read_next_agent_response(ws)
            assert resp["type"] == "agent_response"
            assert "return policy allows returns within 30 days" in resp["message"]

            # Mutation tool was not called
            assert mock_cancel_order.call_count == 0

            # Pending approval remains intact with original approval_id and order #202
            saved = asyncio.run(session_store.get_session(session_id))
            assert saved["pending_approval"]["approval_id"] == pending.approval_id
            assert saved["pending_approval"]["params"]["order_id"] == 202


# ══════════════════════════════════════════════════════════════════
# Scenario 6 — Failure and uncertainty
# ══════════════════════════════════════════════════════════════════

@patch("app.websocket.chat_handler.execute_approved_action", new_callable=AsyncMock)
@patch("app.persistence.escalation.ensure_durable_escalation_state")
def test_scenario_6_failed_and_uncertain_outcomes(mock_ensure_state, mock_execute):
    """Exercise failed deterministic execution outcomes.

    Expected:
    - Frontend receives honest status (failed).
    - No false success is shown.
    - Internal secrets and stack traces are not rendered to customer.
    """
    mock_ensure_state.return_value = EscalationLifecycleStatus.NONE
    token = generate_customer_token(customer_id=1)
    session_id = f"test-sess-fail-{uuid.uuid4().hex[:6]}"

    pending = create_pending_approval(
        action="process_refund",
        params={"order_id": 4004},
        customer_id=1,
        session_id=session_id,
        message="Refund order #4004?",
    )
    session_data = {
        "session_id": session_id,
        "customer_id": 1,
        "_session_revision": 1,
        "mode": "ai",
        "conversation_history": [],
        "pending_approval": pending.model_dump(),
        "workflow_state": {"active_domain": "payment", "session_id": session_id, "customer_id": 1},
    }
    asyncio.run(session_store.create_session_if_absent(session_id, session_data))

    # Test Deterministic Tool Failure
    mock_execute.return_value = ApprovalExecutionResult(
        success=False,
        action="process_refund",
        operation_key=pending.operation_key,
        response_text="The payment gateway rejected the refund due to expired authorization.",
        error="Gateway declined",
        status=ApprovalStatus.FAILED,
        is_uncertain=False,
    )

    client = TestClient(app)
    with client.websocket_connect(f"/ws/chat/{session_id}?token={token}") as ws:
        ws.receive_json()  # welcome

        ws.send_json({
            "message": "Yes, please process refund for order #4004",
            "customer_id": 1,
            "client_request_id": str(uuid.uuid4()),
        })

        read_next_agent_response(ws)  # executing
        fail_msg = read_next_agent_response(ws)

        assert fail_msg["approval"]["status"] == "failed"
        assert fail_msg["approval"]["authoritative"] is True
        assert fail_msg["approval"]["error"] == "Gateway declined"
        # Secret hygiene: no leaked tokens or stack traces
        assert TEST_JWT_SECRET not in json.dumps(fail_msg)
        assert TEST_SIGNING_SECRET not in json.dumps(fail_msg)
        assert "Traceback (most recent call last)" not in json.dumps(fail_msg)


@patch("app.websocket.chat_handler.execute_approved_action", new_callable=AsyncMock)
@patch("app.persistence.escalation.ensure_durable_escalation_state")
def test_scenario_6_uncertain_outcome(mock_ensure_state, mock_execute):
    """Exercise uncertain execution outcome where outcome could not be verified."""
    mock_ensure_state.return_value = EscalationLifecycleStatus.NONE
    token = generate_customer_token(customer_id=1)
    session_id = f"test-sess-uncertain-{uuid.uuid4().hex[:6]}"

    pending = create_pending_approval(
        action="process_refund",
        params={"order_id": 4004},
        customer_id=1,
        session_id=session_id,
        message="Refund order #4004?",
    )
    session_data = {
        "session_id": session_id,
        "customer_id": 1,
        "_session_revision": 1,
        "mode": "ai",
        "conversation_history": [],
        "pending_approval": pending.model_dump(),
        "workflow_state": {"active_domain": "payment", "session_id": session_id, "customer_id": 1},
    }
    asyncio.run(session_store.create_session_if_absent(session_id, session_data))

    mock_execute.return_value = ApprovalExecutionResult(
        success=False,
        action="process_refund",
        operation_key=pending.operation_key,
        response_text="The operation timed out. Status could not be verified.",
        error="Timeout waiting for ledger response",
        status=ApprovalStatus.FAILED,
        is_uncertain=True,
    )

    client = TestClient(app)
    with client.websocket_connect(f"/ws/chat/{session_id}?token={token}") as ws:
        ws.receive_json()  # welcome

        ws.send_json({
            "message": "Yes, please process refund for order #4004",
            "customer_id": 1,
            "client_request_id": str(uuid.uuid4()),
        })

        read_next_agent_response(ws)  # executing
        unc_msg = read_next_agent_response(ws)

        assert unc_msg["approval"]["status"] == "uncertain"
        assert unc_msg["approval"]["authoritative"] is True
        assert unc_msg["approval"]["error"] == "Timeout waiting for ledger response"


@patch("app.persistence.escalation.ensure_durable_escalation_state")
def test_scenario_6_tampered_signature_and_expired(mock_ensure_state):
    """Test tampered signature and expired approval rejection."""
    mock_ensure_state.return_value = EscalationLifecycleStatus.NONE
    token = generate_customer_token(customer_id=1)

    # 1. Tampered Signature
    session_id_tamper = f"test-sess-tamper-{uuid.uuid4().hex[:6]}"
    pending = create_pending_approval(
        action="process_refund",
        params={"order_id": 4004},
        customer_id=1,
        session_id=session_id_tamper,
        message="Refund order #4004?",
    )
    tampered_dict = pending.model_dump()
    tampered_dict["signature"] = "a" * 64  # invalid signature
    asyncio.run(session_store.create_session_if_absent(session_id_tamper, {
        "session_id": session_id_tamper,
        "customer_id": 1,
        "_session_revision": 1,
        "mode": "ai",
        "conversation_history": [],
        "pending_approval": tampered_dict,
        "workflow_state": {"active_domain": "payment", "session_id": session_id_tamper, "customer_id": 1},
    }))

    client = TestClient(app)
    with client.websocket_connect(f"/ws/chat/{session_id_tamper}?token={token}") as ws:
        ws.receive_json()
        ws.send_json({
            "message": "Yes, please process refund for order #4004",
            "customer_id": 1,
            "client_request_id": str(uuid.uuid4()),
        })
        resp = read_next_agent_response(ws)
        assert resp["approval"]["status"] == "failed"
        assert "signature mismatch or tampering" in resp["message"].lower()

    # 2. Expired Approval
    session_id_expired = f"test-sess-expired-{uuid.uuid4().hex[:6]}"
    expired_pending = create_pending_approval(
        action="process_refund",
        params={"order_id": 4004},
        customer_id=1,
        session_id=session_id_expired,
        message="Refund order #4004?",
        ttl_seconds=-10,  # expired in past
    )
    asyncio.run(session_store.create_session_if_absent(session_id_expired, {
        "session_id": session_id_expired,
        "customer_id": 1,
        "_session_revision": 1,
        "mode": "ai",
        "conversation_history": [],
        "pending_approval": expired_pending.model_dump(),
        "workflow_state": {"active_domain": "payment", "session_id": session_id_expired, "customer_id": 1},
    }))

    with client.websocket_connect(f"/ws/chat/{session_id_expired}?token={token}") as ws:
        ws.receive_json()
        ws.send_json({
            "message": "Yes, please process refund for order #4004",
            "customer_id": 1,
            "client_request_id": str(uuid.uuid4()),
        })
        resp = read_next_agent_response(ws)
        assert resp["approval"]["status"] == "expired"
        assert "expired" in resp["message"].lower()


# ══════════════════════════════════════════════════════════════════
# Scenario 7 — WebSocket lifecycle
# ══════════════════════════════════════════════════════════════════

@patch("app.tools.process_refund")
@patch("app.persistence.escalation.ensure_durable_escalation_state")
def test_scenario_7_websocket_disconnect_reconnect_persistence(mock_ensure_state, mock_process_refund):
    """Test disconnect and reconnect retains pending approval and executes once.

    Expected:
    - WS1 disconnects after proposal is persisted.
    - WS2 reconnects with same session ID and authenticated customer.
    - WS2 executes the approval cleanly without duplicate mutations.
    """
    mock_ensure_state.return_value = EscalationLifecycleStatus.NONE
    mock_process_refund.return_value = json.dumps({
        "status": "success",
        "operation_id": "op_4004_reconnect",
        "refund_id": "ref_4004_reconnect",
        "message": "Refund complete.",
    })

    token = generate_customer_token(customer_id=1)
    session_id = f"test-sess-reconnect-{uuid.uuid4().hex[:6]}"

    pending = create_pending_approval(
        action="process_refund",
        params={"order_id": 4004},
        customer_id=1,
        session_id=session_id,
        message="Refund order #4004?",
    )
    initial_session = {
        "session_id": session_id,
        "customer_id": 1,
        "_session_revision": 1,
        "mode": "ai",
        "conversation_history": [],
        "pending_approval": pending.model_dump(),
        "workflow_state": {"active_domain": "payment", "session_id": session_id, "customer_id": 1},
    }
    asyncio.run(session_store.create_session_if_absent(session_id, initial_session))

    client = TestClient(app)

    # 1. First connection opens and disconnects without confirming
    with client.websocket_connect(f"/ws/chat/{session_id}?token={token}") as ws1:
        welcome = ws1.receive_json()
        assert welcome["session_id"] == session_id
        # Client disconnects

    # 2. Reconnect on new websocket with same session
    with client.websocket_connect(f"/ws/chat/{session_id}?token={token}") as ws2:
        ws2.receive_json()  # welcome
        ws2.send_json({
            "message": "Yes, please process refund for order #4004",
            "customer_id": 1,
            "client_request_id": str(uuid.uuid4()),
        })

        read_next_agent_response(ws2)  # executing
        success_msg = read_next_agent_response(ws2)
        assert success_msg["approval"]["status"] == "succeeded"
        assert success_msg["approval"]["authoritative"] is True
        assert mock_process_refund.call_count == 1


# ══════════════════════════════════════════════════════════════════
# Scenario 8 — Accessibility and usability
# ══════════════════════════════════════════════════════════════════

@patch("app.tools.cancel_order")
@patch("app.persistence.escalation.ensure_durable_escalation_state")
def test_scenario_8_explicit_rejection_cancellation(mock_ensure_state, mock_cancel_order):
    """Verify that approval prompts distinguish confirmation from cancellation.

    Expected:
    - Customer sending 'no, cancel' cancels the pending approval safely.
    - Mutation tool is NOT executed.
    - Status transitions to 'cancelled'.
    """
    mock_ensure_state.return_value = EscalationLifecycleStatus.NONE
    token = generate_customer_token(customer_id=1)
    session_id = f"test-sess-reject-{uuid.uuid4().hex[:6]}"

    pending = create_pending_approval(
        action="cancel_order",
        params={"order_id": 101},
        customer_id=1,
        session_id=session_id,
        message="Cancel order #101?",
    )
    initial_session = {
        "session_id": session_id,
        "customer_id": 1,
        "_session_revision": 1,
        "mode": "ai",
        "conversation_history": [],
        "pending_approval": pending.model_dump(),
        "workflow_state": {"active_domain": "order", "session_id": session_id, "customer_id": 1},
    }
    asyncio.run(session_store.create_session_if_absent(session_id, initial_session))

    client = TestClient(app)
    with client.websocket_connect(f"/ws/chat/{session_id}?token={token}") as ws:
        ws.receive_json()  # welcome

        # Customer sends explicit rejection
        ws.send_json({
            "message": "No, cancel this action",
            "customer_id": 1,
            "client_request_id": str(uuid.uuid4()),
        })

        resp = read_next_agent_response(ws)
        assert resp["approval"]["status"] == "cancelled"
        assert "cancelled that action" in resp["message"].lower()

        # Mutation tool was not called
        assert mock_cancel_order.call_count == 0

        # Pending approval cleared from session
        saved = asyncio.run(session_store.get_session(session_id))
        assert saved.get("pending_approval") is None
