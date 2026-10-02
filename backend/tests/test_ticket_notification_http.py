import uuid
from fastapi.testclient import TestClient
import json
import uuid
import os
os.environ["AGENT_SECRET_TOKEN"] = "agent_secret_token"
from app.main import app
from tests.test_ticket_notification_transaction import get_outbox_events_for_source

import psycopg2
import os

TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD", "postgres")
conn_string = f"dbname='postgres' user='postgres' host='localhost' port='5433' password='{TEST_DB_PASSWORD}'"

def mock_supabase_rpc_to_real_db(rpc_name, params):
    class MockResponse:
        def execute(self):
            conn = psycopg2.connect(conn_string)
            conn.autocommit = True
            try:
                cur = conn.cursor()
                if rpc_name == "execute_ticket_mutation_with_outbox":
                    cur.execute(
                        """
                        SELECT execute_ticket_mutation_with_outbox(
                            %s, %s, %s, %s, %s, %s::jsonb,
                            %s, %s, %s, %s, %s::jsonb
                        );
                        """,
                        (
                            params.get("p_customer_id"),
                            params.get("p_client_request_id"),
                            params.get("p_operation_type"),
                            params.get("p_canonical_target"),
                            params.get("p_payload_hash"),
                            json.dumps(params.get("p_payload")),
                            params.get("p_outbox_logical_identity_hash"),
                            params.get("p_outbox_source_event_id"),
                            params.get("p_outbox_notification_type"),
                            params.get("p_outbox_recipient_identity"),
                            json.dumps(params.get("p_outbox_payload_template")) if params.get("p_outbox_payload_template") else None
                        )
                    )
                    res = cur.fetchone()[0]
                    cur.close()
                    
                    class DataResponse:
                        def __init__(self, data):
                            self.data = data
                    return DataResponse(res)
                return None
            finally:
                conn.close()
    return MockResponse()

class TestTicketNotificationHTTP:
    def setup_method(self):
        from app.config import settings
        self.original_token = settings.agent_secret
        settings.agent_secret = "agent_secret_token"
        self.client = TestClient(app)

    def teardown_method(self):
        from app.config import settings
        settings.agent_secret = self.original_token

    def test_update_ticket_http_resolution(self):
        from unittest.mock import patch
        with patch('app.tools.supabase.rpc', side_effect=mock_supabase_rpc_to_real_db):
            
            # Setup: Create a ticket first in DB so we can resolve it
            req_id = str(uuid.uuid4())
            payload = {
                "subject": "HTTP Setup",
                "description": "To be closed",
                "type": "inquiry",
                "status": "open",
                "priority": "low",
                "channel": "chat"
            }
            create_params = {
                "p_customer_id": 1,
                "p_client_request_id": req_id,
                "p_operation_type": "create_ticket",
                "p_canonical_target": "new",
                "p_payload_hash": "hash_create",
                "p_payload": payload,
                "p_outbox_logical_identity_hash": None,
                "p_outbox_source_event_id": None,
                "p_outbox_notification_type": None,
                "p_outbox_recipient_identity": None,
                "p_outbox_payload_template": None
            }
            res = mock_supabase_rpc_to_real_db("execute_ticket_mutation_with_outbox", create_params).execute().data
            ticket_id = res["ticket_id"]
            
            headers = {
                "X-Agent-Token": "agent_secret_token",
                "X-Client-Request-Id": str(uuid.uuid4())
            }
            update_payload = {
                "status": "closed",
                "resolution": "Fixed via HTTP"
            }
            response = self.client.patch(f"/api/v1/tickets/{ticket_id}", json=update_payload, headers=headers)
            
            assert response.status_code == 200, response.text
            
            # Verify outbox event created in REAL DB
            events = get_outbox_events_for_source(f"customer:1|req:{headers['X-Client-Request-Id']}")
            assert len(events) == 1
            assert events[0][1] == "PENDING"

    def test_missing_client_request_id(self):
        headers = {
            "X-Agent-Token": "agent_secret_token",
        }
        response = self.client.patch("/api/v1/tickets/1", json={"status": "closed"}, headers=headers)
        assert response.status_code == 422 # FastAPI validation error for missing header
        assert "X-Client-Request-Id" in response.text
        
    def test_malformed_client_request_id(self):
        headers = {
            "X-Agent-Token": "agent_secret_token",
            "X-Client-Request-Id": "not-a-uuid"
        }
        response = self.client.patch("/api/v1/tickets/1", json={"status": "closed"}, headers=headers)
        assert response.status_code == 422 # FastAPI validation error for malformed UUID
        assert "X-Client-Request-Id" in response.text
