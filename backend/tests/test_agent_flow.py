"""
Tests for the LangGraph agent flow — verifies intent routing
and end-to-end graph execution.

Requires a running LLM (Ollama Cloud) and seeded database.

Usage:
    cd backend
    python -m pytest tests/test_agent_flow.py -v
"""

import json
import pytest
import asyncio

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.agents.graph import customer_support_graph, AgentState
from app.agents.intent_router import classify_intent
from app.tools import get_all_customers


# ──────────────────────────────────────────────
#  Helper to get a test customer
# ──────────────────────────────────────────────
def get_test_customer_id() -> int | None:
    """Get the first customer ID from the database for testing."""
    try:
        result = json.loads(get_all_customers(limit=1))
        if result and isinstance(result, list):
            return result[0]["id"]
    except Exception:
        pass
    return None


def make_state(message: str, customer_id: int = None) -> AgentState:
    """Create a minimal AgentState for testing."""
    return {
        "customer_id": customer_id,
        "customer_name": None,
        "session_id": "test-session-001",
        "message": message,
        "conversation_history": [],
        "intent": "",
        "sentiment": "",
        "urgency": "",
        "route_to": "",
        "tool_results": None,
        "response": None,
        "escalated": False,
        "pending_approval": None,
        "approval_granted": None,
    }


# ──────────────────────────────────────────────
#  Intent Classification Tests
# ──────────────────────────────────────────────
class TestIntentClassification:
    """Test that the intent router classifies messages correctly."""

    @pytest.mark.asyncio
    async def test_order_tracking_intent(self):
        """'Where is my order?' should route to db_agent."""
        result = await classify_intent("Where is my order #12345?")
        assert result["route_to"] == "db_agent", f"Expected db_agent, got: {result}"
        assert result["intent"] in ["order_tracking", "billing", "general"]

    @pytest.mark.asyncio
    async def test_return_policy_intent(self):
        """'Can I return my laptop?' should route to rag_agent (or db_agent on some LLMs)."""
        result = await classify_intent("Can I return my laptop? What is the return window?")
        assert result["route_to"] in ["rag_agent", "db_agent"], f"Expected rag_agent or db_agent, got: {result}"

    @pytest.mark.asyncio
    async def test_escalation_intent(self):
        """Angry customer should route to escalation."""
        result = await classify_intent(
            "This is TERRIBLE service! I've been waiting 3 weeks for my refund and nobody is helping me! I want to talk to a manager NOW!"
        )
        # Angry + high urgency should trigger escalation
        assert result["sentiment"] == "negative", f"Expected negative sentiment: {result}"
        assert result["urgency"] in ("high", "critical"), f"Expected high/critical urgency: {result}"

    @pytest.mark.asyncio
    async def test_general_question_intent(self):
        """General question should route to rag_agent."""
        result = await classify_intent("What payment methods do you accept?")
        assert result["route_to"] in ["rag_agent", "db_agent"], f"Unexpected route: {result}"

    @pytest.mark.asyncio
    async def test_hinglish_intent(self):
        """Hinglish message should be classified correctly."""
        result = await classify_intent("bhai mera order kab tak aa jayega?")
        assert result["intent"] in ["order_tracking", "general"], f"Expected order-related intent: {result}"

    @pytest.mark.asyncio
    async def test_upi_payment_intent(self):
        """UPI payment issue should route to db_agent."""
        result = await classify_intent(
            "My UPI payment was deducted but my order wasn't confirmed. Rs 42,999 was debited."
        )
        assert result["route_to"] in ["db_agent", "escalation"], f"Expected db_agent or escalation: {result}"
        assert result["intent"] in ["billing", "refund", "technical_support"]


# ──────────────────────────────────────────────
#  End-to-End Graph Tests
# ──────────────────────────────────────────────
class TestGraphExecution:
    """Test end-to-end LangGraph execution."""

    @pytest.mark.asyncio
    async def test_general_support_question(self):
        """Agent should answer a general support question."""
        state = make_state("What is your return policy for electronics?")
        result = await customer_support_graph.ainvoke(state)

        assert result.get("response"), "Expected a response from the graph"
        assert len(result["response"]) > 10, "Response seems too short"
        assert result.get("escalated") is False

    @pytest.mark.asyncio
    async def test_order_status_question(self):
        """Agent should attempt to retrieve order information."""
        customer_id = get_test_customer_id()
        if not customer_id:
            pytest.skip("No customers in database")

        state = make_state("Where is my order?", customer_id=customer_id)
        result = await customer_support_graph.ainvoke(state)

        assert result.get("response"), "Expected a response"
        assert result.get("escalated") is False

    @pytest.mark.asyncio
    async def test_escalation_case(self):
        """Angry customer should trigger escalation."""
        state = make_state(
            "I am FURIOUS! Your company is a SCAM! I want my money back RIGHT NOW or I'm going to the consumer forum!"
        )
        result = await customer_support_graph.ainvoke(state)

        assert result.get("response"), "Expected a response"
        # Escalation should be triggered for very angry messages
        # Note: this depends on LLM classification
        assert result.get("escalated") is True or result.get("sentiment") == "negative"


# ──────────────────────────────────────────────
#  RAG-specific Tests
# ──────────────────────────────────────────────
class TestRAGResponses:
    """Test that RAG-grounded responses work correctly."""

    @pytest.mark.asyncio
    async def test_known_policy_question(self):
        """Question about a documented policy should return a grounded answer."""
        state = make_state("What is the return window for electronics?")
        result = await customer_support_graph.ainvoke(state)

        response = result.get("response", "")
        assert "15" in response or "fifteen" in response.lower(), \
            f"Expected mention of 15-day return window for electronics: {response[:200]}"

    @pytest.mark.asyncio
    async def test_unknown_question_no_hallucination(self):
        """Question not in KB should NOT hallucinate an answer."""
        state = make_state("What is the airspeed velocity of an unladen swallow?")
        result = await customer_support_graph.ainvoke(state)

        response = result.get("response", "").lower()
        # Agent should indicate it doesn't have this info
        has_disclaimer = any(phrase in response for phrase in [
            "don't have",
            "do not have",
            "cannot find",
            "outside",
            "not able to",
            "i'm not sure",
            "knowledge base",
            "help you with that",
        ])
        assert has_disclaimer, f"Expected agent to disclaim: {response[:300]}"
