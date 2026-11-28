"""Live classifier and dispatch contract with synthetic, mocked customer data."""

import json
import os
from unittest.mock import Mock

import pytest

pytestmark = [
    pytest.mark.llm_eval(probe=False),
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_INCIDENT_ROUTING") != "1",
        reason="Set RUN_LIVE_INCIDENT_ROUTING=1 to make live model requests.",
    ),
]


@pytest.mark.asyncio
async def test_incident_prompts_dispatch_to_the_expected_customer_tool(monkeypatch):
    from app.agents import domain_graph, order_agent
    from app.config import settings

    monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
    load_orders = Mock(return_value=json.dumps({"recent_orders": [{"id": 42, "status": "shipped"}]}))
    load_cases = Mock(return_value=json.dumps({
        "recent_count": 0,
        "cases": [{"id": 73, "subject": "Delivery inquiry", "status": "open"}],
    }))
    monkeypatch.setattr(order_agent, "get_customer_history", load_orders)
    monkeypatch.setattr(domain_graph, "find_customer_support_cases", load_cases)

    def unexpected_call(*_args, **_kwargs):
        pytest.fail("Unexpected ticket mutation or legacy fallback")

    monkeypatch.setattr(domain_graph, "create_ticket", unexpected_call)
    monkeypatch.setattr(domain_graph, "update_ticket", unexpected_call)
    monkeypatch.setattr(domain_graph.customer_support_graph, "ainvoke", unexpected_call)

    workflow = None
    history = []
    queries = [
        ("Check my ticket status", "v2_ticket_status", "#73"),
        ("is there any active ticket for my account", "v2_ticket_status", "#73"),
        ("give me my order history", "v2_order", "#42"),
        ("my order history", "v2_order", "#42"),
    ]
    for message, expected_node, expected_id in queries:
        load_orders.reset_mock()
        load_cases.reset_mock()
        history.append({"role": "customer", "content": message})
        updates = {}
        async for step in domain_graph.domain_support_graph.astream({
            "message": message, "customer_id": 7, "session_id": "synthetic-incident-routing",
            "workflow_state": workflow, "conversation_history": history,
        }, stream_mode="updates"):
            updates.update(step)

        assert updates["semantic_supervisor"]["semantic_failure_type"] == "success"
        assert expected_node in updates
        assert "safe_clarification" not in updates
        assert "v2_general" not in updates
        load_cases.assert_called_once()
        assert load_cases.call_args.args == (7,)
        if expected_node == "v2_order":
            load_orders.assert_called_once_with(7)
        else:
            load_orders.assert_not_called()
            assert updates[expected_node]["ticket_snapshot"][0][0]["id"] == 73

        answer = updates["guardian_response"]
        assert expected_id in answer["response"]
        assert not answer["escalated"]
        workflow = answer["workflow_state"]
        history.append({"role": "agent", "content": answer["response"]})
