import pytest
from unittest.mock import AsyncMock, patch
from app.agents.graph import customer_support_graph, AgentState, product_llm_adapter
from app.models import WorkflowState, WorkflowStatus
from app.agents.product_agent import ProductDomainContext

@pytest.fixture
def mock_product_llm():
    with patch("app.agents.graph.product_llm_adapter.invoke", new_callable=AsyncMock) as mock:
        yield mock

@pytest.fixture
def mock_intent_router():
    with patch("app.agents.intent_router.classify_intent", new_callable=AsyncMock) as mock:
        yield mock

@pytest.mark.asyncio
async def test_workflow_state_roundtrip(mock_intent_router, mock_product_llm):
    # Setup mock intent router to route to product domain
    mock_intent_router.return_value = {
        "intent": "product_inquiry",
        "route_to": "rag_agent"
    }

    # Setup mock LLM for ProductAgent to extract entity then generate final response
    async def mock_invoke(system, user):
        if "strict data extraction" in system:
            if "order 123" in user:
                return '{"product_name": null, "order_id": {"value": 123, "source": "USER_EXPLICIT"}, "manufacturer": null}'
            elif "SuperPhone X" in user:
                return '{"product_name": {"value": "SuperPhone X", "source": "USER_EXPLICIT"}, "order_id": null, "manufacturer": null}'
            return '{"product_name": null, "order_id": null, "manufacturer": null}'
        
        # Generation step
        return "I can help with that."

    mock_product_llm.side_effect = mock_invoke

    # TURN 1
    state_1: AgentState = {
        "customer_id": 999,
        "customer_name": "Test User",
        "session_id": "session_abc",
        "message": "My SuperPhone X stopped working",
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
        "workflow_state": WorkflowState(
            session_id="session_abc",
            customer_id=999,
        ).model_dump()
    }
    
    result_1 = await customer_support_graph.ainvoke(state_1)
    
    assert result_1["response"] == "I can help with that."
    assert "workflow_state" in result_1
    ws_1 = result_1["workflow_state"]
    assert ws_1["product_name"] == "SuperPhone X"
    assert ws_1["order_id"] is None
    assert ws_1["turn_count"] == 1

    # TURN 2
    state_2: AgentState = {
        "customer_id": 999,
        "customer_name": "Test User",
        "session_id": "session_abc",
        "message": "It is order 123",
        "conversation_history": [{"role": "customer", "content": "My SuperPhone X stopped working"}, {"role": "agent", "content": "I can help with that."}],
        "intent": "",
        "sentiment": "",
        "urgency": "",
        "route_to": "",
        "tool_results": None,
        "response": None,
        "escalated": False,
        "pending_approval": None,
        "approval_granted": None,
        "workflow_state": ws_1  # The persisted state from turn 1
    }

    result_2 = await customer_support_graph.ainvoke(state_2)
    
    ws_2 = result_2["workflow_state"]
    # Should merge
    assert ws_2["product_name"] == "SuperPhone X"
    assert ws_2["order_id"] == 123
    assert ws_2["turn_count"] == 2
