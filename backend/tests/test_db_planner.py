import pytest
from unittest.mock import MagicMock, patch
from app.agents.db_agent import determine_db_action, DBPlannerOutput
from pydantic import ValidationError

@pytest.fixture
def mock_llm_chain():
    with patch("app.agents.db_agent.get_db_llm") as mock_get_llm:
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_get_llm.return_value = mock_llm
        yield mock_structured

def test_planner_valid_order_tracking(mock_llm_chain):
    mock_llm_chain.invoke.return_value = DBPlannerOutput(
        action="track_order",
        order_id=12345,
    )
    result = determine_db_action("Where is order 12345?", "order_tracking", None)
    assert result["action"] == "track_order"
    assert result["params"] == {"order_id": 12345}

def test_planner_missing_order_id_fallback(mock_llm_chain):
    mock_llm_chain.invoke.return_value = DBPlannerOutput(
        action="track_order",
        order_id=None,
    )
    result = determine_db_action("Where is my order?", "order_tracking", None)
    assert result["action"] == "missing_argument"
    assert result["params"] == {}

def test_planner_cancel_active_order(mock_llm_chain):
    mock_llm_chain.invoke.return_value = DBPlannerOutput(
        action="cancel_order",
        order_id=999,
    )
    result = determine_db_action("Cancel my order 999", "order_cancellation", None)
    assert result["action"] == "cancel_order"
    assert result["params"] == {"order_id": 999}

def test_planner_unsupported_action(mock_llm_chain):
    mock_llm_chain.invoke.return_value = DBPlannerOutput(
        action="unsupported_action",
    )
    result = determine_db_action("Delete my account", "account_management", None)
    assert result["action"] == "unsupported_action"
    assert result["params"] == {}

def test_planner_malformed_output(mock_llm_chain):
    mock_llm_chain.invoke.side_effect = Exception("LLM Timeout or Parse Error")
    result = determine_db_action("Where is order 123?", "order_tracking", None)
    assert result["action"] == "missing_argument"
    assert result["params"] == {}

def test_planner_refund_multi_turn_missing(mock_llm_chain):
    # Testing that it returns missing_argument when ID is not present
    mock_llm_chain.invoke.return_value = DBPlannerOutput(
        action="process_refund",
        order_id=None,
    )
    result = determine_db_action("Yes, that one.", "refund", None)
    assert result["action"] == "missing_argument"
    assert result["params"] == {}

def test_planner_check_inventory(mock_llm_chain):
    mock_llm_chain.invoke.return_value = DBPlannerOutput(
        action="check_inventory",
        product_name="Wireless Mouse",
    )
    result = determine_db_action("Do you have Wireless Mouse in stock?", "product_inquiry", None)
    assert result["action"] == "check_inventory"
    assert result["params"] == {"product_name": "Wireless Mouse"}
