import json
from unittest.mock import AsyncMock, Mock

import pytest

from app.agents import domain_graph, order_agent
from app.config import settings
from app.models import RouterDiagnostics, RouterFailureType, SemanticRouteResult


@pytest.fixture(autouse=True)
def offline_graph(monkeypatch):
    monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
    cases = Mock(return_value='{"cases": [], "recent_count": 0}')
    monkeypatch.setattr(domain_graph, "find_customer_support_cases", cases)
    monkeypatch.setattr(domain_graph, "handle_general_request", AsyncMock(return_value={"response": "RAG reply"}))
    monkeypatch.setattr(domain_graph, "create_ticket", Mock(side_effect=AssertionError("unexpected ticket write")))
    monkeypatch.setattr(domain_graph, "update_ticket", Mock(side_effect=AssertionError("unexpected ticket write")))
    return cases


async def run_turn(monkeypatch, domain, intent, message, workflow=None):
    route = SemanticRouteResult(
        domain=domain, intent=intent, sentiment="neutral", urgency="low", confidence=0.95,
        is_continuation=True, entities={"order_id": "private value", "private key text": "omitted"},
        diagnostics=RouterDiagnostics(failure_type=RouterFailureType.SUCCESS),
    )
    monkeypatch.setattr(domain_graph.semantic_router, "classify_semantic_intent", AsyncMock(return_value=route))
    return await domain_graph.domain_support_graph.ainvoke({
        "message": message, "customer_id": 7, "session_id": "unit-session",
        "workflow_state": workflow, "conversation_history": [{"role": "customer", "content": message}],
    })


@pytest.mark.parametrize(("intent", "message"), [
    ("order_history_request", "give me my order history"),
    ("  OrDeR_HisTorY_ReQuEsT  ", "my order history"),
    ("show past orders", "show me what I bought"),
    ("list-orders-please", "show me what I bought"),
])
async def test_order_history_labels_dispatch_to_customer_lookup(monkeypatch, intent, message):
    history = Mock(return_value='{"recent_orders": [{"id": 42, "status": "shipped"}]}')
    monkeypatch.setattr(order_agent, "get_customer_history", history)
    result = await run_turn(monkeypatch, "order", intent, message)
    history.assert_called_once_with(7)
    assert "**Order #42**" in result["response"]
    assert result["intent"] == "view_order_history"


@pytest.mark.parametrize(("domain", "intent", "query"), [
    ("unknown", "unknown", "Check my ticket status"),
    ("general", "faq", "is there any active ticket for my account"),
])
@pytest.mark.parametrize("has_cases", [False, True])
async def test_ticket_queries_use_authenticated_cases(monkeypatch, offline_graph, domain, intent, query, has_cases):
    rows = [{
        "id": 42, "subject": "Delivery inquiry", "status": "in_progress",
        "updated_at": "2026-11-28T05:32:00Z",
    }] if has_cases else []
    offline_graph.return_value = json.dumps({"cases": rows, "recent_count": 0})
    result = await run_turn(monkeypatch, domain, intent, query)
    offline_graph.assert_called_once()
    assert offline_graph.call_args.args == (7,)
    domain_graph.handle_general_request.assert_not_awaited()
    assert result["guardian_action"] == "resolve"
    assert not result["escalated"]
    if has_cases:
        for value in ("#42", "Delivery inquiry", "In progress", "2026-11-28"):
            assert value in result["response"]
    else:
        assert "no active support tickets" in result["response"]


async def test_ticket_lookup_error_is_not_empty(monkeypatch, offline_graph):
    offline_graph.return_value = '{"error": "backend detail"}'
    result = await run_turn(monkeypatch, "general", "faq", "Check my ticket status")
    assert "couldn’t verify" in result["response"]
    assert "backend detail" not in result["response"]


@pytest.mark.parametrize("domain", ["order", "payment"])
async def test_unsupported_intent_runs_enabled_domain(monkeypatch, domain):
    handler = AsyncMock(return_value={"response": "domain reply"})
    monkeypatch.setattr(domain_graph, f"handle_{domain}_request", handler)
    result = await run_turn(monkeypatch, domain, "custom_request", "Could you look into this?")
    handler.assert_awaited_once()
    assert result["response"] == "domain reply"


async def test_repeated_clarifications_escalate(monkeypatch):
    workflow = None
    for attempt in range(1, 4):
        result = await run_turn(monkeypatch, "unknown", "unknown", "Could you look into this?", workflow)
        workflow = result["workflow_state"]
        assert workflow["unresolved_attempts"] == attempt
        assert result["escalated"] is (attempt == 3)
    assert result["guardian_action"] == "escalate"


@pytest.mark.parametrize("payload", ['{"error": "backend detail"}', '{}', 'invalid JSON'])
async def test_order_history_error_is_not_empty(monkeypatch, payload):
    monkeypatch.setattr(order_agent, "get_customer_history", Mock(return_value=payload))
    result = await order_agent.handle_order_request({
        "message": "my order history", "intent": "view_order_history", "customer_id": 7,
    })
    assert "couldn’t verify" in result["response"]
    assert "backend detail" not in result["response"]


async def test_conversational_continuation_honors_target_domain(monkeypatch):
    handler = AsyncMock(return_value={"response": "order follow-up"})
    monkeypatch.setattr(domain_graph, "handle_order_request", handler)
    result = await run_turn(monkeypatch, "general", "continuation", "and then?", {
        "session_id": "unit-session", "customer_id": 7, "active_domain": "order",
        "global_status": "in_progress", "order_state": {"domain_status": "in_progress"},
    })
    handler.assert_awaited_once()
    assert result["response"] == "order follow-up"
    assert result["semantic_domain"] == "general"
    assert result["supervisor_target_domain"] == "order"
    assert result["pre_turn_workflow_domain"] == "order"
    assert result["raw_classifier_intent"] == "continuation"
    assert result["semantic_confidence"] == 0.95
    assert result["is_continuation"] is True
    assert result["entity_keys"] == ["order_id"]
    assert result["semantic_failure_type"] == "success"



async def test_order_history_takes_precedence_over_remembered_order(monkeypatch):
    history = Mock(return_value='{"recent_orders": []}')
    tracking = Mock(side_effect=AssertionError("history must not track a remembered order"))
    monkeypatch.setattr(order_agent, "get_customer_history", history)
    monkeypatch.setattr(order_agent, "track_order", tracking)
    result = await run_turn(monkeypatch, "order", "order_history_request", "my order history", {
        "session_id": "unit-session", "customer_id": 7, "active_domain": "order",
        "global_status": "in_progress", "order_state": {"domain_status": "in_progress", "order_id": "42"},
    })
    assert "any past orders on file" in result["response"]
    history.assert_called_once_with(7)
    tracking.assert_not_called()
