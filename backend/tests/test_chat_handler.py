import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from app.websocket.chat_handler import _mutate_session, _get_session
from app.persistence.session import SaveResult, SessionStore

@pytest.fixture
def mock_session_store():
    with patch("app.websocket.chat_handler.session_store", new_callable=AsyncMock) as mock:
        yield mock

@pytest.fixture
def mock_manager():
    with patch("app.websocket.chat_handler.manager", new_callable=AsyncMock) as mock:
        mock.agent_connections = {}
        yield mock

@pytest.mark.asyncio
async def test_get_session_customer_id_initialization_success(mock_session_store):
    # Setup session missing customer_id
    mock_session_store.get_session.return_value = {"session_id": "123", "_session_revision": 1}
    mock_session_store.save_session_conditional.return_value = SaveResult.SUCCESS
    
    session = await _get_session("123", 456)
    
    assert session["customer_id"] == 456
    mock_session_store.save_session_conditional.assert_called_once()
    args, kwargs = mock_session_store.save_session_conditional.call_args
    assert args[0] == "123"
    assert args[1]["customer_id"] == 456
    assert args[2] == 1 # expected revision

@pytest.mark.asyncio
async def test_get_session_customer_id_initialization_conflict(mock_session_store):
    # Simulate conflict on first try, success on second
    call_count = 0
    def mock_get_session(sid):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"session_id": "123", "_session_revision": 1}
        else:
            return {"session_id": "123", "_session_revision": 2, "other_data": "updated"}
            
    mock_session_store.get_session = AsyncMock(side_effect=mock_get_session)
    mock_session_store.save_session_conditional.side_effect = [SaveResult.CONFLICT, SaveResult.SUCCESS]
    
    session = await _get_session("123", 456)
    
    assert session["customer_id"] == 456
    assert session["other_data"] == "updated"
    assert mock_session_store.save_session_conditional.call_count == 2
    args, kwargs = mock_session_store.save_session_conditional.call_args_list[1]
    assert args[2] == 2 # expected revision on second try

@pytest.mark.asyncio
async def test_agent_disconnect_already_ai(mock_session_store, mock_manager):
    # Import websocket handler block
    # We will simulate the disconnect logic manually since it's hard to trigger directly without FastAPI test client
    # but the logic is isolated in chat_handler.py
    
    has_other_agents = False
    
    def _abandon(s):
        if s and s.get("mode") == "human" and not has_other_agents:
            s["mode"] = "ai"
            return True
        return False
        
    mock_session_store.get_session.return_value = {"mode": "ai", "_session_revision": 1}
    mock_session_store.save_session_conditional.return_value = SaveResult.SUCCESS
    
    session, was_changed = await _mutate_session("123", _abandon, allow_retry=True)
    
    assert not was_changed
    assert session["mode"] == "ai"
    # Mutation should not have called save_session_conditional
    mock_session_store.save_session_conditional.assert_not_called()

@pytest.mark.asyncio
async def test_agent_disconnect_human_to_ai(mock_session_store, mock_manager):
    has_other_agents = False
    
    def _abandon(s):
        if s and s.get("mode") == "human" and not has_other_agents:
            s["mode"] = "ai"
            return True
        return False
        
    mock_session_store.get_session.return_value = {"mode": "human", "_session_revision": 1}
    mock_session_store.save_session_conditional.return_value = SaveResult.SUCCESS
    
    session, was_changed = await _mutate_session("123", _abandon, allow_retry=True)
    
    assert was_changed
    assert session["mode"] == "ai"
    mock_session_store.save_session_conditional.assert_called_once()

@pytest.mark.asyncio
async def test_agent_disconnect_human_with_other_agents(mock_session_store, mock_manager):
    has_other_agents = True
    
    def _abandon(s):
        if s and s.get("mode") == "human" and not has_other_agents:
            s["mode"] = "ai"
            return True
        return False
        
    mock_session_store.get_session.return_value = {"mode": "human", "_session_revision": 1}
    mock_session_store.save_session_conditional.return_value = SaveResult.SUCCESS
    
    session, was_changed = await _mutate_session("123", _abandon, allow_retry=True)
    
    assert not was_changed
    assert session["mode"] == "human"
    mock_session_store.save_session_conditional.assert_not_called()
