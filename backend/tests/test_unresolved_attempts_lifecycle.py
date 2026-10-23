"""Deterministic regression tests for the unresolved_attempts lifecycle.

Verifies:
1. Field presence and bounds on WorkflowState.
2. Serialization and adapter survival through WorkflowState.from_legacy() and model_dump().
3. Consecutive failure counting across realistic multi-turn execution.
4. Escalation triggered on the 3rd consecutive unresolved attempt.
5. Successful resolution clearing the consecutive failure count to 0.
6. High turn_count without unresolved failures never causing escalation.
7. Reconnect / JSON serialization preserving the counter without reset.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.domain_graph import (
    guardian_response_node,
    semantic_supervisor_node,
)
from app.agents.guardian import GuardianAction
from app.agents.supervisor import SupervisorAction
from app.config import settings
from app.models import (
    DomainWorkflowStatus,
    GlobalWorkflowStatus,
    OrchestrationDomain,
    RouterDiagnostics,
    RouterFailureType,
    SemanticRouteResult,
    WorkflowState,
)


def _mock_semantic(domain: str, intent: str) -> SemanticRouteResult:
    return SemanticRouteResult(
        domain=domain,
        intent=intent,
        sentiment="neutral",
        urgency="medium",
        confidence=0.95,
        diagnostics=RouterDiagnostics(failure_type=RouterFailureType.SUCCESS),
    )


class TestUnresolvedAttemptsModelContract:
    """Contract tests for unresolved_attempts in WorkflowState and adapters."""

    def test_default_unresolved_attempts_is_zero(self):
        state = WorkflowState(session_id="test-session-1")
        assert state.unresolved_attempts == 0

    def test_unresolved_attempts_ge_zero_enforced(self):
        with pytest.raises(ValueError):
            WorkflowState(session_id="test-session-2", unresolved_attempts=-1)

    def test_from_legacy_reads_unresolved_attempts(self):
        legacy = {
            "session_id": "test-session-3",
            "active_domain": "product",
            "unresolved_attempts": 2,
        }
        state = WorkflowState.from_legacy(legacy)
        assert state.unresolved_attempts == 2

    def test_roundtrip_json_serialization_preserves_unresolved_attempts(self):
        original = WorkflowState.from_legacy({
            "session_id": "test-session-4",
            "active_domain": OrchestrationDomain.PRODUCT.value,
            "unresolved_attempts": 2,
        })
        dumped = original.model_dump(mode="json")
        assert dumped["unresolved_attempts"] == 2

        restored = WorkflowState.from_legacy(dumped)
        assert restored.unresolved_attempts == 2

    def test_reconnect_simulation_preserves_counter(self):
        """Simulate disconnect: state serialized to JSON string in session store and reloaded."""
        state = WorkflowState.from_legacy({
            "session_id": "sess-reconnect",
            "customer_id": 42,
            "active_domain": "order",
            "unresolved_attempts": 2,
            "turn_count": 3,
        })
        # Store as string (e.g. Redis / PostgreSQL session column)
        raw_storage = json.dumps(state.model_dump(mode="json"))

        # Reconnect: reload and parse
        loaded_dict = json.loads(raw_storage)
        reconnected_state = WorkflowState.from_legacy(loaded_dict)

        assert reconnected_state.unresolved_attempts == 2
        assert reconnected_state.turn_count == 3
        assert reconnected_state.customer_id == 42


class TestUnresolvedAttemptsMultiTurnLifecycle:
    """Multi-turn state-transition tests through the actual supervisor and guardian nodes."""

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.domain_graph.create_ticket", return_value='{"ticket_id": 7001}')
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_three_consecutive_unresolved_attempts_escalate(
        self, mock_classify, mock_create, mock_cases, monkeypatch
    ):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

        # ── TURN 1: First Unresolved Attempt ──
        mock_classify.return_value = _mock_semantic("product", "hardware_defect")
        state_turn1 = {
            "session_id": "sess-multi-1",
            "customer_id": 99,
            "message": "My speaker is completely dead and emitting smoke.",
            "conversation_history": [],
            "client_request_id": "req-turn-1",
            "workflow_state": {
                "session_id": "sess-multi-1",
                "customer_id": 99,
                "turn_count": 0,
                "unresolved_attempts": 0,
            },
        }

        # Step 1-4: Supervisor evaluates and applies transition
        sup_result1 = await semantic_supervisor_node(state_turn1)
        assert sup_result1["supervisor_action"] == SupervisorAction.START_NEW.value
        assert sup_result1["workflow_state"]["unresolved_attempts"] == 0

        # Step 5: Domain agent execution fails or requires human
        domain_result1 = {
            **state_turn1,
            **sup_result1,
            "response": None,  # Hardware failure cannot be automated
            "requires_human": True,
        }

        # Step 6-7: Guardian policy & state persistence
        guard_result1 = await guardian_response_node(domain_result1)
        assert guard_result1["guardian_action"] == GuardianAction.CREATE_TICKET.value
        assert guard_result1["workflow_state"]["unresolved_attempts"] == 1
        assert guard_result1["workflow_state"]["turn_count"] == 1

        # ── TURN 2: Second Unresolved Attempt ──
        persisted_ws_turn1 = guard_result1["workflow_state"]
        mock_classify.return_value = _mock_semantic("product", "hardware_defect")
        state_turn2 = {
            "session_id": "sess-multi-1",
            "customer_id": 99,
            "message": "Is anyone going to help me? The speaker is still dead.",
            "conversation_history": [{"role": "customer", "content": state_turn1["message"]}],
            "client_request_id": "req-turn-2",
            "workflow_state": persisted_ws_turn1,
        }

        sup_result2 = await semantic_supervisor_node(state_turn2)
        assert sup_result2["supervisor_action"] == SupervisorAction.CONTINUE.value
        # Invariant: counter survived supervisor transition
        assert sup_result2["workflow_state"]["unresolved_attempts"] == 1

        domain_result2 = {
            **state_turn2,
            **sup_result2,
            "response": None,
            "requires_human": True,
        }

        guard_result2 = await guardian_response_node(domain_result2)
        assert guard_result2["workflow_state"]["unresolved_attempts"] == 2
        assert guard_result2["workflow_state"]["turn_count"] == 2

        # ── TURN 3: Third Unresolved Attempt -> Triggers Escalation ──
        persisted_ws_turn2 = guard_result2["workflow_state"]
        mock_classify.return_value = _mock_semantic("product", "hardware_defect")
        state_turn3 = {
            "session_id": "sess-multi-1",
            "customer_id": 99,
            "message": "Still not working. Hello?",
            "conversation_history": [{"role": "customer", "content": state_turn2["message"]}],
            "client_request_id": "req-turn-3",
            "workflow_state": persisted_ws_turn2,
        }

        sup_result3 = await semantic_supervisor_node(state_turn3)
        assert sup_result3["workflow_state"]["unresolved_attempts"] == 2

        domain_result3 = {
            **state_turn3,
            **sup_result3,
            "response": None,
            "requires_human": True,
        }

        guard_result3 = await guardian_response_node(domain_result3)
        # CRITICAL ASSERTION: The 3rd unresolved attempt forces ESCALATE
        assert guard_result3["guardian_action"] == GuardianAction.ESCALATE.value
        assert guard_result3["escalated"] is True
        assert "Three or more unresolved attempts require human escalation" in guard_result3["guardian_reason"]
        assert guard_result3["workflow_state"]["unresolved_attempts"] == 3
        assert guard_result3["workflow_state"]["global_status"] == GlobalWorkflowStatus.ESCALATED.value

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_successful_resolution_clears_unresolved_attempts(
        self, mock_classify, mock_cases, monkeypatch
    ):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

        # Prior state had 2 previous unresolved failures
        prior_state = {
            "session_id": "sess-recover-1",
            "customer_id": 105,
            "active_domain": "product",
            "turn_count": 2,
            "unresolved_attempts": 2,
        }

        mock_classify.return_value = _mock_semantic("product", "product_warranty")
        state = {
            "session_id": "sess-recover-1",
            "customer_id": 105,
            "message": "What is the warranty policy?",
            "conversation_history": [],
            "client_request_id": "req-recover-1",
            "workflow_state": prior_state,
        }

        sup_result = await semantic_supervisor_node(state)
        assert sup_result["workflow_state"]["unresolved_attempts"] == 2

        # Domain resolves successfully
        domain_result = {
            **state,
            **sup_result,
            "response": "Your product warranty is active for 2 years.",
            "requires_human": False,
        }

        guard_result = await guardian_response_node(domain_result)
        assert guard_result["guardian_action"] == GuardianAction.RESOLVE.value
        # INVARIANT: Successful resolution clears the failure count to 0
        assert guard_result["workflow_state"]["unresolved_attempts"] == 0
        assert guard_result["workflow_state"]["turn_count"] == 3
        assert guard_result["escalated"] is False

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_ordinary_high_turn_count_never_causes_escalation(
        self, mock_classify, mock_cases, monkeypatch
    ):
        """Prove that turn_count=10 with zero unresolved attempts does NOT trigger escalation."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

        high_turn_state = {
            "session_id": "sess-high-turn",
            "customer_id": 200,
            "active_domain": "product",
            "turn_count": 10,  # Far exceeds 3
            "unresolved_attempts": 0,
        }

        mock_classify.return_value = _mock_semantic("product", "product_inquiry")
        state = {
            "session_id": "sess-high-turn",
            "customer_id": 200,
            "message": "Does this model have Bluetooth 5.3?",
            "conversation_history": [],
            "client_request_id": "req-high-turn",
            "workflow_state": high_turn_state,
        }

        sup_result = await semantic_supervisor_node(state)
        domain_result = {
            **state,
            **sup_result,
            "response": "Yes, it supports Bluetooth 5.3.",
            "requires_human": False,
        }

        guard_result = await guardian_response_node(domain_result)
        assert guard_result["guardian_action"] == GuardianAction.RESOLVE.value
        assert guard_result["escalated"] is False
        assert guard_result["workflow_state"]["unresolved_attempts"] == 0
        assert guard_result["workflow_state"]["turn_count"] == 11
