import pytest
import json
import asyncio
from unittest.mock import AsyncMock, patch
from app.persistence.session import SessionStore, SaveResult

@pytest.fixture
def mock_redis():
    with patch("app.persistence.session.redis_client", new_callable=AsyncMock) as mock:
        yield mock

@pytest.fixture
def memory_store():
    # Force redis_client to None for in-memory testing
    with patch("app.persistence.session.redis_client", None):
        store = SessionStore()
        yield store

@pytest.mark.asyncio
async def test_cas_succeeds_when_expected_revision_matches(memory_store):
    session_id = "test-session"
    session_data = {"val": "A", "_session_revision": 1}
    await memory_store.save_session(session_id, session_data)
    
    loaded = await memory_store.get_session(session_id)
    loaded["val"] = "B"
    result = await memory_store.save_session_conditional(session_id, loaded, 1)
    
    assert result == SaveResult.SUCCESS
    final = await memory_store.get_session(session_id)
    assert final["val"] == "B"

@pytest.mark.asyncio
async def test_cas_increments_session_revision(memory_store):
    session_id = "test-session"
    session_data = {"val": "A", "_session_revision": 1}
    await memory_store.save_session(session_id, session_data)
    
    loaded = await memory_store.get_session(session_id)
    result = await memory_store.save_session_conditional(session_id, loaded, 1)
    
    assert result == SaveResult.SUCCESS
    final = await memory_store.get_session(session_id)
    assert final["_session_revision"] == 2

@pytest.mark.asyncio
async def test_cas_refreshes_ttl(mock_redis):
    store = SessionStore()
    session_id = "test-session"
    session_data = {"val": "A", "_session_revision": 1}
    
    mock_redis.eval.return_value = 0 # SUCCESS
    
    result = await store.save_session_conditional(session_id, session_data, 1)
    
    assert result == SaveResult.SUCCESS
    mock_redis.eval.assert_called_once()
    args = mock_redis.eval.call_args[1]
    # Check that 86400 is passed in args
    assert 86400 in args["args"]

@pytest.mark.asyncio
async def test_cas_rejects_stale_revision(memory_store):
    session_id = "test-session"
    session_data = {"val": "A", "_session_revision": 2}
    await memory_store.save_session(session_id, session_data)
    
    # Attempt to save with expected_revision=1
    loaded = await memory_store.get_session(session_id)
    loaded["val"] = "B"
    result = await memory_store.save_session_conditional(session_id, loaded, 1)
    
    assert result == SaveResult.CONFLICT
    
    # E. rejected stale write does NOT modify current session
    final = await memory_store.get_session(session_id)
    assert final["val"] == "A"
    assert final["_session_revision"] == 2

@pytest.mark.asyncio
async def test_missing_session_rejected(memory_store):
    result = await memory_store.save_session_conditional("missing", {"val": "A"}, 1)
    assert result == SaveResult.MISSING

@pytest.mark.asyncio
async def test_ordinary_save_remains_unconditional(memory_store):
    session_id = "test-session"
    await memory_store.save_session(session_id, {"val": "A", "_session_revision": 5})
    await memory_store.save_session(session_id, {"val": "B", "_session_revision": 1})
    
    final = await memory_store.get_session(session_id)
    assert final["val"] == "B"

@pytest.mark.asyncio
async def test_state_revision_remains_distinct(memory_store):
    session_id = "test-session"
    session_data = {
        "val": "A", 
        "_session_revision": 1,
        "workflow_state": {"state_revision": 100}
    }
    await memory_store.save_session(session_id, session_data)
    
    loaded = await memory_store.get_session(session_id)
    result = await memory_store.save_session_conditional(session_id, loaded, 1)
    
    final = await memory_store.get_session(session_id)
    # _session_revision incremented, but state_revision didn't
    assert final["_session_revision"] == 2
    assert final["workflow_state"]["state_revision"] == 100

@pytest.mark.asyncio
async def test_race_condition(memory_store):
    session_id = "test-session"
    session_data = {"workflow_state": {"val": "A"}, "conversation_history": [], "_session_revision": 1}
    await memory_store.save_session(session_id, session_data)
    
    # Writer A
    loaded_a = await memory_store.get_session(session_id)
    loaded_a["workflow_state"]["val"] = "B"
    
    # Writer B
    loaded_b = await memory_store.get_session(session_id)
    loaded_b["conversation_history"].append("msg")
    
    # A saves successfully
    res_a = await memory_store.save_session_conditional(session_id, loaded_a, 1)
    assert res_a == SaveResult.SUCCESS
    
    # B fails due to conflict
    res_b = await memory_store.save_session_conditional(session_id, loaded_b, 1)
    assert res_b == SaveResult.CONFLICT
    
    final = await memory_store.get_session(session_id)
    assert final["workflow_state"]["val"] == "B"
    assert len(final["conversation_history"]) == 0
    assert final["_session_revision"] == 2
