"""
Phase C — Product Domain Agent V2 Integration Tests.

Verifies:
- Product intent routing into the V2 Product Agent via supervisor
- Inventory tool invocation using real schema ('product_name')
- Knowledge-base retrieval and grounded response behavior
- Missing and ambiguous product information requiring safe clarification
- Warranty checks backed by authoritative database tools with customer scoping
- Tool exceptions and timeouts honestly distinguishing 'not found' from 'could not verify'
- Typed workflow state and Guardian integration
"""

import json
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone
import pytest

from app.agents.domain_graph import (
    domain_support_graph,
    route_after_supervisor,
    semantic_supervisor_node,
    v2_product_node,
)
from app.agents.product_agent import (
    handle_product_request,
    ProductAgent,
    ProductDomainContext,
    get_default_product_resolver,
)
from app.config import settings
from app.models import (
    DomainWorkflowStatus,
    OrchestrationDomain,
    RouterDiagnostics,
    RouterFailureType,
    SemanticRouteResult,
    WorkflowState,
    ProductState,
    WarrantyStatus,
    WarrantyStatusResult,
    SkillExecutionStatus,
)
from app.skills.runtime import SkillRuntime


def _make_semantic_result(domain: str, intent: str) -> SemanticRouteResult:
    return SemanticRouteResult(
        domain=domain,
        intent=intent,
        sentiment="neutral",
        urgency="medium",
        confidence=0.95,
        diagnostics=RouterDiagnostics(failure_type=RouterFailureType.SUCCESS),
    )


class MockLLMAdapter:
    """Deterministic mock LLM adapter for testing ProductAgent ReAct loops."""

    def __init__(self, responses: list[str] | None = None):
        self.responses = list(responses) if responses else []
        self.call_history: list[tuple[str, str]] = []

    async def invoke(self, system_prompt: str, user_prompt: str) -> str:
        self.call_history.append((system_prompt, user_prompt))
        if "strict data extraction" in system_prompt:
            # Check if user message explicitly specifies product or order
            product = None
            order = None
            msg_lower = user_prompt.lower()
            if "pixelpro" in msg_lower:
                product = "PixelPro"
            elif "headphones" in msg_lower:
                product = "Headphones"
            if "101" in msg_lower:
                order = 101
            return json.dumps({
                "product_name": {"value": product, "source": "USER_EXPLICIT"} if product else None,
                "order_id": {"value": order, "source": "USER_EXPLICIT"} if order else None,
                "manufacturer": None
            })

        if self.responses:
            return self.responses.pop(0)
        return "I have answered your product inquiry based on verified information."


# ──────────────────────────────────────────────
#  1. Routing & Supervisor Integration
# ──────────────────────────────────────────────

@pytest.mark.asyncio
@patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
async def test_product_intent_routes_via_supervisor_to_v2_product(mock_classify):
    """Product request must reach Product Agent through V2 semantic supervisor."""
    mock_classify.return_value = _make_semantic_result("product", "product_availability")
    state = {
        "message": "Is the PixelPro in stock?",
        "conversation_history": [],
        "session_id": "session-prod-1",
        "customer_id": 12,
        "workflow_state": {"session_id": "session-prod-1", "customer_id": 12},
    }

    step = await semantic_supervisor_node(state)

    assert step["semantic_domain"] == "product"
    assert step["supervisor_action"] == "start_new"
    assert step["workflow_state"]["active_domain"] == "product"
    assert route_after_supervisor({**state, **step}) == "product"


# ──────────────────────────────────────────────
#  2. Inventory Tool Invocation & Schema
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_inventory_tool_invocation_with_real_schema():
    """Verify inventory tool is invoked with real schema: product_name."""
    tool_calls = []

    def mock_executor(tool_name: str, kwargs: dict):
        tool_calls.append((tool_name, kwargs))
        if tool_name == "check_inventory":
            return json.dumps([
                {
                    "id": 1,
                    "name": "PixelPro Phone",
                    "price": 899.99,
                    "stock_quantity": 15,
                    "category": "Electronics"
                }
            ])
        return "unknown tool"

    llm = MockLLMAdapter([
        '```json\n{"tool_call": "check_inventory", "arguments": {"product_name": "PixelPro"}}\n```',
        "The PixelPro is in stock with 15 units available at $899.99."
    ])

    state = {
        "message": "Is the PixelPro in stock?",
        "intent": "product_availability",
        "session_id": "sess-inv",
        "customer_id": 10,
        "workflow_state": {"session_id": "sess-inv", "customer_id": 10},
    }

    result = await handle_product_request(state, tool_executor=mock_executor, llm_adapter=llm)

    assert len(tool_calls) == 1
    assert tool_calls[0][0] == "check_inventory"
    assert tool_calls[0][1]["product_name"] == "PixelPro"
    assert "15 units available" in result["response"]
    assert result["workflow_state"]["active_domain"] == "product"
    pstate = result["workflow_state"]["product_state"]
    assert pstate["last_tool"] == "check_inventory"
    assert pstate["domain_status"] == DomainWorkflowStatus.COMPLETED.value


# ──────────────────────────────────────────────
#  3. Missing & Ambiguous Product Clarification
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_product_identifier_triggers_safe_clarification():
    """When stock inquiry lacks product identifier, request clarification safely without tool execution."""
    tool_calls = []

    def mock_executor(tool_name: str, kwargs: dict):
        tool_calls.append((tool_name, kwargs))
        return "should not be called"

    llm = MockLLMAdapter([])

    state = {
        "message": "Do you have any in stock?",
        "intent": "product_availability",
        "session_id": "sess-missing-prod",
        "customer_id": 10,
        "workflow_state": {"session_id": "sess-missing-prod", "customer_id": 10},
    }

    result = await handle_product_request(state, tool_executor=mock_executor, llm_adapter=llm)

    # Tool must NOT be called with missing product
    assert len(tool_calls) == 0
    assert "Could you please specify which product" in result["response"]
    pstate = result["workflow_state"]["product_state"]
    assert pstate["domain_status"] == DomainWorkflowStatus.AWAITING_INPUT.value


@pytest.mark.asyncio
async def test_ambiguous_inventory_match_requests_clarification():
    """When inventory check returns multiple matches, agent asks customer to clarify which one."""
    tool_calls = []

    def mock_executor(tool_name: str, kwargs: dict):
        tool_calls.append((tool_name, kwargs))
        return json.dumps([
            {"id": 1, "name": "PixelPro 128GB", "stock_quantity": 5},
            {"id": 2, "name": "PixelPro 256GB", "stock_quantity": 12},
        ])

    llm = MockLLMAdapter([
        '```json\n{"tool_call": "check_inventory", "arguments": {"product_name": "PixelPro"}}\n```',
        "We have both the PixelPro 128GB and PixelPro 256GB in stock. Which model would you like?"
    ])

    state = {
        "message": "Is the PixelPro in stock?",
        "intent": "product_availability",
        "session_id": "sess-ambig-inv",
        "customer_id": 10,
        "workflow_state": {"session_id": "sess-ambig-inv", "customer_id": 10},
    }

    result = await handle_product_request(state, tool_executor=mock_executor, llm_adapter=llm)

    assert "Which model would you like?" in result["response"]
    pstate = result["workflow_state"]["product_state"]
    # Final response asked a clarification question -> domain_status must be AWAITING_INPUT
    assert pstate["domain_status"] == DomainWorkflowStatus.AWAITING_INPUT.value


# ──────────────────────────────────────────────
#  4. Knowledge Base Retrieval & Grounding
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_knowledge_base_retrieval_and_grounded_response():
    """Knowledge base search is used for product facts and response is grounded in retrieved text."""
    tool_calls = []

    def mock_executor(tool_name: str, kwargs: dict):
        tool_calls.append((tool_name, kwargs))
        if tool_name in ("retrieve_as_context", "search_knowledge_base"):
            return (
                "--- Source: pixelpro_specs.md (Relevance: 0.92) ---\n"
                "The PixelPro phone features a 5000mAh battery providing up to 48 hours of battery life."
            )
        return ""

    llm = MockLLMAdapter([
        '```json\n{"tool_call": "retrieve_as_context", "arguments": {"query": "PixelPro battery life"}}\n```',
        "According to pixelpro_specs.md, the PixelPro features a 5000mAh battery that provides up to 48 hours of battery life."
    ])

    state = {
        "message": "What is the battery life of the PixelPro?",
        "intent": "product_specs",
        "session_id": "sess-rag",
        "customer_id": 10,
        "workflow_state": {"session_id": "sess-rag", "customer_id": 10},
    }

    result = await handle_product_request(state, tool_executor=mock_executor, llm_adapter=llm)

    assert len(tool_calls) == 1
    assert tool_calls[0][0] == "retrieve_as_context"
    assert "48 hours" in result["response"]
    assert "pixelpro_specs.md" in result["response"]
    pstate = result["workflow_state"]["product_state"]
    assert pstate["domain_status"] == DomainWorkflowStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_knowledge_base_empty_retrieval_honest_not_found():
    """Empty retrieval result produces honest 'not found' answer without fabricating specifications."""
    def mock_executor(tool_name: str, kwargs: dict):
        return "No relevant information found in the knowledge base."

    llm = MockLLMAdapter([
        '```json\n{"tool_call": "retrieve_as_context", "arguments": {"query": "PixelPro underwater camera"}}\n```',
        "I apologize, but I couldn't find any information about an underwater camera for the PixelPro in our knowledge base."
    ])

    state = {
        "message": "Does the PixelPro work underwater?",
        "intent": "product_specs",
        "session_id": "sess-empty-rag",
        "customer_id": 10,
        "workflow_state": {"session_id": "sess-empty-rag", "customer_id": 10},
    }

    result = await handle_product_request(state, tool_executor=mock_executor, llm_adapter=llm)

    assert "couldn't find any information" in result["response"]
    assert "underwater" in result["response"]


# ──────────────────────────────────────────────
#  5. Authoritative Warranty Integration
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_warranty_check_with_customer_scoping_and_active_status():
    """Warranty check enforces authenticated customer_id and returns accurate coverage status."""
    tool_calls = []

    def mock_executor(tool_name: str, kwargs: dict):
        tool_calls.append((tool_name, kwargs))
        if tool_name == "check_warranty_status":
            return WarrantyStatusResult(
                status=WarrantyStatus.ACTIVE,
                eligible_purchase=True,
                customer_id=kwargs.get("customer_id", 0),
                order_id=101,
                product_name="PixelPro Phone",
                purchase_date=datetime(2026, 1, 15, tzinfo=timezone.utc),
                warranty_period="1 year",
                warranty_expiry=datetime(2027, 1, 15, tzinfo=timezone.utc),
                reason="Warranty expires on 2027-01-15",
            )
        return ""

    llm = MockLLMAdapter([
        '```json\n{"tool_call": "check_warranty_status", "arguments": {"order_id": 101}}\n```',
        "Your PixelPro from order #101 has an active 1-year warranty valid through January 15, 2027."
    ])

    state = {
        "message": "What is the warranty for order 101?",
        "intent": "product_warranty",
        "session_id": "sess-warranty",
        "customer_id": 42,
        "workflow_state": {"session_id": "sess-warranty", "customer_id": 42},
    }

    result = await handle_product_request(state, tool_executor=mock_executor, llm_adapter=llm)

    assert len(tool_calls) == 1
    assert tool_calls[0][0] == "check_warranty_status"
    # Ensure customer_id was injected from authenticated state
    assert tool_calls[0][1]["customer_id"] == 42
    assert "January 15, 2027" in result["response"]
    pstate = result["workflow_state"]["product_state"]
    assert pstate["domain_status"] == DomainWorkflowStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_warranty_missing_identifier_requests_clarification():
    """Warranty request without order_id or product_name asks for clarification safely."""
    tool_calls = []

    def mock_executor(tool_name: str, kwargs: dict):
        tool_calls.append((tool_name, kwargs))
        return ""

    llm = MockLLMAdapter([])

    state = {
        "message": "Can you check my warranty?",
        "intent": "product_warranty",
        "session_id": "sess-no-id-warranty",
        "customer_id": 42,
        "workflow_state": {"session_id": "sess-no-id-warranty", "customer_id": 42},
    }

    result = await handle_product_request(state, tool_executor=mock_executor, llm_adapter=llm)

    assert len(tool_calls) == 0
    assert "order number or the product name" in result["response"]
    pstate = result["workflow_state"]["product_state"]
    assert pstate["domain_status"] == DomainWorkflowStatus.AWAITING_INPUT.value


@pytest.mark.asyncio
async def test_warranty_ambiguous_result_asks_for_order_id():
    """When multiple purchases match, warranty tool returns AMBIGUOUS and agent asks for order ID."""
    tool_calls = []

    def mock_executor(tool_name: str, kwargs: dict):
        tool_calls.append((tool_name, kwargs))
        return WarrantyStatusResult(
            status=WarrantyStatus.AMBIGUOUS,
            reason="Multiple purchases match the product name. Please provide an order_id."
        )

    llm = MockLLMAdapter([
        '```json\n{"tool_call": "check_warranty_status", "arguments": {"product_name": "Headphones"}}\n```',
        "You have multiple headphone purchases on file. Could you please share your order number so I can check the right one?"
    ])

    state = {
        "message": "What is the warranty on my headphones?",
        "intent": "product_warranty",
        "session_id": "sess-ambig-warranty",
        "customer_id": 42,
        "workflow_state": {"session_id": "sess-ambig-warranty", "customer_id": 42},
    }

    result = await handle_product_request(state, tool_executor=mock_executor, llm_adapter=llm)

    assert "order number" in result["response"]
    pstate = result["workflow_state"]["product_state"]
    assert pstate["domain_status"] == DomainWorkflowStatus.AWAITING_INPUT.value


# ──────────────────────────────────────────────
#  6. Tool Exceptions & Honest Error Responses
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_exception_distinguishes_could_not_verify_from_not_found():
    """Tool failure/timeout produces honest 'could not verify' response rather than 'not found'."""
    def mock_executor(tool_name: str, kwargs: dict):
        raise TimeoutError("Database connection timed out after 5000ms")

    llm = MockLLMAdapter([
        '```json\n{"tool_call": "check_inventory", "arguments": {"product_name": "PixelPro"}}\n```',
        "I apologize, but I could not verify the inventory status right now due to a system timeout. Please try again shortly."
    ])

    state = {
        "message": "Is the PixelPro in stock?",
        "intent": "product_availability",
        "session_id": "sess-timeout",
        "customer_id": 10,
        "workflow_state": {"session_id": "sess-timeout", "customer_id": 10},
    }

    result = await handle_product_request(state, tool_executor=mock_executor, llm_adapter=llm)

    assert "could not verify" in result["response"]


# ──────────────────────────────────────────────
#  7. End-to-End V2 Graph Traversal
# ──────────────────────────────────────────────

@pytest.mark.asyncio
@patch("app.agents.domain_graph.find_customer_support_cases")
@patch("app.agents.domain_graph.semantic_router.classify_semantic_intent")
@patch("app.agents.domain_graph.product_node", new_callable=AsyncMock)
async def test_v2_graph_traversal_product_intent(mock_prod_node, mock_classify, mock_cases):
    """End-to-end traversal from supervisor through v2_product to guardian_response."""
    mock_cases.return_value = '{"cases": []}'
    mock_classify.return_value = _make_semantic_result("product", "product_availability")
    mock_prod_node.return_value = {
        "response": "The PixelPro is in stock.",
        "workflow_state": {
            "session_id": "e2e-session",
            "active_domain": "product",
            "turn_count": 1,
            "product_state": {"domain_status": "completed", "last_tool": "check_inventory"}
        },
        "tool_results": [{"name": "PixelPro", "stock_quantity": 10}],
        "escalated": False,
    }

    initial_state = {
        "message": "Is the PixelPro in stock?",
        "conversation_history": [],
        "session_id": "e2e-session",
        "customer_id": 15,
        "architecture_mode": "v2_all",
        "workflow_state": {"session_id": "e2e-session", "customer_id": 15},
    }

    final_state = await domain_support_graph.ainvoke(initial_state)

    assert final_state["semantic_domain"] == "product"
    assert "The PixelPro is in stock." in final_state["response"]
    assert final_state["workflow_state"]["active_domain"] == "product"
    assert final_state["guardian_action"] == "resolve"
