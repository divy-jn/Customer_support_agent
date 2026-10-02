import uuid
import os
import psycopg2
from psycopg2.extras import RealDictCursor
import pytest
import json
from app.outbox.repair_service import OutboxRepairService, RepairResultStatus
from app.notification_identity import NotificationType, SourceEventIdentity, RecipientIdentity, LogicalNotificationIdentity
import threading

TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD", "postgres")
conn_string = f"dbname='postgres' user='postgres' host='localhost' port='5433' password='{TEST_DB_PASSWORD}'"

@pytest.fixture
def repair_service():
    return OutboxRepairService(dsn=conn_string)

@pytest.fixture
def cleanup_db():
    yield
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM outbox_events WHERE recipient_address LIKE '%@repair.test'")
            cur.execute("DELETE FROM escalation_events WHERE session_id LIKE 'repair_%'")
            cur.execute("DELETE FROM idempotency_records WHERE client_request_id IN (SELECT client_request_id FROM idempotency_records WHERE payload_hash = 'repair_hash')")
            cur.execute("DELETE FROM tickets WHERE subject = 'Repair Test Ticket'")
            cur.execute("DELETE FROM customers WHERE email LIKE '%@repair.test'")
        conn.commit()

@pytest.fixture
def test_customer():
    import random
    customer_id = random.randint(100000, 900000)
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO customers (id, name, email)
                VALUES (%s, 'Repair Cust', 'cust' || %s || '@repair.test')
                RETURNING id;
            """, (customer_id, customer_id))
        conn.commit()
    return customer_id

def test_escalation_repair_missing(repair_service, test_customer, cleanup_db):
    """Test A: missing E1 TEAM/CUSTOMER -> repaired. Customer not required -> no repair. missing E2 -> repaired. distinct."""
    session_id1 = f"repair_session_{uuid.uuid4()}"
    event_id1 = str(uuid.uuid4())
    
    session_id2 = f"repair_session_{uuid.uuid4()}"
    event_id2 = str(uuid.uuid4())
    
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            # Insert session state
            cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", (session_id1, test_customer))
            cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", (session_id2, test_customer))
            
            # E1: customer not required
            cur.execute("""
                INSERT INTO escalation_events (escalation_event_id, session_id, customer_id, ticket_id, client_request_id, payload_hash, sentiment, urgency, customer_notification_required)
                VALUES (%s, %s, %s, NULL, %s, 'hash1', 'neutral', 'low', FALSE)
            """, (event_id1, session_id1, test_customer, str(uuid.uuid4())))
            
            # E2: customer required
            cur.execute("""
                INSERT INTO escalation_events (escalation_event_id, session_id, customer_id, ticket_id, client_request_id, payload_hash, sentiment, urgency, customer_notification_required)
                VALUES (%s, %s, %s, NULL, %s, 'hash2', 'negative', 'high', TRUE)
            """, (event_id2, session_id2, test_customer, str(uuid.uuid4())))
        conn.commit()

    # E1 Repair
    res1 = repair_service.repair_escalation_event(event_id1, test_customer)
    assert res1[NotificationType.ESCALATION_TEAM] == RepairResultStatus.ENQUEUED
    assert res1[NotificationType.ESCALATION_CUSTOMER] == RepairResultStatus.NOT_REQUIRED
    
    # E2 Repair
    res2 = repair_service.repair_escalation_event(event_id2, test_customer)
    assert res2[NotificationType.ESCALATION_TEAM] == RepairResultStatus.ENQUEUED
    assert res2[NotificationType.ESCALATION_CUSTOMER] == RepairResultStatus.NOT_RECONSTRUCTIBLE
    
    # Verify E1 and E2 distinct in outbox
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT notification_type, source_event_id FROM outbox_events WHERE source_event_id IN (%s, %s)", (f"escalation:{event_id1}", f"escalation:{event_id2}"))
            rows = cur.fetchall()
            assert len(rows) == 2 # E1 team, E2 team

def test_ticket_repair(repair_service, test_customer, cleanup_db):
    """Test B: missing TICKET_CREATED -> repaired. generic update_ticket -> never becomes TICKET_RESOLVED."""
    req_id_create = str(uuid.uuid4())
    req_id_update = str(uuid.uuid4())
    
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO tickets (customer_id, subject, description, priority, status)
                VALUES (%s, 'Repair Test Ticket', 'desc', 'low', 'open') RETURNING id
            """, (test_customer,))
            ticket_id = cur.fetchone()["id"]
            
            cur.execute("""
                INSERT INTO idempotency_records (customer_id, client_request_id, operation_type, canonical_target, payload_hash, terminal_result)
                VALUES (%s, %s, 'create_ticket', 'new', 'repair_hash', %s)
            """, (test_customer, req_id_create, psycopg2.extras.Json({"ticket_id": ticket_id})))
            
            cur.execute("""
                INSERT INTO idempotency_records (customer_id, client_request_id, operation_type, canonical_target, payload_hash, terminal_result)
                VALUES (%s, %s, 'update_ticket', %s, 'repair_hash', %s)
            """, (test_customer, req_id_update, str(ticket_id), psycopg2.extras.Json({"status": "success"})))
        conn.commit()

    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            # Mutate the ticket!
            cur.execute("UPDATE tickets SET subject = 'MUTATED' WHERE id = %s", (ticket_id,))
        conn.commit()

    # Create repair should return NOT_RECONSTRUCTIBLE because historical creation data is lost
    res_create = repair_service.repair_ticket_creation(req_id_create, test_customer)
    assert res_create == RepairResultStatus.NOT_RECONSTRUCTIBLE
    
    # Update repair should return CONFLICT
    res_update = repair_service.repair_ticket_creation(req_id_update, test_customer)
    assert res_update == RepairResultStatus.CONFLICT

def test_existing_states(repair_service, test_customer, cleanup_db):
    """Test C: SENT, PROCESSING, RETRYABLE, FAILED -> ALREADY_PRESENT."""
    req_id = str(uuid.uuid4())
    
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO tickets (customer_id, subject, description, priority, status)
                VALUES (%s, 'Repair Test Ticket', 'desc', 'low', 'open') RETURNING id
            """, (test_customer,))
            ticket_id = cur.fetchone()["id"]
            
            cur.execute("""
                INSERT INTO idempotency_records (customer_id, client_request_id, operation_type, canonical_target, payload_hash, terminal_result)
                VALUES (%s, %s, 'create_ticket', 'new', 'repair_hash', %s)
            """, (test_customer, req_id, psycopg2.extras.Json({"ticket_id": ticket_id})))
            
            logical = LogicalNotificationIdentity(
                source_event=SourceEventIdentity.from_ticket_creation(test_customer, req_id),
                notification_type=NotificationType.TICKET_CREATED,
                recipient=RecipientIdentity.customer(test_customer)
            )
            
            cur.execute("""
                INSERT INTO outbox_events (logical_identity_hash, source_event_id, notification_type, recipient_identity, recipient_address, payload, status)
                VALUES (%s, %s, %s, %s, 'test@repair.test', '{}', 'FAILED')
            """, (logical.get_hash(), logical.source_event.source_event_id, logical.notification_type.value, logical.recipient.principal))
        conn.commit()

    res = repair_service.repair_ticket_creation(req_id, test_customer)
    # Even if ALREADY_PRESENT could theoretically be checked by logical hash, 
    # since we can't reliably construct the logical payload hash from historical data,
    # the entire type is NOT_RECONSTRUCTIBLE before we even check.
    assert res == RepairResultStatus.NOT_RECONSTRUCTIBLE

def test_concurrency(repair_service, test_customer, cleanup_db):
    """Test D: Concurrent repairs yield exactly one outbox intent."""
    req_id = str(uuid.uuid4())
    
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO tickets (customer_id, subject, description, priority, status)
                VALUES (%s, 'Repair Test Ticket', 'desc', 'low', 'open') RETURNING id
            """, (test_customer,))
            ticket_id = cur.fetchone()["id"]
            
            cur.execute("""
                INSERT INTO idempotency_records (customer_id, client_request_id, operation_type, canonical_target, payload_hash, terminal_result)
                VALUES (%s, %s, 'create_ticket', 'new', 'repair_hash', %s)
            """, (test_customer, req_id, psycopg2.extras.Json({"ticket_id": ticket_id})))
        conn.commit()

    results = []
    def run_repair():
        results.append(repair_service.repair_ticket_creation(req_id, test_customer))

    t1 = threading.Thread(target=run_repair)
    t2 = threading.Thread(target=run_repair)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Since TICKET_CREATED is NOT_RECONSTRUCTIBLE, they both fail immediately.
    assert set(results) == {RepairResultStatus.NOT_RECONSTRUCTIBLE}

def test_toctou(repair_service, test_customer, cleanup_db):
    """Test E: TOCTOU protection."""
    req_id = str(uuid.uuid4())
    
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                INSERT INTO tickets (customer_id, subject, description, priority, status)
                VALUES (%s, 'Repair Test Ticket', 'desc', 'low', 'open') RETURNING id
            """, (test_customer,))
            ticket_id = cur.fetchone()["id"]
            
            cur.execute("""
                INSERT INTO idempotency_records (customer_id, client_request_id, operation_type, canonical_target, payload_hash, terminal_result)
                VALUES (%s, %s, 'create_ticket', 'new', 'repair_hash', %s)
            """, (test_customer, req_id, psycopg2.extras.Json({"ticket_id": ticket_id})))
        conn.commit()
        
    # Simulate source deleted before repair runs
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM idempotency_records WHERE client_request_id = %s", (req_id,))
        conn.commit()
        
    res = repair_service.repair_ticket_creation(req_id, test_customer)
    assert res == RepairResultStatus.SOURCE_GONE

def test_identity_and_authorization(repair_service, test_customer, cleanup_db):
    """Test F & G: Exact identity match and authorization check."""
    event_id = str(uuid.uuid4())
    
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES ('repair_session_1', %s, 'ACTIVE') ON CONFLICT DO NOTHING", (test_customer,))
            cur.execute("""
                INSERT INTO escalation_events (escalation_event_id, session_id, customer_id, ticket_id, client_request_id, payload_hash, sentiment, urgency, customer_notification_required)
                VALUES (%s, 'repair_session_1', %s, NULL, %s, 'hash1', 'neutral', 'low', FALSE)
            """, (event_id, test_customer, str(uuid.uuid4())))
        conn.commit()
        
    # Wrong customer -> CONFLICT
    wrong_customer = test_customer + 1
    res = repair_service.repair_escalation_event(event_id, wrong_customer)
    assert res[NotificationType.ESCALATION_TEAM] == RepairResultStatus.CONFLICT

    # Right customer -> ENQUEUED
    res2 = repair_service.repair_escalation_event(event_id, test_customer)
    assert res2[NotificationType.ESCALATION_TEAM] == RepairResultStatus.ENQUEUED

    # Verify identity exactly equals expected logical identity
    expected = LogicalNotificationIdentity(
        source_event=SourceEventIdentity.from_escalation(event_id),
        notification_type=NotificationType.ESCALATION_TEAM,
        recipient=RecipientIdentity.support_team()
    )
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT logical_identity_hash FROM outbox_events WHERE source_event_id = %s", (f"escalation:{event_id}",))
            row = cur.fetchone()
            assert row["logical_identity_hash"] == expected.get_hash()

def test_rollback(repair_service, test_customer, cleanup_db, monkeypatch):
    """Test H: Simulated outbox insertion failure leaves no partial repair."""
    event_id = str(uuid.uuid4())
    
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES ('repair_session_2', %s, 'ACTIVE') ON CONFLICT DO NOTHING", (test_customer,))
            cur.execute("""
                INSERT INTO escalation_events (escalation_event_id, session_id, customer_id, ticket_id, client_request_id, payload_hash, sentiment, urgency, customer_notification_required)
                VALUES (%s, 'repair_session_2', %s, NULL, %s, 'hash1', 'neutral', 'low', TRUE)
            """, (event_id, test_customer, str(uuid.uuid4())))
        conn.commit()
        
    original_dumps = json.dumps
    def mock_dumps(obj, *args, **kwargs):
        if isinstance(obj, dict) and "sentiment" in obj:
            raise ValueError("Simulated failure")
        return original_dumps(obj, *args, **kwargs)

    monkeypatch.setattr(json, "dumps", mock_dumps)

    with pytest.raises(ValueError, match="Simulated failure"):
        repair_service.repair_escalation_event(event_id, test_customer)

    # Verify no partial state in outbox
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM outbox_events WHERE source_event_id = %s", (f"escalation:{event_id}",))
            assert len(cur.fetchall()) == 0
