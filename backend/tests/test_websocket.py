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
        response = client.get("/api/v1/dashboard/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total_tickets" in data


class TestWebSocketConnection:
    """Test WebSocket chat connections."""

    def test_websocket_connect(self):
        """WebSocket should connect and receive welcome message."""
        client = TestClient(app)
        with client.websocket_connect("/ws/chat") as websocket:
            data = websocket.receive_json()
            assert data["type"] == "system"
            assert "session_id" in data
            assert "Welcome" in data["message"] or "Hello" in data["message"]

    def test_websocket_connect_with_session(self):
        """WebSocket should connect with a specific session ID."""
        client = TestClient(app)
        with client.websocket_connect("/ws/chat/test-session-ws-001") as websocket:
            data = websocket.receive_json()
            assert data["type"] == "system"
            assert data["session_id"] == "test-session-ws-001"

    def test_websocket_send_empty_message(self):
        """Empty messages should be silently ignored (no response)."""
        client = TestClient(app)
        with client.websocket_connect("/ws/chat") as websocket:
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
            assert response["type"] in ("typing", "agent_response", "ping")

    def test_websocket_send_plain_text(self):
        """WebSocket should handle plain text (not JSON) gracefully."""
        client = TestClient(app)
        with client.websocket_connect("/ws/chat") as websocket:
            welcome = websocket.receive_json()
            assert welcome["type"] == "system"

            # Send plain text instead of JSON
            websocket.send_text("hello plain text")

            # Should get typing indicator then response
            response = websocket.receive_json()
            assert response["type"] in ("typing", "agent_response", "ping")
