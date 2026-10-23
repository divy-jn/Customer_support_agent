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
