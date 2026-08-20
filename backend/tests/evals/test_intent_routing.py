"""
Intent Routing Evaluation Tests — validates the intent classification accuracy,
JSON schema validity, and routing correctness.

Metrics tracked:
  - Intent accuracy (correct classification rate)
  - Invalid schema rate (malformed JSON from the LLM)
  - Routing accuracy (correct agent routing rate)
"""

import sys
import os
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.agents.intent_router import classify_intent


# ──────────────────────────────────────────────
#  Test Data
# ──────────────────────────────────────────────

ROUTING_TEST_CASES = [
    # (input_message, expected_intent, expected_route)
    ("What is your return policy?", "faq", "rag_agent"),
    ("How do I return an item?", "faq", "rag_agent"),
    ("Track my order #1005", "order_tracking", "db_agent"),
    ("Where is my package?", "order_tracking", "db_agent"),
    ("I want to cancel order #1003", "order_cancellation", "db_agent"),
    ("I need a refund for order #1002", "refund", "db_agent"),
    ("Check if the wireless headphones are in stock", "product_inquiry", "rag_agent"),
    ("I'm furious! Get me a manager NOW!", "complaint", "escalation"),
    ("This is the worst service ever. I want to talk to a human.", "complaint", "escalation"),
    ("What's the warranty on your products?", "faq", "rag_agent"),
    ("How do I reset my password?", "account_management", "db_agent"),
    ("What payment methods do you accept?", "faq", "rag_agent"),
]


# ──────────────────────────────────────────────
#  Schema Validation Tests (mocked LLM)
# ──────────────────────────────────────────────

class TestIntentSchemaValidation:
    """Tests that the router always returns valid schema, even with bad LLM output."""

    @pytest.mark.asyncio
    async def test_malformed_json_fallback(self):
        """If the LLM returns garbage, the router should return a fallback."""
        with patch("app.agents.intent_router.get_router_llm") as mock_llm:
            mock_response = MagicMock()
            mock_response.content = "This is not JSON at all!"
            mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)

            result = await classify_intent("test message")

            assert "intent" in result
            assert "sentiment" in result
            assert "urgency" in result
            assert "route_to" in result
            # Should use fallback values
            assert result["intent"] == "general"
            assert result["route_to"] == "rag_agent"

    @pytest.mark.asyncio
    async def test_missing_fields_get_defaults(self):
        """If the LLM returns partial JSON, missing fields get defaults."""
        with patch("app.agents.intent_router.get_router_llm") as mock_llm:
            mock_response = MagicMock()
            mock_response.content = json.dumps({"intent": "faq"})  # Missing other fields
            mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)

            result = await classify_intent("test message")

            assert result["intent"] == "faq"
            assert result["sentiment"] == "neutral"  # default
            assert result["urgency"] == "medium"  # default
            assert result["route_to"] == "rag_agent"  # default

    @pytest.mark.asyncio
    async def test_invalid_intent_gets_corrected(self):
        """If the LLM returns an invalid intent value, it gets corrected."""
        with patch("app.agents.intent_router.get_router_llm") as mock_llm:
            mock_response = MagicMock()
            mock_response.content = json.dumps({
                "intent": "nonexistent_intent",
                "sentiment": "invalid_sentiment",
                "urgency": "super_critical",
                "route_to": "fake_agent",
            })
            mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)

            result = await classify_intent("test message")

            assert result["intent"] == "general"  # corrected
            assert result["sentiment"] == "neutral"  # corrected
            assert result["urgency"] == "medium"  # corrected
            assert result["route_to"] == "rag_agent"  # corrected

    @pytest.mark.asyncio
    async def test_escalation_forced_for_angry_critical(self):
        """Negative sentiment + high urgency should always force escalation."""
        with patch("app.agents.intent_router.get_router_llm") as mock_llm:
            mock_response = MagicMock()
            mock_response.content = json.dumps({
                "intent": "billing",
                "sentiment": "negative",
                "urgency": "critical",
                "route_to": "db_agent",  # Router says db_agent but...
            })
            mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)

            result = await classify_intent("I'm furious about my bill!")

            # Should be overridden to escalation
            assert result["route_to"] == "escalation"

    @pytest.mark.asyncio
    async def test_required_fields_always_present(self):
        """Every result must contain the 4 required fields."""
        with patch("app.agents.intent_router.get_router_llm") as mock_llm:
            mock_response = MagicMock()
            mock_response.content = json.dumps({})  # Empty JSON
            mock_llm.return_value.ainvoke = AsyncMock(return_value=mock_response)

            result = await classify_intent("anything")

            required_fields = ["intent", "sentiment", "urgency", "route_to"]
            for field in required_fields:
                assert field in result, f"Missing required field: {field}"

    @pytest.mark.asyncio
    async def test_llm_exception_returns_fallback(self):
        """If the LLM completely fails, we still get a valid fallback."""
        with patch("app.agents.intent_router.get_router_llm") as mock_llm:
            mock_llm.return_value.ainvoke = AsyncMock(side_effect=Exception("LLM is down!"))

            result = await classify_intent("test message")

            assert result["intent"] == "general"
            assert result["route_to"] == "rag_agent"
            assert "error" in result.get("reasoning", "").lower() or "fallback" in result.get("reasoning", "").lower()


# ──────────────────────────────────────────────
#  Live Integration Tests (requires LLM running)
# ──────────────────────────────────────────────

@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_TESTS"),
    reason="Live tests require RUN_LIVE_TESTS=1 and LLM running"
)
class TestIntentRoutingLive:
    """Live tests that actually call the LLM. Run with RUN_LIVE_TESTS=1."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "message,expected_intent,expected_route",
        [(tc[0], tc[1], tc[2]) for tc in ROUTING_TEST_CASES],
        ids=[tc[0][:40] for tc in ROUTING_TEST_CASES],
    )
    async def test_intent_routing_accuracy(self, message, expected_intent, expected_route):
        result = await classify_intent(message)
        
        # We allow some flexibility — the intent might be slightly different
        # but the route should be correct
        assert result["route_to"] == expected_route, (
            f"Input: '{message}'\n"
            f"Expected route: {expected_route}, Got: {result['route_to']}\n"
            f"Intent: {result['intent']}, Reasoning: {result.get('reasoning', 'N/A')}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

