import os
from unittest.mock import AsyncMock, Mock

import pytest

from app.agents.semantic_router import classify_semantic_intent
from app.models import RouterFailureType

pytestmark = [
    pytest.mark.llm_eval(probe=False),
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_INCIDENT_ROUTING") != "1",
        reason="Set RUN_LIVE_INCIDENT_ROUTING=1 to make live model requests.",
    ),
]

# ──────────────────────────────────────────────
#  Targeted Semantic Router Evaluation Set
# ──────────────────────────────────────────────
# This does NOT modify the Golden Dataset. It acts as an independent evaluation
# of the new semantic router's capabilities for Phase B.

EVAL_CASES = [
    {
        "id": "1_general_faq",
        "message": "What are your store operating hours?",
        "history": [],
        "expected_domain": "general"
    },
    {
        "id": "2_product_issue",
        "message": "My new phone won't turn on.",
        "history": [],
        "expected_domain": "product"
    },
    {
        "id": "3_warranty_question",
        "message": "Is this laptop covered under the 1-year warranty?",
        "history": [],
        "expected_domain": "product"
    },
    {
        "id": "4_order_tracking",
        "message": "Please track my order #123. Where is it?",
        "history": [],
        "expected_domain": "order",
        "expected_tool": "track_order",
        "tracking_intents": {"track_order", "tracking", "order_tracking", "order_status"},
    },
    {
        "id": "5_cancellation",
        "message": "I need to cancel my order immediately before it ships.",
        "history": [],
        "expected_domain": "order"
    },
    {
        "id": "6_payment_failure",
        "message": "My credit card was declined at checkout but I see a charge.",
        "history": [],
        "expected_domain": "payment"
    },
    {
        "id": "7_refund",
        "message": "I want a refund for my recent purchase.",
        "history": [],
        "expected_domain": "payment"
    },
    {
        "id": "8_complaint_escalation",
        "message": "I am furious! Let me speak to a manager right now!",
        "history": [],
        "expected_domain": "escalation"
    },
    {
        "id": "9_multi_turn_continuation",
        "message": "Yes, please go ahead.",
        "history": [
            {"role": "user", "content": "I want to cancel order 123"},
            {"role": "assistant", "content": "I can cancel that. Are you sure?"}
        ],
        "expected_domain": "order" # Since context is about an order
    },
    {
        "id": "10_ambiguous_query",
        "message": "hello",
        "history": [],
        "expected_domain": "general"
    }
]

@pytest.mark.asyncio
@pytest.mark.parametrize("case", EVAL_CASES, ids=lambda c: c["id"])
async def test_semantic_routing_eval(case, monkeypatch):
    """
    Evaluates the semantic router against the 10 targeted test sets.
    Checks for:
    - Valid schema (SUCCESS)
    - Correct domain assignment
    - Read-only tool dispatch for the tracked order
    """
    result = await classify_semantic_intent(
        message=case["message"],
        conversation_history=case["history"]
    )
    
    # 1. System Reliability: Ensure the parser and network succeeded
    assert result.diagnostics.failure_type == RouterFailureType.SUCCESS, \
        f"Router system failure: {result.diagnostics.failure_type.value}"
        
    # 2. Semantic Correctness: Ensure the domain is correct
    assert result.domain == case["expected_domain"], \
        f"Expected domain '{case['expected_domain']}' but got '{result.domain}'. Intent was '{result.intent}'"


    if case.get("expected_tool") == "track_order":
        from app.agents import domain_graph, order_agent
        from app.config import settings

        assert result.intent.strip().lower() in case["tracking_intents"]
        monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
        monkeypatch.setattr(domain_graph.semantic_router, "classify_semantic_intent", AsyncMock(return_value=result))
        monkeypatch.setattr(domain_graph, "find_customer_support_cases", Mock(return_value='{"cases":[],"recent_count":0}'))
        track = Mock(return_value='{"id":123,"status":"shipped","tracking_number":"SYNTHETIC-123"}')
        monkeypatch.setattr(order_agent, "track_order", track)
        graph_result = await domain_graph.domain_support_graph.ainvoke({
            "message": case["message"], "conversation_history": case["history"],
            "session_id": "synthetic-tracking-eval", "customer_id": 7,
        })
        track.assert_called_once_with(123, 7)
        assert "SYNTHETIC-123" in graph_result["response"]
