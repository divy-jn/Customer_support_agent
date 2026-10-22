from unittest.mock import AsyncMock, patch

import pytest

from app.agents.domain_graph import (
    guardian_response_node,
    legacy_fallback_node,
    route_after_supervisor,
    semantic_supervisor_node,
    v2_product_node,
)
from app.config import settings
from app.models import RouterDiagnostics, RouterFailureType, SemanticRouteResult


def _semantic(domain: str, intent: str) -> SemanticRouteResult:
    return SemanticRouteResult(
        domain=domain,
        intent=intent,
        sentiment="neutral",
        urgency="medium",
        confidence=0.95,
        diagnostics=RouterDiagnostics(failure_type=RouterFailureType.SUCCESS),
    )


@pytest.mark.asyncio
@patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
async def test_product_uses_supervisor_and_v2_route(mock_classify):
    mock_classify.return_value = _semantic("product", "product_warranty")
    state = {
        "message": "What is the warranty for my headphones?",
        "conversation_history": [],
        "session_id": "v2-product-test",
        "customer_id": 7,
        "workflow_state": {"session_id": "v2-product-test", "customer_id": 7},
    }

    result = await semantic_supervisor_node(state)

    assert result["semantic_domain"] == "product"
    assert result["supervisor_action"] == "start_new"
    assert result["workflow_state"]["active_domain"] == "product"
    assert route_after_supervisor({**state, **result}) == "product"


@pytest.mark.asyncio
@patch("app.agents.domain_graph.product_node", new_callable=AsyncMock)
async def test_v2_product_node_delegates_to_hardened_product_agent(mock_product):
    mock_product.return_value = {"response": "Warranty details", "escalated": False}

    result = await v2_product_node({"message": "warranty"})

    assert result["response"] == "Warranty details"
    mock_product.assert_awaited_once()
    assert mock_product.await_args.args[0]["manage_tickets"] is False


@pytest.mark.asyncio
@patch("app.agents.domain_graph.update_ticket")
@patch("app.agents.domain_graph.find_customer_support_cases")
async def test_guardian_updates_existing_ticket_instead_of_creating_duplicate(mock_cases, mock_update):
    mock_cases.return_value = '{"cases": [{"id": 786, "status": "open", "type": "technical_issue", "subject": "Issue: Product Warranty"}]}'
    mock_update.return_value = '{"status": "success"}'
    result = await guardian_response_node({
        "message": "I need a manager about the warranty", "response": "Your warranty is active.",
        "semantic_domain": "product", "intent": "product_warranty", "customer_id": 7,
        "client_request_id": "request-1", "workflow_state": {"turn_count": 1},
    })

    assert result["guardian_action"] == "update_existing_ticket"
    assert result["guardian_ticket_id"] == 786
    assert "Ticket #786" in result["response"]
    mock_update.assert_called_once()


@pytest.mark.asyncio
@patch("app.agents.domain_graph.customer_support_graph.ainvoke", new_callable=AsyncMock)
async def test_non_product_domains_fall_back_to_legacy_graph(mock_legacy):
    mock_legacy.return_value = {"response": "Order status", "escalated": False}
    state = {"message": "Track order 12", "conversation_history": []}

    assert route_after_supervisor({**state, "semantic_domain": "order"}) == "legacy"
    result = await legacy_fallback_node(state)
    assert result["response"] == "Order status"
    mock_legacy.assert_awaited_once_with(state)


def test_product_escalation_is_owned_by_guardian_not_legacy_handoff():
    assert route_after_supervisor({
        "semantic_domain": "product", "supervisor_action": "escalate",
    }) == "guardian"


def test_order_and_general_v2_slices_also_cross_guardian_boundary(monkeypatch):
    monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")

    assert route_after_supervisor({
        "semantic_domain": "order", "intent": "tracking", "supervisor_action": "continue",
    }) == "order"
    assert route_after_supervisor({
        "semantic_domain": "general", "intent": "faq", "supervisor_action": "continue",
    }) == "general"
    # Delivery exceptions use the V2 agent only to make a Guardian-controlled
    # human handoff; it never invents fulfilment or refund decisions.
    assert route_after_supervisor({
        "semantic_domain": "order", "intent": "delivery_issue", "supervisor_action": "continue",
    }) == "order"


@pytest.mark.asyncio
@patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
async def test_guardian_uses_the_actual_order_domain_for_ticket_creation(mock_cases):
    result = await guardian_response_node({
        "message": "I need a human for my order", "response": "I can help with the order.",
        "semantic_domain": "order", "intent": "tracking", "customer_id": 7,
        "workflow_state": {"turn_count": 1},
    })

    # A solvable request remains automated; the domain is passed to policy
    # rather than being incorrectly treated as a product request.
    assert result["guardian_action"] == "continue"
    assert "complete this request here" in result["response"]


@pytest.mark.asyncio
@patch("app.agents.domain_graph.create_ticket")
@patch("app.agents.domain_graph.find_customer_support_cases")
async def test_guardian_escalates_without_mutation_when_ticket_history_is_unavailable(
    mock_cases, mock_create,
):
    mock_cases.return_value = '{"error": "database unavailable"}'
    result = await guardian_response_node({
        "message": "The device still does not turn on after troubleshooting.",
        "response": None,
        "semantic_domain": "product",
        "intent": "product_warranty",
        "customer_id": 7,
        "client_request_id": "history-outage-request",
        "workflow_state": {"turn_count": 1},
    })

    assert result["guardian_action"] == "escalate"
    assert result["escalated"] is True
    assert "history could not be verified" in result["guardian_reason"].lower()
    mock_create.assert_not_called()


@pytest.mark.asyncio
@patch("app.agents.domain_graph.create_ticket")
@patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
async def test_one_off_human_keyword_does_not_trigger_handoff_by_itself(mock_cases, mock_create):
    result = await guardian_response_node({
        "message": "I want to speak to a human",
        "response": None,
        "semantic_domain": "escalation",
        "intent": "human_handoff",
        "sentiment": "neutral",
        "urgency": "medium",
        "supervisor_action": "escalate",
        "customer_id": 7,
        "conversation_history": [],
        "workflow_state": {"turn_count": 1},
    })

    assert result["guardian_action"] == "continue"
    assert result["escalated"] is False
    assert "What would you like me to look into?" in result["response"]
    mock_create.assert_not_called()


@pytest.mark.asyncio
@patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
async def test_turn_count_alone_never_triggers_unresolved_attempt_escalation(mock_cases):
    result = await guardian_response_node({
        "message": "Can you confirm the order is on its way?",
        "response": "Your order is in transit.",
        "semantic_domain": "order",
        "intent": "tracking",
        "customer_id": 7,
        "workflow_state": {
            "session_id": "many-successful-turns",
            "turn_count": 12,
            "unresolved_attempts": 0,
        },
    })

    assert result["guardian_action"] == "resolve"
    assert result["escalated"] is False
    assert result["workflow_state"]["unresolved_attempts"] == 0


@pytest.mark.asyncio
@patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
async def test_third_actual_unresolved_attempt_triggers_escalation_and_persists_count(mock_cases):
    result = await guardian_response_node({
        "message": "The device still does not turn on.",
        "response": None,
        "semantic_domain": "product",
        "intent": "technical_support",
        "sentiment": "negative",
        "urgency": "medium",
        "customer_id": 7,
        "client_request_id": "unresolved-third-attempt",
        "workflow_state": {
            "session_id": "repeated-failure-session",
            "turn_count": 8,
            "unresolved_attempts": 2,
        },
    })

    assert result["guardian_action"] == "escalate"
    assert result["escalated"] is True
    assert result["workflow_state"]["unresolved_attempts"] == 3


@pytest.mark.asyncio
@patch("app.agents.domain_graph.find_customer_support_cases", return_value='{"cases": []}')
async def test_guardian_preserves_order_cancellation_approval_request(mock_cases):
    pending = {
        "action": "cancel_order",
        "params": {"order_id": 123},
        "message": "I’d like to cancel order #123. Shall I go ahead?",
        "customer_id": 7,
        "session_id": "approval-session",
    }
    result = await guardian_response_node({
        "message": "Cancel order 123",
        "response": pending["message"],
        "semantic_domain": "order",
        "intent": "order_cancellation",
        "customer_id": 7,
        "client_request_id": "approval-request-123",
        "pending_approval": pending,
        "workflow_state": {
            "session_id": "approval-session",
            "customer_id": 7,
            "turn_count": 1,
            "unresolved_attempts": 0,
        },
    })

    assert result["pending_approval"] == pending
    assert result["guardian_action"] == "resolve"
    assert result["escalated"] is False


@pytest.mark.asyncio
@patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
async def test_semantic_supervisor_reports_actual_rollout_mode(mock_classify, monkeypatch):
    monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
    mock_classify.return_value = _semantic("general", "faq")

    result = await semantic_supervisor_node({
        "message": "What is your return policy?",
        "conversation_history": [],
        "session_id": "mode-test-session",
        "customer_id": 7,
        "workflow_state": {"session_id": "mode-test-session", "customer_id": 7},
    })

    assert result["architecture_mode"] == "v2_all"
