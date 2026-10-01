import json
import os
import pytest
import uuid
import psycopg2
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD")
if not TEST_DB_PASSWORD:
    raise ValueError("TEST_DB_PASSWORD environment variable is required to run real idempotency integration tests. Provide it in the local Docker environment.")

conn_string = f"dbname='postgres' user='postgres' host='localhost' port='5433' password='{TEST_DB_PASSWORD}'"

def exec_rpc(operation_type, canonical_target, payload, customer_id=1, client_request_id=None):
    if not client_request_id:
        client_request_id = str(uuid.uuid4())
    import hashlib
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        # In real code we use supabase.rpc, here we call it directly via Postgres
        cur.execute(
            """
            SELECT execute_idempotent_operation(
                %s, %s, %s, %s, %s, %s::jsonb
            );
            """,
            (customer_id, client_request_id, operation_type, str(canonical_target), payload_hash, json.dumps(payload))
        )
        res = cur.fetchone()[0]
        cur.close()
        return res
    finally:
        conn.close()

def get_table_count(table_name):
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {table_name};")
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count

def get_ticket_description(ticket_id):
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT description FROM tickets WHERE id = %s;", (ticket_id,))
    desc = cur.fetchone()[0]
    cur.close()
    conn.close()
    return desc

def get_order_status(order_id):
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT status FROM orders WHERE id = %s;", (order_id,))
    status = cur.fetchone()[0]
    cur.close()
    conn.close()
    return status

def execute_concurrently(func, *args, **kwargs):
    concurrency = 3
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(func, *args, **kwargs) for _ in range(concurrency)]
        for future in as_completed(futures):
            results.append(future.result())
    return results

class TestRealDatabaseIdempotency:
    
    def test_create_ticket_concurrency(self):
        start_count = get_table_count("tickets")
        req_id = str(uuid.uuid4())
        payload = {
            "subject": "Race Condition Test",
            "description": "Will it create one?",
            "type": "inquiry",
            "status": "open",
            "priority": "low",
            "channel": "chat"
        }
        
        # Concurrent execution
        results = execute_concurrently(exec_rpc, "create_ticket", "new", payload, client_request_id=req_id)
        
        # Verify exactly one ticket was created
        end_count = get_table_count("tickets")
        assert end_count == start_count + 1
        
        # Verify all concurrent threads got the same success result (the cached terminal result)
        for res in results:
            assert res.get("status") == "success"
            assert "ticket_id" in res
            # They should all share the identical ticket_id
            assert res["ticket_id"] == results[0]["ticket_id"]
            
        # Conflicting retry
        conflict_payload = payload.copy()
        conflict_payload["subject"] = "Different Subject"
        res_conflict = exec_rpc("create_ticket", "new", conflict_payload, client_request_id=req_id)
        assert res_conflict.get("error") == "IdempotencyConflict"
        
        # Ensure still no new ticket created
        assert get_table_count("tickets") == end_count

    def test_update_ticket_append_concurrency(self):
        # 1. Create a ticket
        payload = {
            "subject": "Append Test",
            "description": "Original",
            "type": "inquiry",
            "status": "open",
            "priority": "low",
            "channel": "chat"
        }
        res_create = exec_rpc("create_ticket", "new", payload)
        ticket_id = res_create["ticket_id"]
        
        # 2. Append concurrently
        req_id = str(uuid.uuid4())
        append_payload = {"description_append": "Additional info"}
        results = execute_concurrently(exec_rpc, "update_ticket", str(ticket_id), append_payload, client_request_id=req_id)
        
        # 3. Verify exactly ONE append happened
        desc = get_ticket_description(ticket_id)
        append_count = desc.count("Additional info")
        assert append_count == 1, f"Expected 1 append, found {append_count} in: {desc}"
        
        for res in results:
            assert res.get("status") == "success"

    def test_cancel_order_idempotency(self):
        # Setup active order manually
        conn = psycopg2.connect(conn_string)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("INSERT INTO orders (id, customer_id, status) VALUES (999, 1, 'active') ON CONFLICT (id) DO UPDATE SET status = 'active';")
        cur.close()
        conn.close()
        
        req_id = str(uuid.uuid4())
        payload = {"status": "cancelled"}
        
        results = execute_concurrently(exec_rpc, "cancel_order", "999", payload, client_request_id=req_id)
        
        # Verify
        assert get_order_status(999) == "cancelled"
        for res in results:
            assert res.get("status") == "success"
            
        # Verify conflicting retry
        conflict_payload = {"status": "shipped"}
        res_conflict = exec_rpc("cancel_order", "999", conflict_payload, client_request_id=req_id)
        assert res_conflict.get("error") == "IdempotencyConflict"

    def test_process_refund_idempotency(self):
        # Setup cancelled order manually
        conn = psycopg2.connect(conn_string)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("INSERT INTO orders (id, customer_id, status) VALUES (888, 1, 'cancelled') ON CONFLICT (id) DO UPDATE SET status = 'cancelled';")
        cur.close()
        conn.close()
        
        req_id = str(uuid.uuid4())
        payload = {"status": "refunded"}
        
        results = execute_concurrently(exec_rpc, "process_refund", "888", payload, client_request_id=req_id)
        
        # Verify
        assert get_order_status(888) == "refunded"
        for res in results:
            assert res.get("status") == "success"

    def test_customer_isolation(self):
        # Customer 1 request
        req_id = str(uuid.uuid4())
        payload = {
            "subject": "Isolation Test",
            "description": "Will it isolate?",
            "type": "inquiry",
            "status": "open",
            "priority": "low",
            "channel": "chat"
        }
        res1 = exec_rpc("create_ticket", "new", payload, customer_id=1, client_request_id=req_id)
        
        # Customer 2 request with same req_id
        res2 = exec_rpc("create_ticket", "new", payload, customer_id=2, client_request_id=req_id)
        
        assert res1.get("status") == "success"
        assert res2.get("status") == "success"
        
        # They should be distinct tickets
        assert res1["ticket_id"] != res2["ticket_id"]
