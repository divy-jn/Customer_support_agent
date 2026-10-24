from unittest.mock import AsyncMock, patch

import pytest

from app.agents.general_agent import handle_general_request


@pytest.mark.asyncio
@patch("app.agents.general_agent.rag_agent.generate_response", new_callable=AsyncMock)
async def test_general_agent_returns_only_rag_result(mock_generate):
    mock_generate.return_value = {
        "response": "Returns are accepted within the policy window.",
        "sources": ["return_policy.md"],
        "context_used": True,
    }
    result = await handle_general_request({"message": "What is the return policy?", "conversation_history": []})
    assert result["response"] == "Returns are accepted within the policy window."
    assert result["tool_results"]["sources"] == ["return_policy.md"]
    assert result["escalated"] is False


@pytest.mark.asyncio
async def test_general_agent_answers_customer_name_from_state():
    result = await handle_general_request({
        "message": "what is my name",
        "customer_name": "Amit Sharma",
        "conversation_history": [],
    })
    assert result["response"] == "You are logged in as Amit Sharma."
    assert result["tool_results"]["source"] == "authenticated_session"
    assert result["escalated"] is False


@pytest.mark.asyncio
async def test_general_agent_answers_customer_id_fallback():
    result = await handle_general_request({
        "message": "who am i",
        "customer_id": 999,
        "conversation_history": [],
    })
    assert "999" in result["response"]
    assert result["tool_results"]["source"] == "authenticated_session"
    assert result["escalated"] is False

