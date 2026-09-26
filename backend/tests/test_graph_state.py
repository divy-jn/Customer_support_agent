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

    # TURN 3 - Continuation with manufacturer
    state_3: AgentState = {
        "customer_id": 999,
        "customer_name": "Test User",
        "session_id": "session_abc",
        "message": "It is Dell",
        "conversation_history": state_2["conversation_history"] + [{"role": "customer", "content": "It is order 123"}, {"role": "agent", "content": "I can help with that."}],
        "intent": "general", # Should continue anyway due to active workflow
        "sentiment": "",
        "urgency": "",
        "route_to": "",
        "tool_results": None,
        "response": None,
        "escalated": False,
        "pending_approval": None,
        "approval_granted": None,
        "workflow_state": ws_2
    }
    
    async def mock_invoke_3(system, user):
        if "strict data extraction" in system:
            return '{"product_name": null, "order_id": null, "manufacturer": {"value": "Dell", "source": "USER_EXPLICIT"}}'
        return "Manufacturer recorded."
    mock_product_llm.side_effect = mock_invoke_3
    
    result_3 = await customer_support_graph.ainvoke(state_3)
    ws_3 = result_3["workflow_state"]
    assert ws_3["manufacturer"] == "Dell"

from app.agents.graph import route_after_classification

def test_workflow_continuation_routing():
    # Active product workflow waiting for input
    state = {
        "intent": "faq",
        "route_to": "rag_agent",
        "workflow_state": {
            "workflow_status": "awaiting_input",
            "active_domain": "product"
        }
    }
    # It should break out because faq is a hard switch
    assert route_after_classification(state) == "rag_node"
    
    # Active product workflow getting order ID (might be misclassified as general by intent router)
    state["intent"] = "order_tracking"
    state["route_to"] = "db_agent"
    assert route_after_classification(state) == "product_node"
    
    # Hard switch to escalation
    state["intent"] = "complaint"
    state["route_to"] = "escalation"
    assert route_after_classification(state) == "escalation_node"

@patch("app.llm_factory.get_llm")
def test_graph_llm_adapter_factory(mock_get_llm):
    """Prove the adapter is constructed through the factory."""
    with patch("app.config.settings.llm_small_model", "test-model-42"):
        adapter = GraphLLMAdapter()
        llm = adapter.llm
        mock_get_llm.assert_called_once_with(model="test-model-42", temperature=0.0)
        assert llm == mock_get_llm.return_value

@pytest.mark.asyncio
async def test_product_agent_extraction():
    # Setup agent
    mock_llm_adapter = AsyncMock()
    agent = ProductAgent(ProductSkillResolver(None), SkillRuntime(None), mock_llm_adapter)
    
    # Base context
    ctx = ProductDomainContext(customer_message="I have a SuperPhone from HP, order 1234", workflow_state=WorkflowState(session_id="test1"))

    # A. explicit manufacturer in message -> accepted
    mock_llm_adapter.invoke.return_value = '{"product_name": null, "order_id": null, "manufacturer": {"value": "HP", "source": "USER_EXPLICIT"}}'
    state = await agent._extract_and_merge_state(ctx)
    assert state.manufacturer == "HP"

    # B. manufacturer absent from message but LLM says USER_EXPLICIT -> rejected
    ctx_no_mfg = ProductDomainContext(customer_message="I have a SuperPhone", workflow_state=WorkflowState(session_id="test1"))
    mock_llm_adapter.invoke.return_value = '{"product_name": null, "order_id": null, "manufacturer": {"value": "Dell", "source": "USER_EXPLICIT"}}'
    state = await agent._extract_and_merge_state(ctx_no_mfg)
    assert state.manufacturer is None

    # C. manufacturer marked MODEL_INFERENCE -> rejected
    mock_llm_adapter.invoke.return_value = '{"product_name": null, "order_id": null, "manufacturer": {"value": "HP", "source": "MODEL_INFERENCE"}}'
    state = await agent._extract_and_merge_state(ctx)
    assert state.manufacturer is None

    # D. explicit order ID -> accepted
    mock_llm_adapter.invoke.return_value = '{"product_name": null, "order_id": {"value": 1234, "source": "USER_EXPLICIT"}, "manufacturer": null}'
    state = await agent._extract_and_merge_state(ctx)
    assert state.order_id == 1234

    # E. hallucinated order ID not present in message -> rejected
    mock_llm_adapter.invoke.return_value = '{"product_name": null, "order_id": {"value": 9999, "source": "USER_EXPLICIT"}, "manufacturer": null}'
    state = await agent._extract_and_merge_state(ctx)
    assert state.order_id is None

    # F. explicit product -> accepted
    mock_llm_adapter.invoke.return_value = '{"product_name": {"value": "SuperPhone", "source": "USER_EXPLICIT"}, "order_id": null, "manufacturer": null}'
    state = await agent._extract_and_merge_state(ctx)
    assert state.product_name == "SuperPhone"

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

