import pytest
import asyncio
import uuid
import json
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

from fastapi import WebSocketDisconnect
from app.models import EscalationLifecycleStatus
from app.persistence.escalation import ensure_durable_escalation_state, get_durable_escalation_state
from app.escalation.service import EscalationService, EscalationRequest
from app.tools import supabase

# A dummy websocket mock for testing chat_handler logic
class MockWebSocket:
    def __init__(self, messages=None):
        self.messages = messages or []
        self.sent_messages = []
        self.accepted = False
        self.closed = False
        self.close_code = None
        
    async def accept(self):
        self.accepted = True
        
    async def receive_text(self):
        if not self.messages:
            raise RuntimeError("WebSocket disconnected")
        return self.messages.pop(0)
        
    async def send_text(self, data):
        self.sent_messages.append(data)
        
    async def close(self, code=1000, reason=None):
        self.closed = True
        self.close_code = code

import os
import psycopg2
TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD", "postgres")
conn_string = f"dbname='postgres' user='postgres' host='localhost' port='5433' password='{TEST_DB_PASSWORD}'"

@pytest.fixture(autouse=True)
def clean_db():
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("NOTIFY pgrst, 'reload schema';")
    cur.execute("DELETE FROM escalation_events;")
    cur.execute("DELETE FROM session_escalation_state;")
    cur.close()
    conn.close()
    
    from app.persistence.session import session_store
    session_store.clear()
    yield

@pytest.fixture(autouse=True)
def mock_persistence(monkeypatch):
    def mock_ensure(session_id, customer_id):
        conn = psycopg2.connect(conn_string)
        conn.autocommit = True
        try:
            cur = conn.cursor()
            cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'NONE') ON CONFLICT DO NOTHING", (session_id, customer_id))
            cur.execute("SELECT status FROM session_escalation_state WHERE session_id = %s", (session_id,))
            row = cur.fetchone()
            return EscalationLifecycleStatus(row[0])
        finally:
            conn.close()

    def mock_get(session_id, customer_id):
        conn = psycopg2.connect(conn_string)
        conn.autocommit = True
        try:
            cur = conn.cursor()
            cur.execute("SELECT status FROM session_escalation_state WHERE session_id = %s", (session_id,))
            row = cur.fetchone()
            if row: return EscalationLifecycleStatus(row[0])
            return None
        finally:
            conn.close()
            
    def mock_escalate(request):
        conn = psycopg2.connect(conn_string)
        conn.autocommit = True
        try:
            cur = conn.cursor()
            cur.execute("UPDATE session_escalation_state SET status = 'ACTIVE' WHERE session_id = %s", (request.session_id,))
            event_id = uuid.uuid4()
            cur.execute("INSERT INTO escalation_events (escalation_event_id, session_id, customer_id, client_request_id, payload_hash) VALUES (%s, %s, %s, %s, %s)", 
                        (str(event_id), request.session_id, request.customer_id, str(request.client_request_id), 'hash'))
        finally:
            conn.close()
        from app.escalation.service import EscalationResult
        return EscalationResult(status="success", message="Escalated", session_id=request.session_id, escalation_event_id=event_id)

    monkeypatch.setattr("app.persistence.escalation.ensure_durable_escalation_state", mock_ensure)
    monkeypatch.setattr("app.persistence.escalation.get_durable_escalation_state", mock_get)
    monkeypatch.setattr("app.escalation.service.EscalationService.escalate_session", mock_escalate)
    
@pytest.mark.asyncio
async def test_durable_state_routing_postgres_active_repairs_redis():
    """Postgres ACTIVE + Redis ai -> repair -> routes human"""
    session_id = f"sess_{uuid.uuid4()}"
    customer_id = 123
    
    # Setup Postgres ACTIVE
    from tests.test_escalation_integration_slice_353 import mock_persistence # ensuring fixture
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    conn.cursor().execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", (session_id, customer_id))
    conn.close()
    
    # Setup Redis as AI
    from app.persistence.session import session_store
    await session_store.create_session_if_absent(session_id, {
        "session_id": session_id,
        "customer_id": customer_id,
        "_session_revision": 1,
        "mode": "ai",
        "conversation_history": []
    })
    
    from app.websocket.chat_handler import handle_customer_ws
    ws = MockWebSocket([json.dumps({"message": "Hello", "client_request_id": str(uuid.uuid4())})])
    
    await handle_customer_ws(ws, session_id, customer_id)
    
    # Verify Redis is repaired to human
    session = await session_store.get_session(session_id)
    assert session["mode"] == "human"
    
    assert len([m for m in ws.sent_messages if '"type": "agent_response"' in m]) == 0

@pytest.mark.asyncio
async def test_durable_state_routing_postgres_released_repairs_redis():
    """Postgres RELEASED + Redis human -> repair -> routes AI"""
    session_id = f"sess_{uuid.uuid4()}"
    customer_id = 123
    
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    conn.cursor().execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'RELEASED')", (session_id, customer_id))
    conn.close()
    
    from app.persistence.session import session_store
    await session_store.create_session_if_absent(session_id, {
        "session_id": session_id,
        "customer_id": customer_id,
        "_session_revision": 1,
        "mode": "human",
        "conversation_history": []
    })
    
    from app.websocket.chat_handler import handle_customer_ws
    ws = MockWebSocket([json.dumps({"message": "Hello", "client_request_id": str(uuid.uuid4())})])
    
    with patch("app.agents.graph.customer_support_graph.ainvoke", new_callable=AsyncMock) as mock_graph:
        mock_graph.return_value = {"response": "I am AI", "escalated": False}
        await handle_customer_ws(ws, session_id, customer_id)
    
    session = await session_store.get_session(session_id)
    assert session["mode"] == "ai"
    mock_graph.assert_called_once()

@pytest.mark.asyncio
async def test_escalation_integration_creates_event():
    """NONE -> escalation service -> E1 -> Redis mode human"""
    session_id = f"sess_{uuid.uuid4()}"
    customer_id = 123
    
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("INSERT INTO customers (id, name, email) VALUES (%s, 'Test', %s) ON CONFLICT (id) DO NOTHING", (customer_id, f"test_{uuid.uuid4()}@example.com"))
    conn.close()

    from app.websocket.chat_handler import handle_customer_ws
    ws = MockWebSocket([json.dumps({"message": "I want a human", "client_request_id": str(uuid.uuid4())})])
    
    with patch("app.agents.graph.customer_support_graph.ainvoke", new_callable=AsyncMock) as mock_graph:
        mock_graph.return_value = {"response": "Escalating!", "escalated": True}
        await handle_customer_ws(ws, session_id, customer_id)
        
    # Verify Postgres state is ACTIVE
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT status FROM session_escalation_state WHERE session_id = %s", (session_id,))
    assert cur.fetchone()[0] == 'ACTIVE'
    
    # Verify E1 created
    cur.execute("SELECT COUNT(*) FROM escalation_events WHERE session_id = %s", (session_id,))
    assert cur.fetchone()[0] == 1
    conn.close()
    
    from app.persistence.session import session_store
    session = await session_store.get_session(session_id)
    assert session["mode"] == "human"

@pytest.mark.asyncio
async def test_durable_lookup_failure_fails_closed():
    session_id = f"sess_{uuid.uuid4()}"
    customer_id = 123
    
    from app.persistence.session import session_store
    await session_store.create_session_if_absent(session_id, {
        "session_id": session_id,
        "customer_id": customer_id,
        "_session_revision": 1,
        "mode": "ai",
        "conversation_history": []
    })
    
    from app.websocket.chat_handler import handle_customer_ws
    ws = MockWebSocket([json.dumps({"message": "Hello", "client_request_id": str(uuid.uuid4())})])
    
    with patch("app.persistence.escalation.ensure_durable_escalation_state", side_effect=RuntimeError("DB dead")):
        await handle_customer_ws(ws, session_id, customer_id)
        
    assert any("experiencing a technical issue verifying session state" in m for m in ws.sent_messages)

@pytest.mark.asyncio
async def test_customer_authorization_isolated():
    session_id = f"sess_{uuid.uuid4()}"
    
    from app.websocket.chat_handler import handle_customer_ws
    # Init with customer A
    ws_a = MockWebSocket([json.dumps({"message": "Hi"})])
    await handle_customer_ws(ws_a, session_id, 111)
    
    # Attempt to use same session with customer B
    ws_b = MockWebSocket([json.dumps({"message": "Hi"})])
    await handle_customer_ws(ws_b, session_id, 222)
    
    assert ws_b.close_code == 1008

@pytest.mark.asyncio
async def test_missing_client_request_id_fails_closed():
    session_id = f"sess_{uuid.uuid4()}"
    customer_id = 123
    
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("INSERT INTO customers (id, name, email) VALUES (%s, 'Test', %s) ON CONFLICT (id) DO NOTHING", (customer_id, f"test_{uuid.uuid4()}@example.com"))
    conn.close()

    from app.websocket.chat_handler import handle_customer_ws
    # No client_request_id in payload
    ws = MockWebSocket([json.dumps({"message": "I want a human"})])
    
    with patch("app.agents.graph.customer_support_graph.ainvoke", new_callable=AsyncMock) as mock_graph:
        mock_graph.return_value = {"response": "Escalating!", "escalated": True}
        await handle_customer_ws(ws, session_id, customer_id)
        
    assert any("Missing client_request_id. Cannot perform escalation." in m for m in ws.sent_messages)
    
    conn = psycopg2.connect(conn_string)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM escalation_events WHERE session_id = %s", (session_id,))
    assert cur.fetchone()[0] == 0
    conn.close()

@pytest.mark.asyncio
async def test_reconstruct_human_routing():
    # C: Redis missing, Postgres ACTIVE -> reconstruct human, current request uses human path.
    session_id = f"sess_{uuid.uuid4()}"
    customer_id = 123
    
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("INSERT INTO customers (id, name, email) VALUES (%s, 'Test', %s) ON CONFLICT (id) DO NOTHING", (customer_id, f"test_{uuid.uuid4()}@example.com"))
    cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", (session_id, customer_id))
    conn.close()
    
    from app.websocket.chat_handler import handle_customer_ws
    ws = MockWebSocket([json.dumps({"message": "Hello", "client_request_id": str(uuid.uuid4())})])
    
    with patch("app.agents.graph.customer_support_graph.ainvoke", new_callable=AsyncMock) as mock_graph:
        await handle_customer_ws(ws, session_id, customer_id)
        
    # AI should not be called
    mock_graph.assert_not_called()
    assert any("I'm connecting you to a human" in m or '"type": "agent_response"' not in m for m in ws.sent_messages)

@pytest.mark.asyncio
async def test_reconstruct_ai_routing():
    # D: Redis missing, Postgres RELEASED -> reconstruct ai, current request uses AI path.
    session_id = f"sess_{uuid.uuid4()}"
    customer_id = 123
    
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("INSERT INTO customers (id, name, email) VALUES (%s, 'Test', %s) ON CONFLICT (id) DO NOTHING", (customer_id, f"test_{uuid.uuid4()}@example.com"))
    cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'RELEASED')", (session_id, customer_id))
    conn.close()
    
    from app.websocket.chat_handler import handle_customer_ws
    ws = MockWebSocket([json.dumps({"message": "Hello", "client_request_id": str(uuid.uuid4())})])
    
    with patch("app.agents.graph.customer_support_graph.ainvoke", new_callable=AsyncMock) as mock_graph:
        mock_graph.return_value = {"response": "I am AI", "escalated": False}
        await handle_customer_ws(ws, session_id, customer_id)
        
    mock_graph.assert_called_once()


@pytest.mark.asyncio
async def test_chat_handler_release_success():
    from app.websocket.chat_handler import handle_agent_ws
    from app.websocket.connection import manager
    from app.persistence.session import session_store
    
    session_id = f"sess_{uuid.uuid4()}"
    customer_id = 999
    client_request_id = str(uuid.uuid4())
    
    session_store[session_id] = {
        "customer_id": customer_id,
        "mode": "human"
    }
    
    # Ensure durable state is ACTIVE
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", (session_id, customer_id))
    conn.close()
    
    ws = MockWebSocket(messages=[
        {"type": "release", "client_request_id": client_request_id}
    ])
    
    await handle_agent_ws(ws, session_id)
    
    # Assert durable state changed to RELEASED
    conn = psycopg2.connect(conn_string)
    cur = conn.cursor()
    cur.execute("SELECT status FROM session_escalation_state WHERE session_id = %s", (session_id,))
    assert cur.fetchone()[0] == 'RELEASED'
    conn.close()
    
    # Assert Redis mode changed to ai
    assert session_store.get(session_id)["mode"] == "ai"

@pytest.mark.asyncio
async def test_chat_handler_release_missing_request_id_fails_closed():
    from app.websocket.chat_handler import handle_agent_ws
    from app.websocket.connection import manager
    from app.persistence.session import session_store
    
    session_id = f"sess_{uuid.uuid4()}"
    customer_id = 999
    
    session_store[session_id] = {
        "customer_id": customer_id,
        "mode": "human"
    }
    
    # Ensure durable state is ACTIVE
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", (session_id, customer_id))
    conn.close()
    
    ws = MockWebSocket(messages=[
        {"type": "release"} # Missing client_request_id
    ])
    
    await handle_agent_ws(ws, session_id)
    
    # Assert durable state did not change
    conn = psycopg2.connect(conn_string)
    cur = conn.cursor()
    cur.execute("SELECT status FROM session_escalation_state WHERE session_id = %s", (session_id,))
    assert cur.fetchone()[0] == 'ACTIVE'
    conn.close()
    
    # Assert Redis mode did not change
    assert session_store.get(session_id)["mode"] == "human"



@pytest.mark.asyncio
async def test_chat_handler_release_success(monkeypatch):
    from app.websocket.chat_handler import handle_agent_ws
    from app.websocket.connection import manager
    from app.persistence.session import session_store
    import psycopg2
    
    session_id = f"sess_{uuid.uuid4()}"
    customer_id = 999
    client_request_id = str(uuid.uuid4())
    
    await session_store.create_session_if_absent(session_id, {
        "session_id": session_id,
        "customer_id": customer_id,
        "_session_revision": 1,
        "mode": "human",
        "conversation_history": []
    })
    
    # Ensure durable state is ACTIVE
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", (session_id, customer_id))
    conn.close()
    
    def mock_release(request):
        conn = psycopg2.connect(conn_string)
        conn.autocommit = True
        try:
            cur = conn.cursor()
            cur.execute("UPDATE session_escalation_state SET status = 'RELEASED' WHERE session_id = %s", (request.session_id,))
        finally:
            conn.close()
        from app.escalation.service import ReleaseResult
        return ReleaseResult(status="success", message="Released", session_id=request.session_id)

    monkeypatch.setattr("app.escalation.service.EscalationService.release_session", mock_release)

    ws = MockWebSocket(messages=[
        json.dumps({"type": "release", "client_request_id": client_request_id})
    ])
    
    await handle_agent_ws(ws, session_id)
    
    # Assert durable state changed to RELEASED
    conn = psycopg2.connect(conn_string)
    cur = conn.cursor()
    cur.execute("SELECT status FROM session_escalation_state WHERE session_id = %s", (session_id,))
    assert cur.fetchone()[0] == 'RELEASED'
    conn.close()
    
    session = await session_store.get_session(session_id)
    assert session["mode"] == "ai"

@pytest.mark.asyncio
async def test_chat_handler_release_missing_request_id_fails_closed():
    from app.websocket.chat_handler import handle_agent_ws
    from app.websocket.connection import manager
    from app.persistence.session import session_store
    import psycopg2
    
    session_id = f"sess_{uuid.uuid4()}"
    customer_id = 999
    
    await session_store.create_session_if_absent(session_id, {
        "session_id": session_id,
        "customer_id": customer_id,
        "_session_revision": 1,
        "mode": "human",
        "conversation_history": []
    })
    
    # Ensure durable state is ACTIVE
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", (session_id, customer_id))
    conn.close()
    
    ws = MockWebSocket(messages=[
        json.dumps({"type": "release"}) # Missing client_request_id
    ])
    
    await handle_agent_ws(ws, session_id)
    
    # Assert durable state did not change
    conn = psycopg2.connect(conn_string)
    cur = conn.cursor()
    cur.execute("SELECT status FROM session_escalation_state WHERE session_id = %s", (session_id,))
    assert cur.fetchone()[0] == 'ACTIVE'
    conn.close()
    
    session = await session_store.get_session(session_id)
    assert session["mode"] == "human"

