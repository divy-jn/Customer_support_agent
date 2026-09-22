import pytest
import asyncio
from app.agents.semantic_router import classify_semantic_intent
from app.models import RouterFailureType

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
        "message": "Where is my order? I ordered it 3 days ago.",
        "history": [],
        "expected_domain": "order"
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
async def test_semantic_routing_eval(case):
    """
    Evaluates the semantic router against the 10 targeted test sets.
    Checks for:
    - Valid schema (SUCCESS)
    - Correct domain assignment
    """
    result = await classify_semantic_intent(
        message=case["message"],
        conversation_history=case["history"]
    )
    
    # 1. System Reliability: Ensure the parser and network succeeded
    assert result.diagnostics.failure_type == RouterFailureType.SUCCESS, \
        f"Router system failure: {result.diagnostics.error_message}"
        
    # 2. Semantic Correctness: Ensure the domain is correct
    assert result.domain == case["expected_domain"], \
        f"Expected domain '{case['expected_domain']}' but got '{result.domain}'. Intent was '{result.intent}'"
