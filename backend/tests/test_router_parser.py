import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from app.agents.intent_router import parse_and_validate_router_output, classify_intent

def test_valid_json_canonical_route():
    # LLM proposed route_to="rag_agent", but canonical mapping should force db_agent
    raw = '{"intent": "order_tracking", "sentiment": "neutral", "urgency": "medium", "route_to": "rag_agent", "reasoning": "ok"}'
    res = parse_and_validate_router_output(raw)
    assert res["intent"] == "order_tracking"
    assert res["route_to"] == "db_agent"
    assert res["llm_proposed_route"] == "rag_agent"

def test_markdown_fenced_json():
    raw = '```json\n{"intent": "order_tracking", "sentiment": "neutral", "urgency": "medium", "route_to": "db_agent"}\n```'
    res = parse_and_validate_router_output(raw)
    assert res["intent"] == "order_tracking"
    assert res["route_to"] == "db_agent"

def test_malformed_json():
    raw = '{"intent": "order_tracking", '
    with pytest.raises(Exception):
        parse_and_validate_router_output(raw)

def test_missing_required_fields():
    raw = '{"intent": "order_tracking", "sentiment": "neutral"}'
    with pytest.raises(ValueError, match="Missing required fields"):
        parse_and_validate_router_output(raw)

def test_invalid_enum_value():
    raw = '{"intent": "invalid_intent", "sentiment": "neutral", "urgency": "medium", "route_to": "db_agent"}'
    with pytest.raises(ValueError, match="Invalid enum values"):
        parse_and_validate_router_output(raw)

def test_unexpected_route_from_llm():
    # LLM outputs unknown_agent, but intent is valid.
    # The current validation checks route_to in valid_routes before canonical mapping.
    raw = '{"intent": "order_tracking", "sentiment": "neutral", "urgency": "medium", "route_to": "unknown_agent"}'
    with pytest.raises(ValueError, match="Invalid enum values"):
        parse_and_validate_router_output(raw)

def test_canonical_escalation():
    # Intent = complaint, route_to = db_agent
    # Expected route_to = escalation
    raw = '{"intent": "complaint", "sentiment": "negative", "urgency": "medium", "route_to": "db_agent"}'
    res = parse_and_validate_router_output(raw)
    assert res["intent"] == "complaint"
    assert res["route_to"] == "escalation"

@pytest.mark.asyncio
async def test_sentiment_urgency_override():
    # Even if intent is order_tracking (db_agent), if sentiment=negative and urgency=high,
    # classify_intent overrides it to escalation.
    with patch("app.agents.intent_router.get_router_llm") as mock_llm:
        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.content = '{"intent": "order_tracking", "sentiment": "negative", "urgency": "high", "route_to": "db_agent"}'
        mock_instance.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm.return_value = mock_instance
        
        res = await classify_intent("I am so angry where is my order")
        
        assert res["intent"] == "order_tracking"
        assert res["route_to"] == "escalation"
        assert res["llm_proposed_route"] == "db_agent"

@pytest.mark.asyncio
async def test_router_transport_failure():
    # 401 Unauthorized -> ROUTER_TRANSPORT_FAILURE
    with patch("app.agents.intent_router.get_router_llm") as mock_llm:
        mock_instance = MagicMock()
        mock_instance.ainvoke = AsyncMock(side_effect=Exception("Error code: 401 - Unauthorized", "OpenAIAuthenticationError"))
        # We need to ensure the exception type __name__ matches OpenAIAuthenticationError
        # or we just throw a generic Exception with that class name.
        class OpenAIAuthenticationError(Exception): pass
        mock_instance.ainvoke.side_effect = OpenAIAuthenticationError("401 Unauthorized")
        mock_llm.return_value = mock_instance
        
        res = await classify_intent("hello")
        
        assert res["intent"] == "general"
        assert res["route_to"] == "rag_agent"
        assert res["router_transport_failure"] is True
        assert res["router_parse_failure"] is False
        assert res["router_internal_failure"] is False

@pytest.mark.asyncio
async def test_router_parse_failure():
    # JSONDecodeError -> ROUTER_PARSE_FAILURE
    with patch("app.agents.intent_router.get_router_llm") as mock_llm:
        mock_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "not json at all"
        mock_instance.ainvoke = AsyncMock(return_value=mock_response)
        mock_llm.return_value = mock_instance
        
        res = await classify_intent("hello")
        
        assert res["intent"] == "general"
        assert res["route_to"] == "rag_agent"
        assert res["router_parse_failure"] is True
        assert res["router_transport_failure"] is False
        assert res["router_internal_failure"] is False

@pytest.mark.asyncio
async def test_router_internal_failure():
    # Unexpected exception -> ROUTER_INTERNAL_FAILURE
    with patch("app.agents.intent_router.get_router_llm") as mock_llm:
        mock_instance = MagicMock()
        class UnknownBizarreError(Exception): pass
        mock_instance.ainvoke = AsyncMock(side_effect=UnknownBizarreError("Disk full!"))
        mock_llm.return_value = mock_instance
        
        res = await classify_intent("hello")
        
        assert res["intent"] == "general"
        assert res["route_to"] == "rag_agent"
        assert res["router_internal_failure"] is True
        assert res["router_transport_failure"] is False
        assert res["router_parse_failure"] is False

@pytest.mark.asyncio
async def test_graph_state_preserves_metadata():
    # Ensure route_intent_node preserves diagnostic metadata
    from app.agents.graph import route_intent_node
    
    with patch("app.agents.intent_router.classify_intent") as mock_classify:
        mock_classify.return_value = {
            "intent": "general",
            "sentiment": "neutral",
            "urgency": "medium",
            "route_to": "rag_agent",
            "router_error_type": "Timeout",
            "router_transport_failure": True,
            "router_parse_failure": False,
            "router_internal_failure": False
        }
        
        state = {"message": "hello", "conversation_history": []}
        res = await route_intent_node(state)
        
        assert res["intent"] == "general"
        assert res["route_to"] == "rag_agent"
        assert res["router_transport_failure"] is True
        assert res["router_parse_failure"] is False
        assert res["router_internal_failure"] is False
        assert res["router_error_type"] == "Timeout"
