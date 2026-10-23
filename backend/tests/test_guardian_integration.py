"""Integration tests for the domain_graph guardian_response_node and _recent_cases paths.

These test the full integration boundary between the Guardian policy,
the ticket lookup tool, and the response composition.  They cover:
- DB failure paths (_recent_cases returns empty on error)
- Guardian ticket mutation error messaging
- Response composition accuracy
- Multi-domain routing through the graph
- Edge cases in _requests_human and _safety_or_legal_issue
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.domain_graph import (
    _recent_cases,
    _requests_human,
    _safety_or_legal_issue,
    guardian_response_node,
    route_after_supervisor,
)
from app.agents.guardian import GuardianAction
from app.config import settings


# ──────────────────────────────────────────────
#  _requests_human keyword detection
# ──────────────────────────────────────────────

class TestRequestsHumanDetection:
    def test_manager_triggers(self):
        assert _requests_human("I want to speak to a manager") is True

    def test_human_triggers(self):
        assert _requests_human("Get me a human right now") is True

    def test_representative_triggers(self):
        assert _requests_human("Can I talk to a representative?") is True

    def test_real_person_triggers(self):
        assert _requests_human("I want a real person") is True

    def test_agent_triggers(self):
        assert _requests_human("Let me speak to an agent") is True

    def test_normal_message_does_not_trigger(self):
        assert _requests_human("What is the return policy?") is False

    def test_case_insensitive(self):
        assert _requests_human("I WANT A MANAGER") is True


# ──────────────────────────────────────────────
#  _safety_or_legal_issue keyword detection
# ──────────────────────────────────────────────

class TestSafetyLegalDetection:
    def test_safety_triggers(self):
        assert _safety_or_legal_issue("This product is a safety hazard") is True

    def test_injury_triggers(self):
        assert _safety_or_legal_issue("My child got an injury from this") is True

    def test_legal_triggers(self):
        assert _safety_or_legal_issue("I will take legal action") is True

    def test_lawyer_triggers(self):
        assert _safety_or_legal_issue("My lawyer will contact you") is True

    def test_police_triggers(self):
        assert _safety_or_legal_issue("I'm reporting this to police") is True

    def test_normal_complaint_does_not_trigger(self):
        assert _safety_or_legal_issue("This product is terrible and I want a refund") is False


# ──────────────────────────────────────────────
#  _recent_cases DB failure resilience
# ──────────────────────────────────────────────

class TestRecentCasesResilience:
    @patch("app.agents.domain_graph.find_customer_support_cases")
    def test_db_error_returns_empty_list_and_failed_flag(self, mock_cases):
        mock_cases.return_value = '{"error": "connection timeout"}'
        cases, failed = _recent_cases(7)
        assert cases == []
        assert failed is True

    @patch("app.agents.domain_graph.find_customer_support_cases")
    def test_invalid_json_returns_empty_list_and_failed_flag(self, mock_cases):
        mock_cases.return_value = "not valid json at all"
        cases, failed = _recent_cases(7)
        assert cases == []
        assert failed is True

    @patch("app.agents.domain_graph.find_customer_support_cases")
    def test_exception_returns_empty_list_and_failed_flag(self, mock_cases):
        mock_cases.side_effect = RuntimeError("DB down")
        cases, failed = _recent_cases(7)
        assert cases == []
        assert failed is True

    @patch("app.agents.domain_graph.find_customer_support_cases")
    def test_malformed_cases_payload_returns_failed_flag(self, mock_cases):
        mock_cases.return_value = '{"cases": "not a list"}'
        cases, failed = _recent_cases(7)
        assert cases == []
        assert failed is True

    @patch("app.agents.domain_graph.find_customer_support_cases")
    def test_malformed_case_object_returns_failed_flag(self, mock_cases):
        mock_cases.return_value = '{"cases": [{"malformed": True}]}'
        cases, failed = _recent_cases(7)
        assert cases == []
        assert failed is True

    @patch("app.agents.domain_graph.find_customer_support_cases")
    def test_legitimate_empty_result_returns_false_flag(self, mock_cases):
        mock_cases.return_value = '{"customer_id": 7, "cases": []}'
        cases, failed = _recent_cases(7)
        assert cases == []
        assert failed is False

    def test_none_customer_id_returns_empty_list_and_false_flag(self):
        cases, failed = _recent_cases(None)
        assert cases == []
        assert failed is False

    def test_zero_customer_id_returns_empty_list_and_false_flag(self):
        cases, failed = _recent_cases(0)
        assert cases == []
        assert failed is False

    @patch("app.agents.domain_graph.find_customer_support_cases")
    def test_valid_cases_are_parsed(self, mock_cases):
        mock_cases.return_value = json.dumps({
            "cases": [
                {"id": 1, "status": "open", "type": "technical_issue", "subject": "Issue: Warranty - phone", "priority": "high"},
                {"id": 2, "status": "closed", "type": "billing", "subject": "Issue: Refund", "priority": "medium"},
            ]
        })
        cases, failed = _recent_cases(7)
        assert len(cases) == 2
        assert failed is False
        assert cases[0].id == 1
        assert cases[0].domain == "product"  # technical_issue -> product
        assert cases[0].intent == "warranty"
        assert cases[1].domain == "payment"  # billing -> payment


# ──────────────────────────────────────────────
#  Guardian response node integration
# ──────────────────────────────────────────────

class TestGuardianResponseNode:
    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    async def test_resolve_does_not_modify_response(self, mock_cases):
        """When Guardian resolves, the domain response passes through unchanged."""
        result = await guardian_response_node({
            "message": "What is the warranty for my headphones?",
            "response": "Your headphones have a 1-year warranty.",
            "semantic_domain": "product",
            "intent": "product_warranty",
            "customer_id": 7,
            "workflow_state": {"turn_count": 1},
        })
        assert result["guardian_action"] == "resolve"
        assert result["response"] == "Your headphones have a 1-year warranty."
        assert result["escalated"] is False

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.domain_graph.create_ticket", return_value='{"ticket_id": 901}')
    async def test_null_response_signals_requires_human_and_creates_ticket_when_history_verified(self, mock_create, mock_cases):
        """A None domain response triggers requires_human and creates a ticket if history verified."""
        result = await guardian_response_node({
            "message": "Help me with my order",
            "response": None,
            "semantic_domain": "order",
            "intent": "order_issue",
            "customer_id": 7,
            "client_request_id": "req-safe-901",
            "workflow_state": {"turn_count": 1},
        })
        assert result["guardian_action"] == "create_ticket"
        assert result["escalated"] is True
        assert result["guardian_ticket_id"] == 901
        assert "passing this to a support specialist" in result["response"]

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases")
    async def test_db_failure_when_resolvable_resolves_without_escalation(self, mock_cases):
        """When case lookup fails but request is resolvable, Guardian resolves without crashing."""
        mock_cases.return_value = '{"error": "timeout"}'
        result = await guardian_response_node({
            "message": "Track my order #123",
            "response": "Your order is shipped.",
            "semantic_domain": "order",
            "intent": "order_tracking",
            "customer_id": 7,
            "workflow_state": {"turn_count": 1},
        })
        assert result["guardian_action"] == "resolve"
        assert "shipped" in result["response"]
        assert result["escalated"] is False

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases")
    @patch("app.agents.domain_graph.create_ticket")
    async def test_db_failure_when_human_needed_escalates_without_ticket_mutation(self, mock_create, mock_cases):
        """When ticket history cannot be verified, Guardian must NOT create a ticket (avoids duplicates)."""
        mock_cases.return_value = '{"error": "connection timeout"}'
        result = await guardian_response_node({
            "message": "I need help with a broken screen",
            "response": None,  # Requires human
            "semantic_domain": "product",
            "intent": "hardware_defect",
            "customer_id": 7,
            "client_request_id": "req-safe-db-err",
            "workflow_state": {"turn_count": 1},
        })
        assert result["guardian_action"] == "escalate"
        assert result["escalated"] is True
        assert result["guardian_ticket_id"] is None
        assert "escalating this to a human support specialist" in result["response"]
        mock_create.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.update_ticket")
    @patch("app.agents.domain_graph.find_customer_support_cases")
    async def test_mutation_error_on_ticket_update_appends_honest_failure_message(self, mock_cases, mock_update):
        """When the ticket update fails, an honest error message is appended without claiming success."""
        mock_cases.return_value = json.dumps({
            "cases": [{"id": 100, "status": "open", "type": "technical_issue", "subject": "Issue: Warranty"}]
        })
        mock_update.return_value = '{"error": "DB write failed"}'
        result = await guardian_response_node({
            "message": "I still have the warranty issue",
            "response": "Your warranty is active.",
            "semantic_domain": "product",
            "intent": "warranty",
            "customer_id": 7,
            "client_request_id": "req-123",
            "workflow_state": {"turn_count": 2},
        })
        assert result["guardian_action"] == "update_existing_ticket"
        assert "couldn’t register" in result["response"] or "try again" in result["response"].lower()
        # Must NOT falsely claim that the ticket was updated
        assert "instead of creating a duplicate" not in result["response"]

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.create_ticket")
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    async def test_mutation_error_on_ticket_create_appends_honest_failure_message(self, mock_cases, mock_create):
        """When ticket creation fails, an honest failure message is appended without claiming success."""
        mock_create.return_value = '{"error": "duplicate request"}'
        result = await guardian_response_node({
            "message": "I need replacement parts",
            "response": None,
            "semantic_domain": "product",
            "intent": "replacement_parts",
            "customer_id": 7,
            "client_request_id": "req-fail-create",
            "workflow_state": {"turn_count": 1},
        })
        assert result["guardian_action"] == "create_ticket"
        assert "couldn’t register" in result["response"] or "try again" in result["response"].lower()
        # Must NOT falsely claim that handoff or ticket succeeded
        assert "passing this to a support specialist" not in result["response"]

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    async def test_safety_message_triggers_escalation(self, mock_cases):
        result = await guardian_response_node({
            "message": "This product caused an injury to my child",
            "response": "I'm sorry to hear that.",
            "semantic_domain": "product",
            "intent": "product_issue",
            "customer_id": 7,
            "workflow_state": {"turn_count": 1},
        })
        assert result["guardian_action"] == "escalate"
        assert result["escalated"] is True
        assert "escalating" in result["response"].lower()

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    async def test_missing_client_request_id_skips_mutation(self, mock_cases):
        """Guardian ticket creation is skipped when client_request_id is missing."""
        result = await guardian_response_node({
            "message": "I need a refund and I want a human",
            "response": None,  # None response → requires_human
            "semantic_domain": "payment",
            "intent": "refund_request",
            "customer_id": 7,
            # no client_request_id
            "workflow_state": {"turn_count": 1},
        })
        assert result["guardian_action"] == "create_ticket"
        # Mutation was skipped, so error message should appear
        assert "couldn’t register" in result["response"] or "try again" in result["response"].lower()

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    async def test_pending_approval_survives_guardian_response_node(self, mock_cases):
        """Pending approval state passes through guardian_response_node without executing prematurely."""
        pending = {"action": "cancel_order", "params": {"order_id": 12345}, "message": "Confirm cancellation?"}
        result = await guardian_response_node({
            "message": "Cancel order 12345",
            "response": "Are you sure you want to cancel order 12345? Please reply YES to confirm.",
            "semantic_domain": "order",
            "intent": "order_cancellation",
            "customer_id": 7,
            "client_request_id": "req-appr-1",
            "pending_approval": pending,
            "workflow_state": {"turn_count": 1},
        })
        assert result["guardian_action"] == "resolve"
        assert result["pending_approval"] == pending
        assert "Are you sure" in result["response"]
        assert result["escalated"] is False


# ──────────────────────────────────────────────
#  Routing edge cases
# ──────────────────────────────────────────────

class TestRoutingEdgeCases:
    def test_clarification_action_falls_to_safe_clarification(self):
        assert route_after_supervisor({
            "semantic_domain": "product",
            "supervisor_action": "request_clarification",
        }) == "clarification"

    def test_unknown_domain_falls_to_legacy(self):
        assert route_after_supervisor({
            "semantic_domain": "unknown",
            "supervisor_action": "start_new",
        }) == "legacy"

    def test_payment_v2_gated_by_feature_flag(self, monkeypatch):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_product")
        assert route_after_supervisor({
            "semantic_domain": "payment",
            "intent": "refund",
            "supervisor_action": "continue",
        }) == "legacy"

    def test_payment_v2_enabled_with_core_flag(self, monkeypatch):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_core")
        assert route_after_supervisor({
            "semantic_domain": "payment",
            "intent": "refund",
            "supervisor_action": "continue",
        }) == "payment"

    def test_order_v2_enabled_with_product_order_flag(self, monkeypatch):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_product_order")
        assert route_after_supervisor({
            "semantic_domain": "order",
            "intent": "tracking",
            "supervisor_action": "continue",
        }) == "order"

    def test_general_v2_requires_v2_all(self, monkeypatch):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_core")
        assert route_after_supervisor({
            "semantic_domain": "general",
            "intent": "faq",
            "supervisor_action": "continue",
        }) == "legacy"

    def test_escalation_supervisor_action_goes_to_guardian(self):
        assert route_after_supervisor({
            "semantic_domain": "order",
            "supervisor_action": "escalate",
        }) == "guardian"
