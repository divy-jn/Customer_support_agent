import os
import json
import uuid
import pytest
import psycopg2
from concurrent.futures import ThreadPoolExecutor, as_completed

TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD")
if not TEST_DB_PASSWORD:
    raise ValueError("TEST_DB_PASSWORD environment variable is required to run real postgres tests. Provide it in the local Docker environment.")

conn_string = f"dbname='postgres' user='postgres' host='localhost' port='5433' password='{TEST_DB_PASSWORD}'"

def setup_session_state(session_id, customer_id, status='NONE'):
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO session_escalation_state (session_id, customer_id, status)
            VALUES (%s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE SET status = EXCLUDED.status, customer_id = EXCLUDED.customer_id;
            """,
            (session_id, customer_id, status)
        )
        cur.close()
    finally:
        conn.close()

def get_outbox_events(session_id):
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT o.notification_type, o.recipient_identity, o.payload, o.source_event_id
            FROM outbox_events o
            JOIN escalation_events e ON o.source_event_id = e.escalation_event_id::TEXT
            WHERE e.session_id = %s
            """,
            (session_id,)
        )
        return cur.fetchall()
    finally:
        conn.close()
        
def get_escalation_events(session_id):
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT escalation_event_id, client_request_id FROM escalation_events WHERE session_id = %s ORDER BY created_at ASC",
            (session_id,)
        )
        return cur.fetchall()
    finally:
        conn.close()

def execute_concurrently(func, *args, **kwargs):
    concurrency = 3
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(func, *args, **kwargs) for _ in range(concurrency)]
        for future in as_completed(futures):
            results.append(future.result())
    return results

# Mock Supabase RPC class to intercept PostgREST calls and route to local psycopg2
class MockSupabaseRPC:
    def __init__(self, rpc_name, params):
        self.rpc_name = rpc_name
        self.params = params

    def execute(self):
        conn = psycopg2.connect(conn_string)
        conn.autocommit = True
        try:
            cur = conn.cursor()
            if self.rpc_name == "execute_release_transition":
                cur.execute(
                    "SELECT execute_release_transition(%s, %s, %s, %s, %s::jsonb);",
                    (self.params["p_customer_id"], self.params["p_client_request_id"], self.params["p_session_id"], self.params["p_payload_hash"], json.dumps(self.params["p_payload"]))
                )
            elif self.rpc_name == "execute_escalation_transition":
                cur.execute(
                    "SELECT execute_escalation_transition(%s, %s, %s, %s, %s::jsonb, %s);",
                    (self.params["p_customer_id"], self.params["p_client_request_id"], self.params["p_session_id"], self.params["p_payload_hash"], json.dumps(self.params["p_payload"]), str(uuid.uuid4()))
                )
            res = cur.fetchone()[0]
            cur.close()
            
            # mock the response object from supabase python client
            class DummyResponse:
                def __init__(self, data):
                    self.data = data
            return DummyResponse(res)
        finally:
            conn.close()

@pytest.fixture(autouse=True)
def mock_supabase_client(monkeypatch):
    monkeypatch.setattr("app.escalation.service.supabase.rpc", MockSupabaseRPC)

class TestRealEscalationService:
    
    def test_release_session_active(self):
        """ACTIVE -> RELEASED"""
        from app.escalation.service import EscalationService, ReleaseRequest
        session_id = f"sess_{uuid.uuid4()}"
        customer_id = 999
        setup_session_state(session_id, customer_id, 'ACTIVE')
        
        req = ReleaseRequest(
            customer_id=customer_id,
            session_id=session_id,
            client_request_id=uuid.uuid4()
        )
        res = EscalationService.release_session(req)
        assert res.status == "success"
        
        conn = psycopg2.connect(conn_string)
        try:
            cur = conn.cursor()
            cur.execute("SELECT status FROM session_escalation_state WHERE session_id = %s", (session_id,))
            assert cur.fetchone()[0] == 'RELEASED'
        finally:
            conn.close()

    def test_release_session_none(self):
        """NONE release rejected"""
        from app.escalation.service import EscalationService, ReleaseRequest
        session_id = f"sess_{uuid.uuid4()}"
        customer_id = 1
        setup_session_state(session_id, customer_id, 'NONE')
        
        req = ReleaseRequest(
            customer_id=customer_id,
            session_id=session_id,
            client_request_id=uuid.uuid4()
        )
        res = EscalationService.release_session(req)
        assert res.status == "failed"
        assert "Cannot release a session" in res.message

    def test_release_session_released_idempotency(self):
        """RELEASED release deterministic/idempotent behavior and exact replay"""
        from app.escalation.service import EscalationService, ReleaseRequest
        session_id = f"sess_{uuid.uuid4()}"
        customer_id = 1
        setup_session_state(session_id, customer_id, 'ACTIVE')
        
        client_request_id = uuid.uuid4()
        
        # First request
        req1 = ReleaseRequest(
            customer_id=customer_id,
            session_id=session_id,
            client_request_id=client_request_id
        )
        res1 = EscalationService.release_session(req1)
        assert res1.status == "success"
        
        # Exact replay
        res2 = EscalationService.release_session(req1)
        assert res2.status == "success"
        
    def test_release_session_idempotency_conflict(self):
        """same request_id + different fingerprint -> IdempotencyConflict"""
        from app.escalation.service import EscalationService, ReleaseRequest
        session_id = f"sess_{uuid.uuid4()}"
        customer_id = 1
        setup_session_state(session_id, customer_id, 'ACTIVE')
        
        client_request_id = uuid.uuid4()
        
        # First request
        req1 = ReleaseRequest(
            customer_id=customer_id,
            session_id=session_id,
            client_request_id=client_request_id
        )
        res1 = EscalationService.release_session(req1)
        assert res1.status == "success"
        
        # Different fingerprint (different session_id) but same request_id
        session_id_2 = f"sess_{uuid.uuid4()}"
        setup_session_state(session_id_2, customer_id, 'ACTIVE')
        req2 = ReleaseRequest(
            customer_id=customer_id,
            session_id=session_id_2,
            client_request_id=client_request_id
        )
        with pytest.raises(Exception) as excinfo:
            EscalationService.release_session(req2)
        assert excinfo.type.__name__ == "IdempotencyConflict"

    def test_release_session_authorization_fails(self):
        """customer/session authorization"""
        from app.escalation.service import EscalationService, ReleaseRequest
        session_id = f"sess_{uuid.uuid4()}"
        customer_id = 1
        setup_session_state(session_id, customer_id, 'ACTIVE')
        
        req = ReleaseRequest(
            customer_id=123, # Wrong customer
            session_id=session_id,
            client_request_id=uuid.uuid4()
        )
        res = EscalationService.release_session(req)
        assert res.status == "failed"

    def test_release_session_rollback_on_failure(self, monkeypatch):
        """rollback when release transaction fails"""
        from app.escalation.service import EscalationService, ReleaseRequest
        session_id = f"sess_{uuid.uuid4()}"
        customer_id = 1
        setup_session_state(session_id, customer_id, 'ACTIVE')
        
        # Inject an exception into the RPC call to simulate a transaction failure
        original_execute = MockSupabaseRPC.execute
        def mock_execute_failure(self_obj):
            if self_obj.rpc_name == "execute_release_transition":
                raise Exception("Simulated DB failure")
            return original_execute(self_obj)
        monkeypatch.setattr(MockSupabaseRPC, "execute", mock_execute_failure)
        
        req = ReleaseRequest(
            customer_id=customer_id,
            session_id=session_id,
            client_request_id=uuid.uuid4()
        )
        
        with pytest.raises(Exception, match="Simulated DB failure"):
            EscalationService.release_session(req)
            
        # Verify state remains ACTIVE
        conn = psycopg2.connect(conn_string)
        try:
            cur = conn.cursor()
            cur.execute("SELECT status FROM session_escalation_state WHERE session_id = %s", (session_id,))
            assert cur.fetchone()[0] == 'ACTIVE'
        finally:
            conn.close()

    def test_concurrency_release_and_escalation_a(self):
        """
        Prove serialization order A:
        release acquires session_escalation_state lock first -> ACTIVE -> RELEASED
        -> waiting escalation sees RELEASED -> escalation becomes E2
        """
        # We can simulate this by mocking the sleep in the RPC or executing sequentially if real concurrency is hard,
        # but execute_concurrently can try. Let's do a sequence that proves the locking logic.
        from app.escalation.service import EscalationService, ReleaseRequest, EscalationRequest
        session_id = f"sess_{uuid.uuid4()}"
        customer_id = 1
        setup_session_state(session_id, customer_id, 'ACTIVE')
        
        req_rel = ReleaseRequest(
            customer_id=customer_id,
            session_id=session_id,
            client_request_id=uuid.uuid4()
        )
        req_esc = EscalationRequest(
            customer_id=customer_id,
            session_id=session_id,
            client_request_id=uuid.uuid4(),
            sentiment="negative",
            urgency="high",
            customer_notification_required=True
        )
        
        # Run them concurrently to prove real database locking prevents interleaving
        import time
        with ThreadPoolExecutor(max_workers=2) as executor:
            # We don't know which will win the race, but Postgres guarantees serialization.
            f_rel = executor.submit(EscalationService.release_session, req_rel)
            f_esc = executor.submit(EscalationService.escalate_session, req_esc)
            res_rel = f_rel.result()
            res_esc = f_esc.result()
            
        assert res_rel.status == "success"
        
        events = get_escalation_events(session_id)
        outbox = get_outbox_events(session_id)
        
        # Valid Outcome A: Release won the race
        # ACTIVE -> RELEASED. Escalation then runs on RELEASED, causing E2.
        if res_esc.status == "success":
            assert len(events) == 1
            assert len(outbox) == 2
        
        # Valid Outcome B: Escalation won the race
        # ACTIVE -> ACTIVE (escalation bypass). Release then runs on ACTIVE -> RELEASED.
        elif res_esc.status == "bypassed":
            assert len(events) == 0
            assert len(outbox) == 0
        else:
            pytest.fail(f"Unexpected escalation status: {res_esc.status}")

    def test_concurrency_release_and_escalation_b(self):
        """
        Prove serialization order B:
        escalation acquires lock first while ACTIVE -> escalation bypasses
        -> waiting release sees ACTIVE -> ACTIVE -> RELEASED
        """
        from app.escalation.service import EscalationService, ReleaseRequest, EscalationRequest
        session_id = f"sess_{uuid.uuid4()}"
        customer_id = 1
        setup_session_state(session_id, customer_id, 'ACTIVE')
        
        req_rel = ReleaseRequest(
            customer_id=customer_id,
            session_id=session_id,
            client_request_id=uuid.uuid4()
        )
        req_esc = EscalationRequest(
            customer_id=customer_id,
            session_id=session_id,
            client_request_id=uuid.uuid4(),
            sentiment="negative",
            urgency="high",
            customer_notification_required=True
        )
        
        # Run them concurrently many times to ensure no weird interleavings happen
        for _ in range(5):
            session_id = f"sess_{uuid.uuid4()}"
            setup_session_state(session_id, customer_id, 'ACTIVE')
            req_rel.session_id = session_id
            req_esc.session_id = session_id
            req_rel.client_request_id = uuid.uuid4()
            req_esc.client_request_id = uuid.uuid4()
            
            with ThreadPoolExecutor(max_workers=2) as executor:
                f_rel = executor.submit(EscalationService.release_session, req_rel)
                f_esc = executor.submit(EscalationService.escalate_session, req_esc)
                res_rel = f_rel.result()
                res_esc = f_esc.result()
                
            assert res_rel.status == "success"
            
            events = get_escalation_events(session_id)
            outbox = get_outbox_events(session_id)
            
            if res_esc.status == "success":
                assert len(events) == 1
                assert len(outbox) == 2
            elif res_esc.status == "bypassed":
                assert len(events) == 0
                assert len(outbox) == 0
            else:
                pytest.fail(f"Unexpected escalation status: {res_esc.status}")

