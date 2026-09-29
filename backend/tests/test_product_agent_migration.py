import pytest
import json
from app.models import WorkflowState, ProductState, OrderState, OrchestrationDomain, DomainWorkflowStatus, ToolResultEnvelope
from app.agents.product_agent import ProductDomainContext
from tests.test_product_agent import make_product_agent

@pytest.mark.asyncio
class TestProductAgentF24Migration:

    async def test_active_domain_ownership(self):
        """ProductAgent MUST NOT independently change routing ownership."""
        agent, _, _, llm, _ = make_product_agent(llm_response="The phone features a 6.7 inch display.")
        
        # Supervisor sets ORDER domain but routes to ProductAgent
        state = WorkflowState(session_id="test")
        state.active_domain = OrchestrationDomain.ORDER
        state.order_state = OrderState()
        ctx = ProductDomainContext(
            customer_message="Tell me about this phone",
            semantic_intent="product_inquiry",
            workflow_state=state
        )
        
        result = await agent.handle(ctx)
        # Verify active_domain was not silently converted to PRODUCT
        assert result.workflow_state.active_domain == OrchestrationDomain.ORDER
        assert result.workflow_state.product_state is not None
        
    async def test_suspended_escalated_safety(self):
        """Verify ProductAgent does not overwrite orchestration-owned SUSPENDED or ESCALATED status."""
        agent, _, _, llm, _ = make_product_agent(llm_response="Here is the info.")
        
        state = WorkflowState(session_id="test")
        state.product_state = ProductState(domain_status=DomainWorkflowStatus.SUSPENDED)
        
        ctx = ProductDomainContext(
            customer_message="Tell me about this phone",
            semantic_intent="product_inquiry",
            workflow_state=state
        )
        
        result = await agent.handle(ctx)
        # Should remain suspended
        assert result.workflow_state.product_state.domain_status == DomainWorkflowStatus.SUSPENDED

        state.product_state.domain_status = DomainWorkflowStatus.ESCALATED
        result2 = await agent.handle(ctx)
        assert result2.workflow_state.product_state.domain_status == DomainWorkflowStatus.ESCALATED

    async def test_order_id_compatibility(self):
        """
        Verify order_id compatibility:
        - ProductState does not own order_id
        - OrderState remains the source of truth for order facts
        """
        agent, _, _, llm, _ = make_product_agent(
            executor_return="Phone specs",
            llm_response="Here is the info."
        )
        # Provide order_id via user message
        state = WorkflowState(session_id="test")
        state.active_domain = OrchestrationDomain.PRODUCT
        state.product_state = ProductState()
        ctx = ProductDomainContext(
            customer_message="My order is 999. Tell me about the phone features.",
            semantic_intent="product_inquiry",
            workflow_state=state
        )
        
        # We need LLM to extract order_id, then return final response
        llm.invoke.side_effect = [
            '{"order_id": {"value": "999", "source": "USER_EXPLICIT"}}',
            'Here is the info.'
        ]
        
        result = await agent.handle(ctx)
        
        # order_id should be in order_state
        assert result.workflow_state.order_state is not None
        assert result.workflow_state.order_state.order_id == "999"
        
        # product_state does not contain order_id
        assert not hasattr(result.workflow_state.product_state, "order_id")
        
        # legacy root field might be set or not depending on projection, but let's check it doesn't break
        # Since active_domain is PRODUCT, legacy projection clears root order_id
        proj = result.workflow_state.to_legacy_projection()
        assert proj.get("order_id") is None
        
    async def test_persistence_round_trip(self):
        """
        Serialize to JSON, reconstruct WorkflowState, verify semantic equality
        and type consistency for ToolResultEnvelope.
        """
        agent, _, _, llm, _ = make_product_agent(
            executor_return='{"status": "Specs found."}',
            llm_response='```json\n{"tool_call": "retrieve_product_info", "arguments": {"product_name": "Phone"}}\n```'
        )
        
        state = WorkflowState(session_id="test")
        state.active_domain = OrchestrationDomain.PRODUCT
        state.product_state = ProductState()
        ctx = ProductDomainContext(
            customer_message="Tell me about Phone",
            semantic_intent="product_inquiry",
            workflow_state=state
        )
        
        # We need the extraction LLM call to return product_name
        # Then the agent LLM call to return the tool call
        llm.invoke.side_effect = [
            '{"product_name": {"value": "Phone", "source": "USER_EXPLICIT"}}', # extraction
            '```json\n{"tool_call": "retrieve_product_info", "arguments": {"product_name": "Phone"}}\n```',
            'Tool executed successfully.' # final response
        ]
        
        result = await agent.handle(ctx)
        assert result.workflow_state.product_state.last_tool == "retrieve_product_info"
        assert isinstance(result.workflow_state.product_state.last_tool_result, ToolResultEnvelope)
        
        # Serialize
        json_data = result.workflow_state.model_dump_json()
        
        # Deserialize
        reconstructed = WorkflowState.model_validate_json(json_data)
        
        assert reconstructed.product_state.product_name == "Phone"
        assert reconstructed.product_state.last_tool == "retrieve_product_info"
        assert isinstance(reconstructed.product_state.last_tool_result, ToolResultEnvelope)
        assert reconstructed.product_state.last_tool_result.tool_name == "retrieve_product_info"
