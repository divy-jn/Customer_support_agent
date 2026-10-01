import json
import os
import pytest
import uuid
import psycopg2
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD")
if not TEST_DB_PASSWORD:
    raise ValueError("TEST_DB_PASSWORD environment variable is required to run real integration tests. Provide it in the local Docker environment.")

conn_string = f"dbname='postgres' user='postgres' host='localhost' port='5433' password='{TEST_DB_PASSWORD}'"

def exec_enqueue(logical_identity_hash, source_event_id, notification_type, recipient_identity, recipient_address, payload):
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT enqueue_outbox_event(
                %s, %s, %s, %s, %s, %s::jsonb
            );
            """,
            (
                logical_identity_hash,
                source_event_id,
                notification_type,
                recipient_identity,
                recipient_address,
                json.dumps(payload)
            )
        )
        res = cur.fetchone()[0]
        cur.close()
        return res
    finally:
        conn.close()

def get_outbox_count():
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM outbox_events;")
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count

def execute_concurrently(func, *args, **kwargs):
    concurrency = 3
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(func, *args, **kwargs) for _ in range(concurrency)]
        for future in as_completed(futures):
            results.append(future.result())
    return results

class TestOutboxSchemaAndEnqueue:
    def test_first_enqueue_succeeds(self):
        # 1. first enqueue succeeds
        start_count = get_outbox_count()
        hash_id = str(uuid.uuid4())
        
        res = exec_enqueue(
            logical_identity_hash=hash_id,
            source_event_id="customer:1|req:123",
            notification_type="TICKET_CREATED",
            recipient_identity="customer:1",
            recipient_address="test@example.com",
            payload={"ticket_id": 999}
        )
        
        assert res["status"] == "enqueued"
        assert "event_id" in res
        assert get_outbox_count() == start_count + 1

    def test_exact_duplicate_enqueue_returns_existing(self):
        # 2. exact duplicate enqueue returns the existing logical notification
        hash_id = str(uuid.uuid4())
        
        res1 = exec_enqueue(
            logical_identity_hash=hash_id,
            source_event_id="customer:1|req:456",
            notification_type="TICKET_CREATED",
            recipient_identity="customer:1",
            recipient_address="test@example.com",
            payload={"ticket_id": 1000}
        )
        
        res2 = exec_enqueue(
            logical_identity_hash=hash_id,
            source_event_id="customer:1|req:456",
            notification_type="TICKET_CREATED",
            recipient_identity="customer:1",
            recipient_address="test@example.com",
            payload={"ticket_id": 1000}
        )
        
        assert res1["status"] == "enqueued"
        assert res2["status"] == "duplicate"
        assert res1["event_id"] == res2["event_id"]

    def test_concurrent_duplicate_enqueue(self):
        # 3. concurrent duplicate enqueue creates exactly one row
        start_count = get_outbox_count()
        hash_id = str(uuid.uuid4())
        
        results = execute_concurrently(
            exec_enqueue,
            logical_identity_hash=hash_id,
            source_event_id="customer:1|req:789",
            notification_type="TICKET_CREATED",
            recipient_identity="customer:1",
            recipient_address="test@example.com",
            payload={"ticket_id": 1001}
        )
        
        enqueued_count = sum(1 for r in results if r["status"] == "enqueued")
        duplicate_count = sum(1 for r in results if r["status"] == "duplicate")
        
        assert enqueued_count == 1
        assert duplicate_count == len(results) - 1
        assert get_outbox_count() == start_count + 1

        event_ids = set(r["event_id"] for r in results)
        assert len(event_ids) == 1

    def test_same_source_event_different_recipient(self):
        # 4. same source event + different recipient creates two rows
        start_count = get_outbox_count()
        source_event_id = "customer:2|req:111"
        
        res1 = exec_enqueue(
            logical_identity_hash=str(uuid.uuid4()),  # different hashes because different recipients
            source_event_id=source_event_id,
            notification_type="ESCALATION_TEAM",
            recipient_identity="support-team:tier1",
            recipient_address="tier1@example.com",
            payload={"ticket_id": 2000}
        )
        
        res2 = exec_enqueue(
            logical_identity_hash=str(uuid.uuid4()),
            source_event_id=source_event_id,
            notification_type="ESCALATION_TEAM",
            recipient_identity="support-team:tier2",
            recipient_address="tier2@example.com",
            payload={"ticket_id": 2000}
        )
        
        assert res1["status"] == "enqueued"
        assert res2["status"] == "enqueued"
        assert get_outbox_count() == start_count + 2

    def test_same_source_event_different_type(self):
        # 5. same source event + different notification type creates two rows
        start_count = get_outbox_count()
        source_event_id = "customer:3|req:222"
        
        res1 = exec_enqueue(
            logical_identity_hash=str(uuid.uuid4()),
            source_event_id=source_event_id,
            notification_type="TICKET_CREATED",
            recipient_identity="customer:3",
            recipient_address="c3@example.com",
            payload={"ticket_id": 3000}
        )
        
        res2 = exec_enqueue(
            logical_identity_hash=str(uuid.uuid4()),
            source_event_id=source_event_id,
            notification_type="TICKET_RESOLVED",
            recipient_identity="customer:3",
            recipient_address="c3@example.com",
            payload={"ticket_id": 3000}
        )
        
        assert res1["status"] == "enqueued"
        assert res2["status"] == "enqueued"
        assert get_outbox_count() == start_count + 2

    def test_different_source_event_distinct_rows(self):
        # 6. different source event creates distinct rows
        start_count = get_outbox_count()
        
        res1 = exec_enqueue(
            logical_identity_hash=str(uuid.uuid4()),
            source_event_id="customer:4|req:A",
            notification_type="TICKET_CREATED",
            recipient_identity="customer:4",
            recipient_address="c4@example.com",
            payload={"ticket_id": 4000}
        )
        
        res2 = exec_enqueue(
            logical_identity_hash=str(uuid.uuid4()),
            source_event_id="customer:4|req:B",
            notification_type="TICKET_CREATED",
            recipient_identity="customer:4",
            recipient_address="c4@example.com",
            payload={"ticket_id": 4001}
        )
        
        assert res1["status"] == "enqueued"
        assert res2["status"] == "enqueued"
        assert get_outbox_count() == start_count + 2

    def test_recipient_address_changes(self):
        # 7. recipient address changes but recipient_identity stays same => same logical identity
        hash_id = str(uuid.uuid4())
        source_event_id = "customer:5|req:333"
        
        res1 = exec_enqueue(
            logical_identity_hash=hash_id,
            source_event_id=source_event_id,
            notification_type="ORDER_UPDATED",
            recipient_identity="customer:5",
            recipient_address="old@example.com",
            payload={"order_id": 5000}
        )
        
        # If the address changed in the system, but the logical intent is the same:
        res2 = exec_enqueue(
            logical_identity_hash=hash_id,
            source_event_id=source_event_id,
            notification_type="ORDER_UPDATED",
            recipient_identity="customer:5",
            recipient_address="new@example.com",
            payload={"order_id": 5000}
        )
        
        assert res1["status"] == "enqueued"
        assert res2["status"] == "duplicate"
        assert res1["event_id"] == res2["event_id"]

    def test_customer_isolation_preserved(self):
        # 8. customer isolation is preserved
        hash1 = str(uuid.uuid4())
        hash2 = str(uuid.uuid4())
        
        res1 = exec_enqueue(
            logical_identity_hash=hash1,
            source_event_id="customer:6|req:XXX",
            notification_type="TICKET_CREATED",
            recipient_identity="customer:6",
            recipient_address="c6@example.com",
            payload={"ticket_id": 6000}
        )
        
        res2 = exec_enqueue(
            logical_identity_hash=hash2,
            source_event_id="customer:7|req:XXX",
            notification_type="TICKET_CREATED",
            recipient_identity="customer:7",
            recipient_address="c7@example.com",
            payload={"ticket_id": 7000}
        )
        
        assert res1["status"] == "enqueued"
        assert res2["status"] == "enqueued"
        assert res1["event_id"] != res2["event_id"]

    def test_invalid_status_transition_data(self):
        # 9. invalid status transition data cannot be inserted
        conn = psycopg2.connect(conn_string)
        conn.autocommit = True
        cur = conn.cursor()
        
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute("""
                INSERT INTO outbox_events (
                    logical_identity_hash, source_event_id, notification_type, 
                    recipient_identity, recipient_address, payload, status
                ) VALUES (
                    %s, 'test', 'type', 'recip', 'addr', '{}', 'INVALID_STATUS'
                )
            """, (str(uuid.uuid4()),))
        
        cur.close()
        conn.close()

    def test_required_payload_fields(self):
        # 10. required payload fields are enforced
        conn = psycopg2.connect(conn_string)
        conn.autocommit = True
        cur = conn.cursor()
        
        with pytest.raises(psycopg2.errors.NotNullViolation):
            cur.execute("""
                INSERT INTO outbox_events (
                    logical_identity_hash, source_event_id, notification_type, 
                    recipient_identity, recipient_address
                    -- missing payload
                ) VALUES (
                    %s, 'test', 'type', 'recip', 'addr'
                )
            """, (str(uuid.uuid4()),))
            
        cur.close()
        conn.close()

    def test_outbox_row_uuid_differs_from_logical(self):
        # 11. outbox row UUID differs from logical notification identity
        hash_id = "logical_hash_not_a_uuid_" + str(uuid.uuid4())
        
        res = exec_enqueue(
            logical_identity_hash=hash_id,
            source_event_id="customer:8|req:444",
            notification_type="CUSTOM_EMAIL",
            recipient_identity="customer:8",
            recipient_address="c8@example.com",
            payload={"ticket_id": 8000}
        )
        
        assert res["status"] == "enqueued"
        event_id = res["event_id"]
        
        assert event_id != hash_id
        # event_id must be a valid UUID
        uuid.UUID(event_id)

    def test_transaction_rollback_removes_outbox_row(self):
        # 12. transaction rollback removes the outbox row when the enclosing transaction rolls back
        hash_id = str(uuid.uuid4())
        
        conn = psycopg2.connect(conn_string)
        conn.autocommit = False
        cur = conn.cursor()
        
        # Enqueue within transaction
        cur.execute(
            """
            SELECT enqueue_outbox_event(
                %s, %s, %s, %s, %s, %s::jsonb
            );
            """,
            (
                hash_id,
                "customer:9|req:555",
                "TICKET_CREATED",
                "customer:9",
                "c9@example.com",
                '{"ticket_id": 9000}'
            )
        )
        
        # Rollback
        conn.rollback()
        
        # Check that it's not there
        cur.execute("SELECT COUNT(*) FROM outbox_events WHERE logical_identity_hash = %s", (hash_id,))
        count = cur.fetchone()[0]
        assert count == 0
        
        cur.close()
        conn.close()


class TestBusinessMutationAtomicity:
    def test_1_rollback_enqueue_only(self):
        # Test 1: BEGIN enqueue_outbox_event() ROLLBACK -> outbox row absent
        hash_id = str(uuid.uuid4())
        
        conn = psycopg2.connect(conn_string)
        conn.autocommit = False
        cur = conn.cursor()
        
        cur.execute(
            "SELECT enqueue_outbox_event(%s, %s, %s, %s, %s, %s::jsonb);",
            (hash_id, "src1", "TICKET_CREATED", "c1", "a@b.com", '{}')
        )
        conn.rollback()
        
        cur.execute("SELECT COUNT(*) FROM outbox_events WHERE logical_identity_hash = %s", (hash_id,))
        count = cur.fetchone()[0]
        assert count == 0
        
        cur.close()
        conn.close()

    def test_2_rollback_business_and_enqueue(self):
        # Test 2: BEGIN business mutation, enqueue_outbox_event() ROLLBACK -> BOTH absent
        req_id = str(uuid.uuid4())
        hash_id = str(uuid.uuid4())
        
        conn = psycopg2.connect(conn_string)
        conn.autocommit = False
        cur = conn.cursor()
        
        # Business mutation
        import json
        import hashlib
        payload = {"subject": "Test 2 Rollback", "description": "Roll me back", "type": "inquiry", "status": "open", "priority": "low", "channel": "chat"}
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        
        cur.execute(
            "SELECT execute_idempotent_operation(%s, %s, %s, %s, %s, %s::jsonb);",
            (1, req_id, "create_ticket", "new", payload_hash, json.dumps(payload))
        )
        res = cur.fetchone()[0]
        ticket_id = res.get("ticket_id")
        
        # Enqueue
        cur.execute(
            "SELECT enqueue_outbox_event(%s, %s, %s, %s, %s, %s::jsonb);",
            (hash_id, f"customer:1|req:{req_id}", "TICKET_CREATED", "customer:1", "c1@example.com", json.dumps({"ticket_id": ticket_id}))
        )
        
        conn.rollback()
        
        # Verify both are absent
        conn.autocommit = True
        cur.execute("SELECT COUNT(*) FROM tickets WHERE id = %s", (ticket_id,))
        ticket_count = cur.fetchone()[0]
        assert ticket_count == 0
        
        cur.execute("SELECT COUNT(*) FROM outbox_events WHERE logical_identity_hash = %s", (hash_id,))
        outbox_count = cur.fetchone()[0]
        assert outbox_count == 0
        
        cur.close()
        conn.close()

    def test_3_commit_business_and_enqueue(self):
        # Test 3: BEGIN business mutation, enqueue_outbox_event() COMMIT -> BOTH exist
        req_id = str(uuid.uuid4())
        hash_id = str(uuid.uuid4())
        
        conn = psycopg2.connect(conn_string)
        conn.autocommit = False
        cur = conn.cursor()
        
        # Business mutation
        import json
        import hashlib
        payload = {"subject": "Test 3 Commit", "description": "Commit me", "type": "inquiry", "status": "open", "priority": "low", "channel": "chat"}
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        
        cur.execute(
            "SELECT execute_idempotent_operation(%s, %s, %s, %s, %s, %s::jsonb);",
            (1, req_id, "create_ticket", "new", payload_hash, json.dumps(payload))
        )
        res = cur.fetchone()[0]
        ticket_id = res.get("ticket_id")
        
        # Enqueue
        cur.execute(
            "SELECT enqueue_outbox_event(%s, %s, %s, %s, %s, %s::jsonb);",
            (hash_id, f"customer:1|req:{req_id}", "TICKET_CREATED", "customer:1", "c1@example.com", json.dumps({"ticket_id": ticket_id}))
        )
        
        conn.commit()
        
        # Verify both exist
        conn.autocommit = True
        cur.execute("SELECT COUNT(*) FROM tickets WHERE id = %s", (ticket_id,))
        ticket_count = cur.fetchone()[0]
        assert ticket_count == 1
        
        cur.execute("SELECT COUNT(*) FROM outbox_events WHERE logical_identity_hash = %s", (hash_id,))
        outbox_count = cur.fetchone()[0]
        assert outbox_count == 1
        
        cur.close()
        conn.close()

    def test_4_failure_between_business_and_enqueue(self):
        # Test 4: failure between business mutation and enqueue -> prove both roll back atomically
        req_id = str(uuid.uuid4())
        hash_id = str(uuid.uuid4())
        
        conn = psycopg2.connect(conn_string)
        conn.autocommit = False
        cur = conn.cursor()
        
        # Business mutation
        import json
        import hashlib
        payload = {"subject": "Test 4 Failure", "description": "Fail between", "type": "inquiry", "status": "open", "priority": "low", "channel": "chat"}
        payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        
        ticket_id = None
        try:
            cur.execute(
                "SELECT execute_idempotent_operation(%s, %s, %s, %s, %s, %s::jsonb);",
                (1, req_id, "create_ticket", "new", payload_hash, json.dumps(payload))
            )
            res = cur.fetchone()[0]
            ticket_id = res.get("ticket_id")
            
            # Simulate failure (e.g. division by zero) before enqueue
            cur.execute("SELECT 1 / 0;")
            
            cur.execute(
                "SELECT enqueue_outbox_event(%s, %s, %s, %s, %s, %s::jsonb);",
                (hash_id, f"customer:1|req:{req_id}", "TICKET_CREATED", "customer:1", "c1@example.com", json.dumps({"ticket_id": ticket_id}))
            )
            conn.commit()
        except Exception:
            conn.rollback()
        
        # Verify both absent
        conn.autocommit = True
        cur.execute("SELECT COUNT(*) FROM tickets WHERE id = %s", (ticket_id,))
        ticket_count = cur.fetchone()[0]
        assert ticket_count == 0
        
        cur.execute("SELECT COUNT(*) FROM outbox_events WHERE logical_identity_hash = %s", (hash_id,))
        outbox_count = cur.fetchone()[0]
        assert outbox_count == 0
        
        cur.close()
        conn.close()
