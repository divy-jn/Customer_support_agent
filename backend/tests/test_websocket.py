"""
WebSocket connection and message tests.

Uses FastAPI TestClient for WebSocket testing.

Usage:
    cd backend
    python -m pytest tests/test_websocket.py -v
"""

import json
import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
from app.main import app


class TestHealthEndpoint:
    """Test the health check endpoint."""

    def test_health_check(self):
        """Health endpoint should return 200 or 503."""
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code in (200, 503), f"Unexpected status: {response.status_code}"
        data = response.json()
        assert "status" in data
        assert "service" in data
        assert data["service"] == "customer-support-ai"


class TestCORS:
    """Test CORS configuration."""

    def test_cors_localhost_3000(self):
        """CORS should allow localhost:3000."""
        client = TestClient(app)
        response = client.options(
            "/api/v1/customers",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"

    def test_cors_127_3000(self):
        """CORS should allow 127.0.0.1:3000."""
        client = TestClient(app)
        response = client.options(
            "/api/v1/customers",
            headers={
                "Origin": "http://127.0.0.1:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"

    def test_cors_localhost_3001(self):
        """CORS should allow localhost:3001."""
        client = TestClient(app)
        response = client.options(
            "/api/v1/customers",
            headers={
                "Origin": "http://localhost:3001",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:3001"


class TestRESTEndpoints:
    """Test REST API endpoints."""

    def test_get_customers(self):
        """GET /api/v1/customers should return customer list."""
        client = TestClient(app)
        response = client.get("/api/v1/customers?limit=5")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_get_tickets(self):
        """GET /api/v1/tickets should return ticket list."""
        client = TestClient(app)
        response = client.get("/api/v1/tickets?limit=5")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_search_customer_empty_query(self):
        """Search with empty query should return 400."""
        client = TestClient(app)
        response = client.get("/api/v1/customers/search?q=")
        assert response.status_code == 400

    def test_dashboard_stats(self):
        """GET /api/v1/dashboard/stats should return statistics."""
        client = TestClient(app)
        response = client.get("/api/v1/dashboard/stats", headers={"X-Agent-Token": "test-agent-secret"})
        assert response.status_code == 200
        data = response.json()
        assert "total_tickets" in data

    def test_dashboard_stats_missing_auth(self):
        client = TestClient(app)
        response = client.get("/api/v1/dashboard/stats")
        assert response.status_code == 401

    def test_dashboard_stats_customer_role(self, auth_token):
        client = TestClient(app)
        response = client.get("/api/v1/dashboard/stats", headers={"Authorization": f"Bearer {auth_token}"})
        assert response.status_code == 401



import jwt
from datetime import datetime, timedelta, timezone
from app.config import settings
import os

# Ensure tests run with correct settings if not already
os.environ["DEBUG_MODE"] = "True"
os.environ["AGENT_SECRET"] = "test-agent-secret"
os.environ["JWT_SECRET"] = "dev-secret-key-that-is-long-enough-for-testing-purposes"

# Force config reload for this module if needed
settings.debug_mode = True
settings.agent_secret = "test-agent-secret"
settings.jwt_secret = "dev-secret-key-that-is-long-enough-for-testing-purposes"

@pytest.fixture
def auth_token():
    expiration = datetime.now(timezone.utc) + timedelta(minutes=60)
    payload = {
        "sub": "1",
        "role": "customer",
        "exp": expiration
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

@pytest.fixture
def agent_token():
    expiration = datetime.now(timezone.utc) + timedelta(minutes=60)
    payload = {
        "sub": "2",
        "role": "agent",
        "exp": expiration
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

class TestWebSocketConnection:
    """Test WebSocket chat connections."""

    @patch("app.persistence.escalation.ensure_durable_escalation_state", new_callable=MagicMock)
    def test_websocket_connect(self, mock_get_session_state, auth_token):
        from app.models import EscalationLifecycleStatus
        mock_get_session_state.return_value = EscalationLifecycleStatus.NONE
        """WebSocket should connect and receive welcome message."""
        client = TestClient(app)
        with client.websocket_connect(f"/ws/chat?token={auth_token}") as websocket:
            data = websocket.receive_json()
            assert data["type"] == "system"
            assert "session_id" in data
            assert "Welcome" in data["message"] or "Hello" in data["message"]

    @patch("app.persistence.escalation.ensure_durable_escalation_state", new_callable=MagicMock)
    def test_websocket_connect_with_session(self, mock_get_session_state, auth_token):
        from app.models import EscalationLifecycleStatus
        mock_get_session_state.return_value = EscalationLifecycleStatus.NONE
        """WebSocket should connect with a specific session ID."""
        client = TestClient(app)
        with client.websocket_connect(f"/ws/chat/test-session-ws-001?token={auth_token}") as websocket:
            data = websocket.receive_json()
            assert data["type"] == "system"
            assert data["session_id"] == "test-session-ws-001"

    @patch("app.persistence.escalation.ensure_durable_escalation_state", new_callable=MagicMock)
    def test_websocket_send_empty_message(self, mock_get_session_state, auth_token):
        from app.models import EscalationLifecycleStatus
        mock_get_session_state.return_value = EscalationLifecycleStatus.NONE
        """Empty messages should be silently ignored (no response)."""
        client = TestClient(app)
        with client.websocket_connect(f"/ws/chat?token={auth_token}") as websocket:
            # Receive welcome
            welcome = websocket.receive_json()
            assert welcome["type"] == "system"

            # Send empty message
            websocket.send_json({"message": ""})

            # Send a valid message to verify connection is still alive
            websocket.send_json({"message": "hello"})

            # We should get a typing indicator or response (not crash)
            response = websocket.receive_json()
            # Could be "typing" or "agent_response" depending on processing speed
            assert response["type"] in ("typing", "agent_response", "ping"), f"Expected typing or agent_response, got {response}"

    @patch("app.persistence.escalation.ensure_durable_escalation_state", new_callable=MagicMock)
    def test_websocket_send_plain_text(self, mock_get_session_state, auth_token):
        from app.models import EscalationLifecycleStatus
        mock_get_session_state.return_value = EscalationLifecycleStatus.NONE
        """WebSocket should handle plain text (not JSON) gracefully."""
        client = TestClient(app)
        with client.websocket_connect(f"/ws/chat?token={auth_token}") as websocket:
            welcome = websocket.receive_json()
            assert welcome["type"] == "system"

            # Send plain text instead of JSON
            websocket.send_text("hello plain text")

            # Should get typing indicator then response
            response = websocket.receive_json()
            assert response["type"] in ("typing", "agent_response", "ping"), f"Expected typing or agent_response, got {response}"

    @patch("app.persistence.escalation.ensure_durable_escalation_state")
    @patch("app.websocket.chat_handler.customer_support_graph.ainvoke", new_callable=AsyncMock)
    @patch("app.tools.supabase")
    def test_websocket_reconnection_persistence(self, mock_supabase, mock_call_agent, mock_get_session_state, auth_token):
        from app.models import EscalationLifecycleStatus
        mock_get_session_state.return_value = EscalationLifecycleStatus.NONE
        
        # Mock LLM to return a fast response
        mock_call_agent.return_value = {"messages": [{"role": "agent", "content": "mock LLM response", "metadata": {}}]}
        
        # Mock supabase.table().upsert().execute() to prevent real network calls
        mock_supabase.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [{"customer_id": 1}]
        
        client = TestClient(app)
        import uuid
        session_id = f"test-reconnect-{uuid.uuid4().hex[:6]}"
        
        messages = ["message A", "message B"]
        for msg_text in messages:
            with client.websocket_connect(f"/ws/chat/{session_id}?token={auth_token}") as websocket:
                websocket.receive_json() # welcome
                websocket.send_json({"message": msg_text})
                
                # Receive LLM response
                msg = websocket.receive_json()
                while msg.get("type") in ("ping", "typing", "system"):
                    msg = websocket.receive_json()
                
                assert msg["type"] in ("agent_response", "customer_message")
                
        # Validate that the DB upsert was called
        assert mock_supabase.table.called

    @patch("app.escalation.service.EscalationService.escalate_session")
    @patch("app.persistence.escalation.ensure_durable_escalation_state")
    def test_failed_durable_takeover_does_not_mutate_mode(self, mock_get_session_state, mock_escalate, auth_token, agent_token):
        from app.models import EscalationLifecycleStatus
        from app.escalation.service import EscalationResult
        mock_get_session_state.return_value = EscalationLifecycleStatus.NONE
        
        # Mock the service to fail
        mock_escalate.return_value = EscalationResult(status="error", message="Simulated DB Failure", session_id="test_session", error="Simulated DB Failure")
        
        client = TestClient(app)
        import uuid
        session_id = f"test-takeover-fail-{uuid.uuid4().hex[:6]}"
        
        # Customer connects
        with client.websocket_connect(f"/ws/chat/{session_id}?token={auth_token}") as customer_ws:
            customer_ws.receive_json() # welcome
            
            # Agent connects
            with client.websocket_connect(f"/ws/agent/{session_id}?token={agent_token}") as agent_ws:
                agent_ws.receive_json() # session state
                
                # Agent tries to take over
                req_id = str(uuid.uuid4())
                agent_ws.send_json({"type": "takeover", "agent_name": "Test Human", "client_request_id": req_id})
                
                # Agent should receive error
                err = agent_ws.receive_json()
                assert err["type"] == "error"
                assert "Simulated DB Failure" in err["message"]
                
                # Customer should NOT receive takeover message
                # Send a message from agent to prove mode is still AI
                agent_ws.send_json({"type": "agent_message", "message": "hello", "agent_name": "Test Human"})
                
                err2 = agent_ws.receive_json()
                assert err2["type"] == "error"
                assert "Take over the conversation before sending" in err2["message"]

    @patch("app.persistence.escalation.ensure_durable_escalation_state")
    def test_reconnect_reads_persisted_state_correctly(self, mock_get_session_state, auth_token):
        from app.models import EscalationLifecycleStatus
        # Suppose the DB already says this session is ACTIVE (escalated)
        mock_get_session_state.return_value = EscalationLifecycleStatus.ACTIVE
        
        client = TestClient(app)
        session_id = "test-reconnect-escalated"
        
        with client.websocket_connect(f"/ws/chat/{session_id}?token={auth_token}") as customer_ws:
            welcome = customer_ws.receive_json()
            assert welcome["type"] == "system"
            
            with patch("app.websocket.chat_handler.customer_support_graph.ainvoke", new_callable=AsyncMock) as mock_call_agent:
                customer_ws.send_json({"message": "Hello in human mode"})
                import time
                time.sleep(0.1)
                # Ensure the AI agent was NOT invoked because the session mode was read from durable state as 'human'
                mock_call_agent.assert_not_called()

    @patch("app.persistence.escalation.ensure_durable_escalation_state", new_callable=MagicMock)
    def test_websocket_reconnection_race_condition(self, mock_get_session_state, auth_token):
        from app.models import EscalationLifecycleStatus
        mock_get_session_state.return_value = EscalationLifecycleStatus.NONE
        """
        Regression test:
        WS1 connects for session X
        WS2 connects for SAME session X
        WS2 becomes active connection
        WS1 disconnects
        -> active_connections[X] MUST still point to WS2
        Then WS2 sends/receives a message successfully.
        """
        import uuid
        session_id = f"test-race-{uuid.uuid4().hex[:6]}"
        uri = f"/ws/chat/{session_id}?token={auth_token}"
        client = TestClient(app)
        
        # Connect WS2 (the new active connection)
        with client.websocket_connect(uri) as ws2_conn:
            ws2_conn.receive_json() # welcome
            
            from app.websocket.connection import manager
            
            # Simulate the late disconnect of an OLD websocket (WS1)
            class MockWebsocket:
                pass
            old_ws = MockWebsocket()
            
            # The disconnect of the OLD websocket should NOT delete the NEW connection
            manager.disconnect_customer(session_id, old_ws)
            
            assert session_id in manager.active_connections, "WS2 was erroneously deleted by WS1 disconnect"
            
            # Now verify WS2 still works
            ws2_conn.send_json({"message": "hello"})
            
            # Should receive typing or agent_response
            response = ws2_conn.receive_json()
            assert response["type"] in ("typing", "agent_response", "ping")

class TestHumanHandover:
    """Test human-agent takeover and release flows."""

    @pytest.mark.skip(reason="Hangs with TestClient WebSocket")
    def test_agent_takeover_and_release(self, auth_token):
        """Complete lifecycle: AI -> Human Takeover -> Human Message -> Release -> AI."""
        client = TestClient(app)
        session_id = "test-handover-session-001"
        
        # 1. Customer connects
        with client.websocket_connect(f"/ws/chat/{session_id}?token={auth_token}") as customer_ws:
            welcome = customer_ws.receive_json()
            assert welcome["type"] == "system"
            
            # 2. Agent connects to dashboard
            with client.websocket_connect(f"/ws/agent/{session_id}?agent_secret=test-agent-secret") as agent_ws:
                agent_welcome = agent_ws.receive_json()
                assert agent_welcome["type"] == "session_state"
                
                # 3. Agent takes over
                agent_ws.send_json({"type": "takeover", "agent_name": "Test Human"})
                
                # Customer should receive system message about takeover
                cust_takeover_msg = customer_ws.receive_json()
                # Skip ping/typing if any
                while cust_takeover_msg["type"] in ("ping", "typing"):
                    cust_takeover_msg = customer_ws.receive_json()
                
                assert cust_takeover_msg["type"] == "system"
                assert cust_takeover_msg["mode"] == "human"
                
                # 4. Customer sends a message in human mode
                customer_ws.send_json({"message": "Hello Human"})
                
                # Agent should receive customer message
                agent_receives_msg = agent_ws.receive_json()
                while agent_receives_msg["type"] in ("ping", "typing"):
                    agent_receives_msg = agent_ws.receive_json()
                assert agent_receives_msg["type"] == "customer_message"
                assert agent_receives_msg["message"] == "Hello Human"
                
                # 5. Agent sends a message
                agent_ws.send_json({"type": "agent_message", "message": "Hi, I am human", "agent_name": "Test Human"})
                
                # Customer receives agent message
                cust_agent_msg = customer_ws.receive_json()
                while cust_agent_msg["type"] in ("ping", "typing"):
                    cust_agent_msg = customer_ws.receive_json()
                
                assert cust_agent_msg["type"] == "agent_response"
                assert cust_agent_msg["message"] == "Hi, I am human"
                assert cust_agent_msg["agent_name"] == "Test Human"
                
                # 6. Agent releases conversation
                agent_ws.send_json({"type": "release"})
                
                # Customer receives release system message
                cust_release_msg = customer_ws.receive_json()
                while cust_release_msg["type"] in ("ping", "typing"):
                    cust_release_msg = customer_ws.receive_json()
                
                assert cust_release_msg["type"] == "system"
                assert cust_release_msg["mode"] == "ai"

class TestDualActiveSessions:
    """Test connection management for the same session and same customer.

    Production contract:
    - Connections are keyed by session_id, not customer_id.
    - Reconnecting to the SAME session_id replaces the old websocket in the
      connection manager (last-writer-wins).
    - Two different session_ids for the same authenticated customer coexist
      independently — there is no customer-level eviction.
    """

    @patch("app.persistence.escalation.ensure_durable_escalation_state", new_callable=MagicMock)
    @pytest.mark.asyncio
    async def test_same_session_reconnect_replaces_connection(self, mock_esc):
        """Reconnecting to the same session_id overwrites the active_connections entry."""
        from app.models import EscalationLifecycleStatus
        mock_esc.return_value = EscalationLifecycleStatus.NONE
        from app.websocket.connection import manager

        session_id = "test-session-dual-replace"
        
        class DummyWS:
            async def send_text(self, data): pass
            async def close(self): pass
            async def accept(self): pass
            
        ws1 = DummyWS()
        ws2 = DummyWS()
        
        await manager.connect_customer(ws1, session_id)
        assert manager.active_connections[session_id] == ws1
        
        await manager.connect_customer(ws2, session_id)
        assert manager.active_connections[session_id] == ws2

    @patch("app.persistence.escalation.ensure_durable_escalation_state", new_callable=MagicMock)
    @pytest.mark.asyncio
    async def test_different_sessions_same_customer_coexist(self, mock_esc):
        """Two different session_ids for the same JWT customer both remain active."""
        from app.models import EscalationLifecycleStatus
        mock_esc.return_value = EscalationLifecycleStatus.NONE
        from app.websocket.connection import manager

        session1_id = "sess1"
        session2_id = "sess2"
        
        class DummyWS:
            async def send_text(self, data): pass
            async def close(self): pass
            async def accept(self): pass
            
        ws1 = DummyWS()
        ws2 = DummyWS()
        
        await manager.connect_customer(ws1, session1_id)
        await manager.connect_customer(ws2, session2_id)
        
        # Both sessions are distinct and coexist
        assert manager.active_connections[session1_id] == ws1
        assert manager.active_connections[session2_id] == ws2
        
        manager.disconnect_customer(session1_id)
        manager.disconnect_customer(session2_id)
