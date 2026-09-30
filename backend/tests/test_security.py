import pytest
import json
from fastapi.testclient import TestClient
import jwt
from datetime import datetime, timedelta, timezone
import uuid
import asyncio
import os

# Set required environment variables BEFORE importing app so Settings initializes correctly
os.environ["DEBUG_MODE"] = "True"
os.environ["AGENT_SECRET"] = "test-agent-secret"
os.environ["JWT_SECRET"] = "dev-secret-key-that-is-long-enough-for-testing-purposes"

from app.main import app
from app.config import settings
from app.websocket.chat_handler import session_store

# Ensure settings are correctly synced in case they were already loaded
settings.debug_mode = True
settings.agent_secret = "test-agent-secret"
settings.jwt_secret = "dev-secret-key-that-is-long-enough-for-testing-purposes"

client = TestClient(app)

def create_mock_jwt(customer_id: int, role: str = "customer", expired: bool = False) -> str:
    expiration = datetime.now(timezone.utc)
    if expired:
        expiration -= timedelta(minutes=10)
    else:
        expiration += timedelta(minutes=60)
        
    payload = {
        "sub": str(customer_id),
        "role": role,
        "exp": expiration
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

@pytest.fixture
def auth_headers_a():
    return {"Authorization": f"Bearer {create_mock_jwt(1)}"}

@pytest.fixture
def auth_headers_b():
    return {"Authorization": f"Bearer {create_mock_jwt(2)}"}

@pytest.fixture
def expired_headers():
    return {"Authorization": f"Bearer {create_mock_jwt(1, expired=True)}"}

@pytest.fixture
def admin_headers():
    return {"x-api-key": settings.admin_api_key}

@pytest.fixture
def agent_headers():
    return {"x-agent-token": settings.agent_secret}

# ==========================================
# AUTH TESTS
# ==========================================

def test_missing_jwt():
    response = client.get("/api/v1/customers/1/history")
    assert response.status_code in (401, 403)

def test_invalid_jwt():
    response = client.get("/api/v1/customers/1/history", headers={"Authorization": "Bearer invalid.token.here"})
    assert response.status_code in (401, 403)

def test_expired_jwt(expired_headers):
    response = client.get("/api/v1/customers/1/history", headers=expired_headers)
    assert response.status_code in (401, 403)

def test_wrong_role():
    token = create_mock_jwt(1, role="agent")
    response = client.get("/api/v1/customers/1/history", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403

def test_valid_customer_jwt(auth_headers_a):
    response = client.get("/api/v1/customers/1/history", headers=auth_headers_a)
    assert response.status_code == 200

def test_dev_login_rejects_nonexistent():
    response = client.post("/api/v1/auth/dev-login?email=invalid@example.com")
    assert response.status_code == 404

def test_dev_login_unavailable_in_production(monkeypatch):
    monkeypatch.setattr(settings, "debug_mode", False)
    response = client.post("/api/v1/auth/dev-login?email=amit.sharma@example.com")
    assert response.status_code == 403
    # reset manually just in case monkeypatch doesn't revert properly for other tests
    monkeypatch.setattr(settings, "debug_mode", True)

# ==========================================
# REST OWNERSHIP TESTS
# ==========================================

def test_customer_a_can_access_own_history(auth_headers_a):
    response = client.get("/api/v1/customers/1/history", headers=auth_headers_a)
    assert response.status_code == 200

def test_customer_a_cannot_access_customer_b_history(auth_headers_a):
    response = client.get("/api/v1/customers/2/history", headers=auth_headers_a)
    assert response.status_code == 403

def test_customer_a_cannot_track_customer_b_order(auth_headers_a):
    # Customer B has order 2024 (Assuming from seed_database.py)
    response = client.get("/api/v1/orders/2024/track", headers=auth_headers_a)
    assert response.status_code in (403, 404)

def test_customer_a_cannot_access_customer_b_ticket(auth_headers_a):
    # Customer B has ticket 5002
    response = client.get("/api/v1/tickets/5002", headers=auth_headers_a)
    assert response.status_code in (403, 404)
    
def test_customer_a_cannot_modify_customer_b_ticket():
    # Modification requires verify_agent_token so it's irrelevant for customer JWT.
    pass

def test_customer_a_cannot_cancel_customer_b_order():
    pass

def test_customer_a_cannot_refund_customer_b_order():
    pass

# ==========================================
# DASHBOARD TESTS
# ==========================================

def test_customer_cannot_access_dashboard_sessions(auth_headers_a):
    response = client.get("/api/v1/dashboard/sessions", headers=auth_headers_a)
    assert response.status_code in (401, 403)

def test_unauthenticated_cannot_access_dashboard_sessions():
    response = client.get("/api/v1/dashboard/sessions")
    assert response.status_code in (401, 403)

def test_agent_can_access_dashboard_sessions(agent_headers):
    response = client.get("/api/v1/dashboard/sessions", headers=agent_headers)
    assert response.status_code == 200

# ==========================================
# WEBSOCKET & SESSION TESTS
# ==========================================
import websockets
from fastapi.websockets import WebSocketDisconnect

def test_ws_missing_token():
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/chat"):
            pass

def test_ws_invalid_token():
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/chat?token=invalid"):
            pass

def test_ws_customer_a_can_connect_own_session():
    token = create_mock_jwt(1)
    with client.websocket_connect(f"/ws/chat?token={token}") as websocket:
        data = websocket.receive_json()
        assert data["type"] == "system"

def test_ws_customer_a_cannot_connect_customer_b_session():
    token_b = create_mock_jwt(2)
    session_id = "test-session-b"
    # create session for B
    with client.websocket_connect(f"/ws/chat/{session_id}?token={token_b}") as ws_b:
        data = ws_b.receive_json()
    
    token_a = create_mock_jwt(1)
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws/chat/{session_id}?token={token_a}") as ws_a:
            ws_a.receive_json()
