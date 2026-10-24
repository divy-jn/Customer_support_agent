import pytest
import uuid
from fastapi.testclient import TestClient
from app.main import app
from app.config import settings
import jwt
from datetime import datetime, timedelta, timezone

client = TestClient(app)

def create_token(role: str, sub: str):
    expiration = datetime.now(timezone.utc) + timedelta(minutes=60)
    payload = {"sub": sub, "role": role, "exp": expiration}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)

@pytest.fixture
def agent_token():
    return create_token("agent", "agent-1")

@pytest.fixture
def customer_token():
    return create_token("customer", "123")

@pytest.fixture
def other_customer_token():
    return create_token("customer", "456")

@pytest.fixture
def mocker():
    from unittest.mock import patch
    patches = []
    class Mocker:
        def patch(self, *args, **kwargs):
            p = patch(*args, **kwargs)
            started = p.start()
            patches.append(p)
            return started
    yield Mocker()
    for p in reversed(patches):
        p.stop()

class TestPhase2Security:
    
    def test_unauthenticated_access_rejected(self):
        # /customers and /tickets should require agent token
        resp1 = client.get("/api/v1/customers")
        assert resp1.status_code == 401
        
        resp2 = client.get("/api/v1/tickets")
        assert resp2.status_code == 401
        
        resp3 = client.get("/api/v1/customers/search?q=test")
        assert resp3.status_code == 401

    def test_customer_token_rejected_on_agent_endpoints(self, customer_token):
        headers = {"Authorization": f"Bearer {customer_token}"}
        # /customers and /tickets list are agent only
        resp1 = client.get("/api/v1/customers", headers=headers)
        assert resp1.status_code == 401
        
        resp2 = client.get("/api/v1/tickets", headers=headers)
        assert resp2.status_code == 401

    def test_agent_token_allowed(self, agent_token, mocker):
        headers = {"Authorization": f"Bearer {agent_token}"}
        mocker.patch("app.routes.get_all_customers", return_value="[]")
        resp1 = client.get("/api/v1/customers", headers=headers)
        assert resp1.status_code == 200
        assert resp1.json() == []

    def test_customer_can_search_self_only(self, customer_token, mocker):
        headers = {"Authorization": f"Bearer {customer_token}"}
        
        # Mock lookup to return two customers
        mock_data = '[{"id": 123, "name": "Me"}, {"id": 456, "name": "Other"}]'
        mocker.patch("app.routes.lookup_customer", return_value=mock_data)
        
        resp = client.get("/api/v1/customers/search?q=test", headers=headers)
        assert resp.status_code == 200
        # Should filter out id=456 because token is for 123
        assert len(resp.json()) == 1
        assert resp.json()[0]["id"] == 123

    def test_customer_cannot_access_other_customer_history(self, customer_token, other_customer_token):
        headers = {"Authorization": f"Bearer {customer_token}"}
        
        # Accessing own history - should pass auth (might fail 404 in mock)
        # Using 456's history with 123's token
        resp2 = client.get("/api/v1/customers/456/history", headers=headers)
        assert resp2.status_code == 403

    def test_order_mutation_requires_client_request_id(self, agent_token):
        headers = {"Authorization": f"Bearer {agent_token}"}
        
        # Missing header
        resp1 = client.post("/api/v1/orders/123/cancel", headers=headers)
        assert resp1.status_code == 422 # FastAPI missing header validation
        
        resp2 = client.post("/api/v1/orders/123/refund", headers=headers)
        assert resp2.status_code == 422
        
    def test_order_mutation_passes_client_request_id(self, agent_token, mocker):
        headers = {
            "Authorization": f"Bearer {agent_token}",
            "X-Client-Request-Id": str(uuid.uuid4())
        }
        
        # Mock the underlying tools
        mock_cancel = mocker.patch("app.routes.cancel_order", return_value='{"status": "success"}')
        resp = client.post("/api/v1/orders/123/cancel", headers=headers)
        
        assert resp.status_code == 200
        mock_cancel.assert_called_once()
        assert mock_cancel.call_args[0][0] == 123
        assert mock_cancel.call_args[1]["client_request_id"] == headers["X-Client-Request-Id"]


import json
from unittest.mock import MagicMock
from app.tools import cancel_order, process_refund

class TestOrderMutationCustomerIdPropagation:
    """Isolated unit tests verifying canonical customer_id derivation and RPC parameters."""

    def test_cancel_order_derives_customer_id_when_omitted(self, mocker):
        mock_supabase = mocker.patch("app.tools.supabase")
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 101, "customer_id": 42, "status": "active"}
        ]
        mock_rpc_exec = MagicMock(data={"status": "success"})
        mock_supabase.rpc.return_value.execute.return_value = mock_rpc_exec
        
        req_id = str(uuid.uuid4())
        res_str = cancel_order(order_id=101, customer_id=None, client_request_id=req_id)
        res = json.loads(res_str)
        assert res.get("status") == "success"
        
        mock_supabase.rpc.assert_called_once()
        rpc_name, rpc_params = mock_supabase.rpc.call_args[0]
        assert rpc_name == "execute_idempotent_operation"
        assert rpc_params["p_customer_id"] == 42
        assert rpc_params["p_client_request_id"] == req_id
        assert rpc_params["p_canonical_target"] == "101"
        assert rpc_params["p_operation_type"] == "cancel_order"

    def test_cancel_order_validates_matching_customer_id(self, mocker):
        mock_supabase = mocker.patch("app.tools.supabase")
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 101, "customer_id": 42, "status": "active"}
        ]
        mock_rpc_exec = MagicMock(data={"status": "success"})
        mock_supabase.rpc.return_value.execute.return_value = mock_rpc_exec
        
        req_id = str(uuid.uuid4())
        res_str = cancel_order(order_id=101, customer_id=42, client_request_id=req_id)
        res = json.loads(res_str)
        assert res.get("status") == "success"
        
        rpc_name, rpc_params = mock_supabase.rpc.call_args[0]
        assert rpc_params["p_customer_id"] == 42

    def test_cancel_order_rejects_conflicting_customer_id(self, mocker):
        mock_supabase = mocker.patch("app.tools.supabase")
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 101, "customer_id": 42, "status": "active"}
        ]
        
        req_id = str(uuid.uuid4())
        res_str = cancel_order(order_id=101, customer_id=99, client_request_id=req_id)
        res = json.loads(res_str)
        assert "error" in res
        assert "not belong to you" in res["error"]
        mock_supabase.rpc.assert_not_called()

    def test_cancel_order_missing_order_does_not_call_rpc(self, mocker):
        mock_supabase = mocker.patch("app.tools.supabase")
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        
        req_id = str(uuid.uuid4())
        res_str = cancel_order(order_id=999, client_request_id=req_id)
        res = json.loads(res_str)
        assert "error" in res
        assert "not found" in res["error"]
        mock_supabase.rpc.assert_not_called()

    def test_process_refund_derives_customer_id_when_omitted(self, mocker):
        mock_supabase = mocker.patch("app.tools.supabase")
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 202, "customer_id": 88, "status": "delivered"}
        ]
        mock_rpc_exec = MagicMock(data={"status": "success"})
        mock_supabase.rpc.return_value.execute.return_value = mock_rpc_exec
        
        req_id = str(uuid.uuid4())
        res_str = process_refund(order_id=202, customer_id=None, client_request_id=req_id)
        res = json.loads(res_str)
        assert res.get("status") == "success"
        
        mock_supabase.rpc.assert_called_once()
        rpc_name, rpc_params = mock_supabase.rpc.call_args[0]
        assert rpc_name == "execute_idempotent_operation"
        assert rpc_params["p_customer_id"] == 88
        assert rpc_params["p_client_request_id"] == req_id
        assert rpc_params["p_canonical_target"] == "202"
        assert rpc_params["p_operation_type"] == "process_refund"

    def test_process_refund_rejects_conflicting_customer_id(self, mocker):
        mock_supabase = mocker.patch("app.tools.supabase")
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": 202, "customer_id": 88, "status": "delivered"}
        ]
        
        req_id = str(uuid.uuid4())
        res_str = process_refund(order_id=202, customer_id=99, client_request_id=req_id)
        res = json.loads(res_str)
        assert "error" in res
        assert "not belong to you" in res["error"]
        mock_supabase.rpc.assert_not_called()

    def test_process_refund_missing_order_does_not_call_rpc(self, mocker):
        mock_supabase = mocker.patch("app.tools.supabase")
        mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        
        req_id = str(uuid.uuid4())
        res_str = process_refund(order_id=999, client_request_id=req_id)
        res = json.loads(res_str)
        assert "error" in res
        assert "not found" in res["error"]
        mock_supabase.rpc.assert_not_called()

    def test_mutation_requires_client_request_id(self):
        res1 = json.loads(cancel_order(order_id=1, client_request_id=None))
        assert "error" in res1
        assert "client_request_id is required" in res1["error"]

        res2 = json.loads(process_refund(order_id=1, client_request_id=None))
        assert "error" in res2
        assert "client_request_id is required" in res2["error"]


