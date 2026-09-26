import pytest
from unittest.mock import AsyncMock, patch
from app.agents.graph import customer_support_graph, AgentState, product_llm_adapter
from app.models import WorkflowState, WorkflowStatus
from app.agents.product_agent import ProductDomainContext, ProductAgent, ProductSkillResolver
from app.skills.runtime import SkillRuntime
from app.websocket.chat_handler import handle_customer_ws, session_store
from starlette.websockets import WebSocket
from app.agents.graph import GraphLLMAdapter
import json

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
        ).model_dump(mode="json")
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

def test_graph_llm_adapter_factory():
    """Prove the adapter is constructed through the factory."""
    adapter = GraphLLMAdapter()
    llm = adapter.llm
    # The factory returns an LLM with specific configuration
    assert llm.model_name is not None
    assert getattr(llm, "temperature", None) == 0.0

@pytest.mark.asyncio
async def test_product_agent_extraction():
    # Setup agent
    mock_llm_adapter = AsyncMock()
    agent = ProductAgent(ProductSkillResolver(None), SkillRuntime(None), mock_llm_adapter)
    
    # 1. Raw JSON
    mock_llm_adapter.invoke.return_value = '{"product_name": {"value": "SuperPhone", "source": "USER_EXPLICIT"}, "order_id": null, "manufacturer": null}'
    ctx = ProductDomainContext(customer_message="I have a SuperPhone", workflow_state=WorkflowState(session_id="test1"))
    state = await agent._extract_and_merge_state(ctx)
    assert state.product_name == "SuperPhone"

    # 2. Fenced JSON
    mock_llm_adapter.invoke.return_value = '```json\n{"product_name": {"value": "SuperPhone 2", "source": "USER_EXPLICIT"}, "order_id": null, "manufacturer": null}\n```'
    state = await agent._extract_and_merge_state(ctx)
    assert state.product_name == "SuperPhone 2"

    # 3. Malformed JSON
    mock_llm_adapter.invoke.return_value = '{"product_name": "missing brace'
    state = await agent._extract_and_merge_state(ctx)
    assert state.product_name is None # Original state unaffected

    # 4. Inferred Manufacturer (negative test)
    mock_llm_adapter.invoke.return_value = '{"product_name": null, "order_id": null, "manufacturer": {"value": "Dell", "source": "MODEL_INFERENCE"}}'
    state = await agent._extract_and_merge_state(ctx)
    assert state.manufacturer is None

    # 5. Explicit Manufacturer
    mock_llm_adapter.invoke.return_value = '{"product_name": null, "order_id": null, "manufacturer": {"value": "HP", "source": "USER_EXPLICIT"}}'
    state = await agent._extract_and_merge_state(ctx)
    assert state.manufacturer == "HP"

@pytest.mark.asyncio
async def test_chat_handler_session_persistence():
    # Mock WebSocket
    mock_ws = AsyncMock(spec=WebSocket)
    # Give it exactly two messages to simulate two turns
    # It loops `await websocket.receive_text()` until exception
    mock_ws.receive_text.side_effect = ["My phone broke", "It is a SuperPhone", Exception("disconnect")]
    
    # Mock intent router
    with patch("app.agents.intent_router.classify_intent", new_callable=AsyncMock) as mock_router:
        mock_router.return_value = {"intent": "product_inquiry", "route_to": "rag_agent"}
        
        # Mock product llm
        with patch("app.agents.graph.product_llm_adapter.invoke", new_callable=AsyncMock) as mock_llm:
            async def mock_invoke(system, user):
                if "strict data extraction" in system:
                    if "SuperPhone" in user:
                        return '{"product_name": {"value": "SuperPhone", "source": "USER_EXPLICIT"}, "order_id": null, "manufacturer": null}'
                    return '{"product_name": null, "order_id": null, "manufacturer": null}'
                return "How can I help?"
            mock_llm.side_effect = mock_invoke
            
            # Run the handler
            import uuid
            session_id = f"test_sess_pers_{uuid.uuid4().hex}"
            
            try:
                await handle_customer_ws(mock_ws, session_id=session_id)
            except Exception as e:
                if str(e) != "disconnect":
                    raise

            # Load session to verify workflow_state is persisted
            session = await session_store.get_session(session_id)
            assert session is not None
            assert "workflow_state" in session
            assert session["workflow_state"]["product_name"] == "SuperPhone"
            assert session["workflow_state"]["turn_count"] == 2

