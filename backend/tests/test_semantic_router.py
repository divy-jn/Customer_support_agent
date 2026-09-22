import pytest
import asyncio
from unittest.mock import patch, MagicMock

from app.models import SemanticRouteResult, RouterFailureType
from app.agents.semantic_router import parse_and_validate_semantic_output, classify_semantic_intent
from app.agents.routing_policy import map_domain_to_route
from app.agents.graph import route_intent_node, AgentState


# ──────────────────────────────────────────────
#  Routing Policy Tests
# ──────────────────────────────────────────────
def test_map_domain_to_route():
    assert map_domain_to_route("general") == "GeneralAgent"
    assert map_domain_to_route("product") == "ProductAgent"
    assert map_domain_to_route("order") == "OrderAgent"
    assert map_domain_to_route("payment") == "PaymentAgent"
    assert map_domain_to_route("escalation") == "EscalationAgent"
    assert map_domain_to_route("unknown") == "GeneralAgent"
    assert map_domain_to_route("invalid_domain") == "GeneralAgent"


# ──────────────────────────────────────────────
#  Parser & Validation Tests
# ──────────────────────────────────────────────
def test_valid_json_parsing():
    valid_json = '{"domain": "order", "intent": "track_order", "sentiment": "neutral", "urgency": "medium", "confidence": 0.95, "is_continuation": false}'
    result = parse_and_validate_semantic_output(valid_json, 100)
    assert result.diagnostics.failure_type == RouterFailureType.SUCCESS
    assert result.domain == "order"
    assert result.confidence == 0.95

def test_markdown_fenced_json():
    fenced_json = '```json\n{"domain": "payment", "intent": "refund", "sentiment": "negative", "urgency": "high", "confidence": 0.8, "is_continuation": true}\n```'
    result = parse_and_validate_semantic_output(fenced_json, 150)
    assert result.diagnostics.failure_type == RouterFailureType.SUCCESS
    assert result.domain == "payment"

def test_malformed_json():
    malformed = '{"domain": "order", "intent": "track'
    result = parse_and_validate_semantic_output(malformed, 50)
    assert result.diagnostics.failure_type == RouterFailureType.PARSER_ERROR
    assert result.domain == "unknown"

def test_missing_required_field():
    missing = '{"domain": "order", "sentiment": "neutral", "urgency": "medium", "confidence": 0.9, "is_continuation": false}'
    result = parse_and_validate_semantic_output(missing, 60)
    assert result.diagnostics.failure_type == RouterFailureType.SCHEMA_VALIDATION_ERROR
    assert "intent" in result.diagnostics.error_message

def test_invalid_enum():
    invalid = '{"domain": "aliens", "intent": "abduction", "sentiment": "neutral", "urgency": "medium", "confidence": 0.9, "is_continuation": false}'
    result = parse_and_validate_semantic_output(invalid, 70)
    assert result.diagnostics.failure_type == RouterFailureType.SCHEMA_VALIDATION_ERROR
    assert "domain=aliens" in result.diagnostics.error_message

def test_unknown_domain_is_valid_enum():
    unknown = '{"domain": "unknown", "intent": "weird_stuff", "sentiment": "neutral", "urgency": "medium", "confidence": 0.1, "is_continuation": false}'
    result = parse_and_validate_semantic_output(unknown, 80)
    assert result.diagnostics.failure_type == RouterFailureType.SUCCESS
    assert result.domain == "unknown"


# ──────────────────────────────────────────────
#  LLM Exception Handling Tests
# ──────────────────────────────────────────────

AuthenticationError = type("AuthenticationError", (Exception,), {})

@pytest.mark.asyncio
@patch("app.agents.semantic_router.get_llm")
async def test_authentication_error(mock_get_llm):
    mock_llm = MagicMock()
    error = AuthenticationError("API key invalid")
    mock_llm.ainvoke.side_effect = error
    mock_get_llm.return_value = mock_llm
    
    result = await classify_semantic_intent("hello")
    assert result.diagnostics.failure_type == RouterFailureType.AUTHENTICATION_ERROR

class APITimeoutError(Exception):
    pass

@pytest.mark.asyncio
@patch("app.agents.semantic_router.get_llm")
async def test_timeout_error(mock_get_llm):
    mock_llm = MagicMock()
    error = APITimeoutError("Timeout")
    mock_llm.ainvoke.side_effect = error
    mock_get_llm.return_value = mock_llm
    
    result = await classify_semantic_intent("hello")
    assert result.diagnostics.failure_type == RouterFailureType.TIMEOUT_ERROR

@pytest.mark.asyncio
@patch("app.agents.semantic_router.get_llm")
async def test_rate_limit_error(mock_get_llm):
    mock_llm = MagicMock()
    RateLimitError = type("RateLimitError", (Exception,), {})
    mock_llm.ainvoke.side_effect = RateLimitError("Too Many Requests")
    mock_get_llm.return_value = mock_llm
    
    result = await classify_semantic_intent("hello")
    assert result.diagnostics.failure_type == RouterFailureType.RATE_LIMIT_ERROR

@pytest.mark.asyncio
@patch("app.agents.semantic_router.get_llm")
async def test_timeout_error(mock_get_llm):
    mock_llm = MagicMock()
    TimeoutError_Exception = type("TimeoutError", (Exception,), {})
    mock_llm.ainvoke.side_effect = TimeoutError_Exception("Timeout")
    mock_get_llm.return_value = mock_llm
    
    result = await classify_semantic_intent("hello")
    assert result.diagnostics.failure_type == RouterFailureType.TIMEOUT_ERROR

@pytest.mark.asyncio
@patch("app.agents.semantic_router.get_llm")
async def test_internal_error(mock_get_llm):
    mock_llm = MagicMock()
    error = ValueError("Something bad happened")
    mock_llm.ainvoke.side_effect = error
    mock_get_llm.return_value = mock_llm
    
    result = await classify_semantic_intent("hello")
    assert result.diagnostics.failure_type == RouterFailureType.INTERNAL_ERROR


# ──────────────────────────────────────────────
#  Shadow Mode Non-Interference Test (Graph Boundary)
# ──────────────────────────────────────────────
@pytest.mark.asyncio
@patch("app.agents.semantic_router.classify_semantic_intent")
@patch("app.agents.intent_router.classify_intent")
async def test_shadow_mode_does_not_break_legacy(mock_legacy, mock_semantic):
    # Mock legacy to succeed
    mock_legacy.return_value = {
        "intent": "track_order",
        "route_to": "db_agent"
    }
    
    # Mock semantic to CRASH completely
    mock_semantic.side_effect = Exception("TOTAL SHADOW FAILURE")
    
    state: AgentState = {
        "message": "Where is my order?",
        "conversation_history": [],
        "session_id": "test_123"
    }
    
    # Run the node
    result = await route_intent_node(state)
    
    # Await background tasks so they don't leak (and we test they swallowed the error safely)
    from app.agents.graph import _shadow_tasks
    if _shadow_tasks:
        await asyncio.gather(*_shadow_tasks)
    
    # Assert legacy path was still returned correctly despite the shadow crash
    assert result["intent"] == "track_order"
    assert result["route_to"] == "db_agent"
    
    # Verify both were called
    mock_legacy.assert_called_once()
    mock_semantic.assert_called_once()

