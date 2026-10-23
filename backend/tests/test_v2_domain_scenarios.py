"""End-to-End V2 Domain Scenarios and Cross-Domain Workflow State Integrity Tests.

Verifies:
1. General Domain: Grounded RAG knowledge base query with no transaction execution.
2. Product Domain: Product info & troubleshooting passing through Guardian.
3. Order Domain:
   - Read-only tracking with customer-scoped data.
   - Missing order ID triggers clarification without tool execution.
   - Cancellation requires and creates an explicit pending_approval structure.
4. Payment Domain:
   - Refund requires explicit approval and customer confirmation.
   - Billing failures query verified payment records and require specialist review.
5. Escalation Domain:
   - Severe issues (safety/legal, complex complaints) routed to Guardian.
   - Existing active ticket reused (no duplicate created).
6. Multi-turn Cross-Domain Integrity:
   - Product inquiry -> Order question (SUSPEND_AND_SWITCH).
   - Order question -> Resume Product inquiry (RESUME).
   - Approval-gated cancellation flow.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.domain_graph import domain_support_graph
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
)


def _route(domain: str, intent: str) -> SemanticRouteResult:
    return SemanticRouteResult(
        domain=domain,
        intent=intent,
        sentiment="neutral",
        urgency="medium",
        confidence=0.98,
        diagnostics=RouterDiagnostics(failure_type=RouterFailureType.SUCCESS),
    )


# ──────────────────────────────────────────────────────────────
#  1. Business Domain Scenarios
# ──────────────────────────────────────────────────────────────

class TestV2BusinessDomains:
    """Verifies that each domain performs its distinct responsibility end-to-end."""

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.general_agent.rag_agent.generate_response")
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_general_domain_uses_grounded_rag_knowledge(
        self, mock_classify, mock_rag, mock_cases, monkeypatch
    ):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
        mock_classify.return_value = _route("general", "faq")
        mock_rag.return_value = {
            "response": "Our standard return window is 30 days from purchase.",
            "sources": ["returns_policy.md"],
            "context_used": True,
        }

        state = {
            "session_id": "sess-gen-1",
            "customer_id": 101,
            "message": "What is your return policy?",
            "conversation_history": [],
            "workflow_state": {"session_id": "sess-gen-1", "customer_id": 101},
        }

        result = await domain_support_graph.ainvoke(state)

        assert result["semantic_domain"] == "general"
        assert result["guardian_action"] == GuardianAction.RESOLVE.value
        assert "30 days" in result["response"]
        assert result["tool_results"]["context_used"] is True
        assert result["escalated"] is False
        mock_rag.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.domain_graph.product_node", new_callable=AsyncMock)
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_product_domain_passes_through_guardian(
        self, mock_classify, mock_product, mock_cases, monkeypatch
    ):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_product")
        mock_classify.return_value = _route("product", "product_warranty")
        mock_product.return_value = {
            "response": "Your PulseSound Pro headphones have a 2-year warranty.",
            "escalated": False,
        }

        state = {
            "session_id": "sess-prod-1",
            "customer_id": 102,
            "message": "Is my PulseSound Pro under warranty?",
            "conversation_history": [],
            "workflow_state": {"session_id": "sess-prod-1", "customer_id": 102},
        }

        result = await domain_support_graph.ainvoke(state)

        assert result["semantic_domain"] == "product"
        assert result["guardian_action"] == GuardianAction.RESOLVE.value
        assert "2-year warranty" in result["response"]
        mock_product.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.order_agent.track_order")
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_order_domain_tracking_is_customer_scoped(
        self, mock_classify, mock_track, mock_cases, monkeypatch
    ):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_product_order")
        mock_classify.return_value = _route("order", "tracking")
        mock_track.return_value = json.dumps({
            "id": 8841,
            "status": "shipped",
            "tracking_number": "TRK-9921",
            "products": {"name": "Smart Watch Ultra"},
        })

        state = {
            "session_id": "sess-ord-1",
            "customer_id": 55,
            "message": "Where is my order #8841?",
            "conversation_history": [],
            "workflow_state": {"session_id": "sess-ord-1", "customer_id": 55},
        }

        result = await domain_support_graph.ainvoke(state)

        assert result["semantic_domain"] == "order"
        assert "Order #8841 for Smart Watch Ultra is currently shipped" in result["response"]
        assert "TRK-9921" in result["response"]
        mock_track.assert_called_once_with(8841, 55)

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.order_agent.track_order")
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_order_domain_missing_order_id_asks_for_clarification(
        self, mock_classify, mock_track, mock_cases, monkeypatch
    ):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_product_order")
        mock_classify.return_value = _route("order", "tracking")

        state = {
            "session_id": "sess-ord-2",
            "customer_id": 55,
            "message": "Where is my package?",  # No order ID provided
            "conversation_history": [],
            "workflow_state": {"session_id": "sess-ord-2", "customer_id": 55},
        }

        result = await domain_support_graph.ainvoke(state)

        assert result["semantic_domain"] == "order"
        assert "order number" in result["response"].lower()
        # Invariant: No mutation or query tool was called without identifier
        mock_track.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_order_domain_cancellation_is_approval_gated(
        self, mock_classify, mock_cases, monkeypatch
    ):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_product_order")
        mock_classify.return_value = _route("order", "cancel_order")

        state = {
            "session_id": "sess-ord-3",
            "customer_id": 55,
            "message": "Please cancel order #8841",
            "conversation_history": [],
            "workflow_state": {"session_id": "sess-ord-3", "customer_id": 55},
        }

        result = await domain_support_graph.ainvoke(state)

        assert result["semantic_domain"] == "order"
        assert result["pending_approval"] is not None
        assert result["pending_approval"]["action"] == "cancel_order"
        assert result["pending_approval"]["params"] == {"order_id": 8841}
        assert "Shall I go ahead?" in result["response"]

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_payment_domain_refund_is_approval_gated(
        self, mock_classify, mock_cases, monkeypatch
    ):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_core")
        mock_classify.return_value = _route("payment", "refund")

        state = {
            "session_id": "sess-pay-1",
            "customer_id": 77,
            "message": "I would like a refund for order #4002",
            "conversation_history": [],
            "workflow_state": {"session_id": "sess-pay-1", "customer_id": 77},
        }

        result = await domain_support_graph.ainvoke(state)

        assert result["semantic_domain"] == "payment"
        assert result["pending_approval"] is not None
        assert result["pending_approval"]["action"] == "process_refund"
        assert result["pending_approval"]["params"] == {"order_id": 4002}
        assert "Would you like me to proceed?" in result["response"]

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.create_ticket", return_value='{"ticket_id": 5501}')
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.payment_agent.get_payment_record")
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_payment_domain_billing_issue_uses_verified_context(
        self, mock_classify, mock_payment, mock_cases, mock_create, monkeypatch
    ):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_core")
        mock_classify.return_value = _route("payment", "billing_issue")
        mock_payment.return_value = json.dumps({
            "order_id": 4002,
            "status": "failed",
            "amount": 120.0,
        })

        state = {
            "session_id": "sess-pay-2",
            "customer_id": 77,
            "message": "Why was I charged twice for order #4002?",
            "conversation_history": [],
            "client_request_id": "req-pay-bill",
            "workflow_state": {"session_id": "sess-pay-2", "customer_id": 77},
        }

        result = await domain_support_graph.ainvoke(state)

        assert result["semantic_domain"] == "payment"
        assert result["guardian_action"] == GuardianAction.CREATE_TICKET.value
        assert "failed" in result["response"]
        assert "specialist" in result["response"].lower()
        mock_payment.assert_called_once_with(4002, 77)
        mock_create.assert_called_once()


# ──────────────────────────────────────────────────────────────
#  2. Multi-Turn Cross-Domain Workflow State Integrity
# ──────────────────────────────────────────────────────────────

class TestMultiTurnCrossDomainWorkflows:
    """Verifies that WorkflowState maintains typed domain states across switches and resumes."""

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
    @patch("app.agents.order_agent.track_order")
    @patch("app.agents.domain_graph.product_node", new_callable=AsyncMock)
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_product_inquiry_then_order_switch_suspends_and_tracks(
        self, mock_classify, mock_product, mock_track, mock_cases, monkeypatch
    ):
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

        # ── Turn 1: Product inquiry ──
        mock_classify.return_value = _route("product", "product_warranty")
        mock_product.return_value = {"response": "The headset has a 1-year warranty.", "escalated": False}

        state_t1 = {
            "session_id": "sess-cross-1",
            "customer_id": 44,
            "message": "What is the warranty for my headset?",
            "conversation_history": [],
            "workflow_state": {"session_id": "sess-cross-1", "customer_id": 44},
        }

        res_t1 = await domain_support_graph.ainvoke(state_t1)
        ws_t1 = res_t1["workflow_state"]
        assert ws_t1["active_domain"] == OrchestrationDomain.PRODUCT.value
        assert ws_t1["product_state"]["domain_status"] == DomainWorkflowStatus.IN_PROGRESS.value
        assert ws_t1["suspended_domains"] == []

        # ── Turn 2: Switch to order tracking (SUSPEND_AND_SWITCH) ──
        mock_classify.return_value = _route("order", "tracking")
        mock_track.return_value = json.dumps({"id": 1001, "status": "delivered", "products": {"name": "Headset"}})

        state_t2 = {
            "session_id": "sess-cross-1",
            "customer_id": 44,
            "message": "Also where is my order #1001?",
            "conversation_history": [
                {"role": "customer", "content": state_t1["message"]},
                {"role": "agent", "content": res_t1["response"]},
            ],
            "workflow_state": ws_t1,
        }

        res_t2 = await domain_support_graph.ainvoke(state_t2)
        ws_t2 = res_t2["workflow_state"]

        # Invariant: PRODUCT domain was cleanly suspended into suspended_domains
        assert ws_t2["active_domain"] == OrchestrationDomain.ORDER.value
        assert OrchestrationDomain.PRODUCT.value in ws_t2["suspended_domains"]
        assert ws_t2["product_state"]["domain_status"] == DomainWorkflowStatus.SUSPENDED.value
        assert ws_t2["order_state"]["domain_status"] == DomainWorkflowStatus.IN_PROGRESS.value
        assert "delivered" in res_t2["response"]

        # ── Turn 3: Switch back to Product inquiry (RESUME) ──
        mock_classify.return_value = _route("product", "product_warranty")
        mock_product.return_value = {"response": "Accidental damage is covered under extended plan.", "escalated": False}

        state_t3 = {
            "session_id": "sess-cross-1",
            "customer_id": 44,
            "message": "Does the warranty cover accidental damage?",
            "conversation_history": state_t2["conversation_history"] + [
                {"role": "customer", "content": state_t2["message"]},
                {"role": "agent", "content": res_t2["response"]},
            ],
            "workflow_state": ws_t2,
        }

        res_t3 = await domain_support_graph.ainvoke(state_t3)
        ws_t3 = res_t3["workflow_state"]

        # Invariant: Resumed product workflow; ORDER domain was suspended
        assert ws_t3["active_domain"] == OrchestrationDomain.PRODUCT.value
        assert ws_t3["product_state"]["domain_status"] == DomainWorkflowStatus.IN_PROGRESS.value
        assert OrchestrationDomain.ORDER.value in ws_t3["suspended_domains"]
        assert OrchestrationDomain.PRODUCT.value not in ws_t3["suspended_domains"]

    @pytest.mark.asyncio
    @patch("app.agents.domain_graph.update_ticket", return_value='{"status": "updated"}')
    @patch("app.agents.domain_graph.find_customer_support_cases")
    @patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
    async def test_unresolved_complaint_reuses_existing_active_ticket(
        self, mock_classify, mock_cases, mock_update, monkeypatch
    ):
        """Guardian must update the existing active ticket rather than creating duplicate tickets."""
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
        mock_classify.return_value = _route("order", "delivery_issue")
        mock_cases.return_value = json.dumps({
            "cases": [{
                "id": 9901,
                "status": "open",
                "type": "cancellation",
                "subject": "Issue: Delivery Issue - Order 123",
            }]
        })

        state = {
            "session_id": "sess-dup-test",
            "customer_id": 78,
            "message": "My order #123 was delayed again and I am very unhappy.",
            "conversation_history": [],
            "client_request_id": "req-dup-1",
            "workflow_state": {"session_id": "sess-dup-test", "customer_id": 78},
        }

        result = await domain_support_graph.ainvoke(state)

        assert result["guardian_action"] == GuardianAction.UPDATE_EXISTING_TICKET.value
        assert result["guardian_ticket_id"] == 9901
        assert "Ticket #9901" in result["response"]
        mock_update.assert_called_once()
