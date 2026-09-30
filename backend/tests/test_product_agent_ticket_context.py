import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch
from app.models import (
    WorkflowState, ProductState, OrderState, OrchestrationDomain, 
    DomainWorkflowStatus, ToolResultEnvelope
)
from app.agents.product_agent import ProductAgent, ProductDomainContext, ProductSkillResolver
from app.tickets.lifecycle import TicketLifecycleResult
from datetime import datetime, timezone

@pytest.fixture
def mock_llm_adapter():
    adapter = MagicMock()
    # Mock extract_and_merge_state extraction
    adapter.invoke = AsyncMock(return_value='{"order_id": {"value": 999, "source": "USER_EXPLICIT"}, "product_name": {"value": "Phone", "source": "USER_EXPLICIT"}}')
    return adapter

@pytest.fixture
def product_agent(mock_llm_adapter):
    mock_skill_resolver = MagicMock(spec=ProductSkillResolver)
    mock_skill = MagicMock()
    mock_skill.name = "Test Skill"
    mock_skill.metadata.version = "1.0"
    mock_skill_resolver.resolve.return_value = mock_skill
    
    mock_skill_runtime = MagicMock()
    validation_res = MagicMock()
    validation_res.status.value = "success"
    validation_res.status.name = "SUCCESS"
    mock_skill_runtime.validate_inputs.return_value = validation_res
    
    exec_res = MagicMock()
    exec_res.status.value = "success"
    exec_res.status.name = "SUCCESS"
    exec_res.structured_output = {"tool_result": "Success!"}
    mock_skill_runtime.execute_tool.return_value = exec_res
    
    # We will override llm_adapter.invoke to return a final answer in the second call
    mock_llm_adapter.invoke.return_value = '{"order_id": {"value": 999, "source": "USER_EXPLICIT"}, "product_name": {"value": "Phone", "source": "USER_EXPLICIT"}}'
    
    return ProductAgent(
        skill_resolver=mock_skill_resolver,
        skill_runtime=mock_skill_runtime,
        llm_adapter=mock_llm_adapter
    )

@pytest.mark.asyncio
@patch("app.tickets.lifecycle.TicketLifecycleService.process_issue")
async def test_product_agent_constructs_ticket_context(mock_process, product_agent):
    mock_process.return_value = TicketLifecycleResult(
        action="CREATED",
        ticket_id=101,
        customer_id=1,
        order_id=999,
        issue_type="technical_support",
        status="open",
        matched_existing=False,
        reason="",
        timestamp=datetime.now(timezone.utc)
    )
    
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=50)
    ctx = ProductDomainContext(
        customer_message="My order is 999 and my phone screen is broken.",
        semantic_intent="technical_support",
        customer_id=1,
        workflow_state=state
    )
    
    res = await product_agent.handle(ctx)
    
    mock_process.assert_called_once()
    ticket_ctx = mock_process.call_args[0][0]
    
    # Assert constraints
    assert ticket_ctx.__class__.__name__ == "TicketContext"
    assert ticket_ctx.domain == OrchestrationDomain.PRODUCT
    assert ticket_ctx.active_ticket_id == 50
    assert ticket_ctx.order_id == 999
    assert ticket_ctx.product_name == "Phone"
    
    # Assert result propagates to state
    assert res.workflow_state.product_state.active_ticket_id == 101

@pytest.mark.asyncio
@patch("app.tickets.lifecycle.TicketLifecycleService.process_issue")
async def test_ticket_isolation(mock_process, product_agent):
    mock_process.return_value = TicketLifecycleResult(
        action="UPDATED",
        ticket_id=701,
        customer_id=1,
        order_id=None,
        issue_type="test",
        status="open",
        matched_existing=True,
        reason="",
        timestamp=datetime.now(timezone.utc)
    )
    
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=701)
    state.order_state = OrderState(active_ticket_id=702)
    
    ctx = ProductDomainContext(
        customer_message="Help",
        semantic_intent="test",
        customer_id=1,
        workflow_state=state
    )
    
    # Overriding extraction so it doesn't extract anything new
    async def invoke_no_extract(*args):
        if "data extraction" in args[0]:
            return "{}"
        return "Done"
    product_agent.llm_adapter.invoke.side_effect = invoke_no_extract
    
    res = await product_agent.handle(ctx)
    
    assert res.workflow_state.product_state.active_ticket_id == 701
    assert res.workflow_state.order_state.active_ticket_id == 702
    
    # Validate ticket context didn't mix up IDs
    ticket_ctx = mock_process.call_args[0][0]
    assert ticket_ctx.active_ticket_id == 701

@pytest.mark.asyncio
@patch("app.tickets.lifecycle.TicketLifecycleService.process_issue")
async def test_transient_order_context(mock_process, product_agent):
    mock_process.return_value = TicketLifecycleResult(
        action="CREATED",
        ticket_id=105,
        customer_id=1,
        order_id=999,
        issue_type="test",
        status="open",
        matched_existing=False,
        reason="",
        timestamp=datetime.now(timezone.utc)
    )
    
    state = WorkflowState(session_id="test")
    # Explicitly no OrderState exists at start
    
    ctx = ProductDomainContext(
        customer_message="My order is 999 and my phone screen is broken.",
        semantic_intent="technical_support",
        customer_id=1,
        workflow_state=state
    )
    
    res = await product_agent.handle(ctx)
    
    # Order ID reached context
    ticket_ctx = mock_process.call_args[0][0]
    assert ticket_ctx.order_id == 999
    
    # But OrderState was not mutated/created
    assert res.workflow_state.order_state is None
    # the root order_id should be mutated by extraction but only for compatibility
    # actually it shouldn't be written back to root if it is transient extraction unless to_legacy_projection does it
    # the test is simply that order_state is unchanged.

@pytest.mark.asyncio
@patch("app.tickets.lifecycle.TicketLifecycleService.process_issue")
async def test_ticket_registration_failure(mock_process, product_agent):
    mock_process.return_value = TicketLifecycleResult(
        action="FAILED",
        ticket_id=None,
        customer_id=1,
        order_id=None,
        issue_type=None,
        status=None,
        matched_existing=False,
        reason="DB Connection Error",
        timestamp=datetime.now(timezone.utc)
    )
    
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=10)
    
    ctx = ProductDomainContext(
        customer_message="Help",
        semantic_intent="technical_support",
        customer_id=1,
        workflow_state=state
    )
    
    # Override extraction
    async def invoke_no_extract(*args):
        if "data extraction" in args[0]:
            return "{}"
        return "Done"
    product_agent.llm_adapter.invoke.side_effect = invoke_no_extract
    
    res = await product_agent.handle(ctx)
    
    # Verification of failure handling
    assert res.execution_status == "ticket_registration_failed"
    assert res.workflow_state.product_state.domain_status == DomainWorkflowStatus.FAILED
    assert res.workflow_state.product_state.active_ticket_id == 10 # Preserved
    assert "internal issue registering your request" in res.response
    
@pytest.mark.asyncio
@patch("app.tickets.lifecycle.TicketLifecycleService.process_issue")
async def test_ignored_action_preserves_ticket(mock_process, product_agent):
    mock_process.return_value = TicketLifecycleResult(
        action="IGNORED",
        ticket_id=None,
        customer_id=1,
        order_id=None,
        issue_type="test",
        status="open",
        matched_existing=True,
        reason="",
        timestamp=datetime.now(timezone.utc)
    )
    
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=20)
    
    ctx = ProductDomainContext(
        customer_message="Help",
        semantic_intent="test",
        customer_id=1,
        workflow_state=state
    )
    
    async def invoke_no_extract(*args):
        if "data extraction" in args[0]:
            return "{}"
        return "Done"
    product_agent.llm_adapter.invoke.side_effect = invoke_no_extract
    
    res = await product_agent.handle(ctx)
    
    # Preserves active ticket
    assert res.workflow_state.product_state.active_ticket_id == 20

@pytest.mark.asyncio
@patch("app.tickets.lifecycle.TicketLifecycleService.process_issue")
async def test_invalid_urgency_fails_closed(mock_process, product_agent):
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=10)
    
    ctx = ProductDomainContext(
        customer_message="Help",
        semantic_intent="test",
        customer_id=1,
        workflow_state=state,
        urgency="INVALID_URGENCY"
    )
    
    res = await product_agent.handle(ctx)
    
    assert res.execution_status == "invalid_urgency"
    assert res.workflow_state.product_state.domain_status == DomainWorkflowStatus.FAILED
    assert res.workflow_state.product_state.active_ticket_id == 10
    mock_process.assert_not_called()

@pytest.mark.asyncio
@patch("app.tickets.lifecycle.TicketLifecycleService.process_issue")
async def test_invalid_sentiment_fails_closed(mock_process, product_agent):
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=10)
    
    ctx = ProductDomainContext(
        customer_message="Help",
        semantic_intent="test",
        customer_id=1,
        workflow_state=state,
        sentiment="SUPER_MAD"
    )
    
    res = await product_agent.handle(ctx)
    
    assert res.execution_status == "invalid_sentiment"
    assert res.workflow_state.product_state.domain_status == DomainWorkflowStatus.FAILED
    assert res.workflow_state.product_state.active_ticket_id == 10
    mock_process.assert_not_called()

@pytest.mark.asyncio
@patch("app.tickets.lifecycle.TicketLifecycleService.process_issue")
async def test_invalid_order_id_fails_closed(mock_process, product_agent):
    state = WorkflowState(session_id="test")
    state.product_state = ProductState(active_ticket_id=10)
    
    ctx = ProductDomainContext(
        customer_message="Help my order ABC",
        semantic_intent="test",
        customer_id=1,
        workflow_state=state
    )
    
    # Extract an invalid order_id explicitly
    product_agent.llm_adapter.invoke.return_value = '{"order_id": {"value": "ABC", "source": "USER_EXPLICIT"}}'
    
    res = await product_agent.handle(ctx)
    
    assert res.execution_status == "invalid_order_id"
    assert res.workflow_state.product_state.domain_status == DomainWorkflowStatus.FAILED
    assert res.workflow_state.product_state.active_ticket_id == 10
    assert res.workflow_state.order_state is None
    mock_process.assert_not_called()

