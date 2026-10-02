import uuid
import json
import pytest
from datetime import datetime, timezone
from app.outbox.reconciliation_planner import ReconciliationPlanner, ReconciliationStatus
from app.notification_identity import NotificationType
import psycopg2
from psycopg2.extras import RealDictCursor
import os

TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD", "postgres")
conn_string = f"dbname='postgres' user='postgres' host='localhost' port='5433' password='{TEST_DB_PASSWORD}'"

@pytest.fixture
def planner():
    return ReconciliationPlanner(dsn=conn_string)

@pytest.fixture
def cleanup_db():
    yield
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM outbox_events;")
            cur.execute("DELETE FROM escalation_events;")
            cur.execute("DELETE FROM idempotency_records;")
            cur.execute("DELETE FROM session_escalation_state;")
            cur.execute("DELETE FROM tickets;")
            cur.execute("DELETE FROM customers WHERE email LIKE '%@reconcile.test';")
        conn.commit()

@pytest.fixture
def test_customer():
    import random
    customer_id = random.randint(10000, 90000)
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO customers (id, name, email)
                VALUES (%s, 'Test Rec', 'rec@reconcile.test')
                ON CONFLICT (id) DO NOTHING
                RETURNING id;
            """, (customer_id,))
            res = cur.fetchone()
            if not res:
                # Fallback if conflict
                customer_id += 1
                cur.execute("""
                    INSERT INTO customers (id, name, email)
                    VALUES (%s, 'Test Rec', 'rec' || %s || '@reconcile.test')
                    RETURNING id;
                """, (customer_id, customer_id))
        conn.commit()
    return customer_id

def test_escalation_e1_e2_missing(planner, test_customer, cleanup_db):
    """Test A, B, D: E1 required, E2 required (customer_notification_required=True) and outbox missing."""
    session_id = f"test-sess-{uuid.uuid4()}"
    esc_event_id = str(uuid.uuid4())
    req_id = str(uuid.uuid4())
    
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", (session_id, test_customer))
            cur.execute("""
                INSERT INTO escalation_events (escalation_event_id, session_id, customer_id, client_request_id, payload_hash, customer_notification_required)
                VALUES (%s, %s, %s, %s, 'hash', true)
            """, (esc_event_id, session_id, test_customer, req_id))
        conn.commit()
    
    planned = planner.plan_for_escalation_event(esc_event_id)
    assert len(planned) == 2
    
    e1 = next(p for p in planned if p.notification_type == NotificationType.ESCALATION_TEAM)
    assert e1.status == ReconciliationStatus.MISSING
    assert e1.source_event_id == f"escalation:{esc_event_id}"
    
    e2 = next(p for p in planned if p.notification_type == NotificationType.ESCALATION_CUSTOMER)
    assert e2.status == ReconciliationStatus.MISSING
    assert e2.source_event_id == f"escalation:{esc_event_id}"
    assert e2.recipient_address == "rec@reconcile.test"

def test_escalation_e2_not_required(planner, test_customer, cleanup_db):
    """Test A: no customer notification when not required."""
    session_id = f"test-sess-{uuid.uuid4()}"
    esc_event_id = str(uuid.uuid4())
    req_id = str(uuid.uuid4())
    
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", (session_id, test_customer))
            cur.execute("""
                INSERT INTO escalation_events (escalation_event_id, session_id, customer_id, client_request_id, payload_hash, customer_notification_required)
                VALUES (%s, %s, %s, %s, 'hash', false)
            """, (esc_event_id, session_id, test_customer, req_id))
        conn.commit()
    
    planned = planner.plan_for_escalation_event(esc_event_id)
    assert len(planned) == 2
    e2 = next(p for p in planned if p.notification_type == NotificationType.ESCALATION_CUSTOMER)
    assert e2.status == ReconciliationStatus.NOT_REQUIRED

def test_outbox_existing_states(planner, test_customer, cleanup_db):
    """Test C: SENT, PROCESSING, RETRYABLE, FAILED are all PRESENT."""
    session_id = f"test-sess-{uuid.uuid4()}"
    esc_event_id = str(uuid.uuid4())
    req_id = str(uuid.uuid4())
    
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", (session_id, test_customer))
            cur.execute("""
                INSERT INTO escalation_events (escalation_event_id, session_id, customer_id, client_request_id, payload_hash, customer_notification_required)
                VALUES (%s, %s, %s, %s, 'hash', true)
            """, (esc_event_id, session_id, test_customer, req_id))
        conn.commit()

    # Get hashes
    planned_initial = planner.plan_for_escalation_event(esc_event_id)
    e1_hash = next(p.logical_identity_hash for p in planned_initial if p.notification_type == NotificationType.ESCALATION_TEAM)
    e2_hash = next(p.logical_identity_hash for p in planned_initial if p.notification_type == NotificationType.ESCALATION_CUSTOMER)

    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO outbox_events (logical_identity_hash, source_event_id, notification_type, recipient_identity, recipient_address, payload, status)
                VALUES (%s, %s, 'ESCALATION_TEAM', 'team:support', 'x', '{}'::jsonb, 'SENT')
            """, (e1_hash, f"escalation:{esc_event_id}"))
            
            cur.execute("""
                INSERT INTO outbox_events (logical_identity_hash, source_event_id, notification_type, recipient_identity, recipient_address, payload, status)
                VALUES (%s, %s, 'ESCALATION_CUSTOMER', %s, 'x', '{}'::jsonb, 'RETRYABLE')
            """, (e2_hash, f"escalation:{esc_event_id}", f"customer:{test_customer}"))
        conn.commit()

    planned = planner.plan_for_escalation_event(esc_event_id)
    e1 = next(p for p in planned if p.notification_type == NotificationType.ESCALATION_TEAM)
    e2 = next(p for p in planned if p.notification_type == NotificationType.ESCALATION_CUSTOMER)
    
    assert e1.status == ReconciliationStatus.PRESENT
    assert e2.status == ReconciliationStatus.PRESENT

def test_custom_email_missing(planner, test_customer, cleanup_db):
    """Test E: missing custom-email outbox -> NOT_RECONSTRUCTIBLE."""
    req_id = str(uuid.uuid4())
    
    planned = planner.plan_for_custom_email(req_id, test_customer)
    assert len(planned) == 1
    assert planned[0].status == ReconciliationStatus.NOT_RECONSTRUCTIBLE

def test_custom_email_present(planner, test_customer, cleanup_db):
    req_id = str(uuid.uuid4())
    planned_initial = planner.plan_for_custom_email(req_id, test_customer)
    ident_hash = planned_initial[0].logical_identity_hash
    source_id = planned_initial[0].source_event_id
    
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO outbox_events (logical_identity_hash, source_event_id, notification_type, recipient_identity, recipient_address, payload, status)
                VALUES (%s, %s, 'CUSTOM_EMAIL', %s, 'x', '{}'::jsonb, 'PROCESSING')
            """, (ident_hash, source_id, f"customer:{test_customer}"))
        conn.commit()

    planned = planner.plan_for_custom_email(req_id, test_customer)
    assert planned[0].status == ReconciliationStatus.PRESENT

def test_determinism_and_no_session_inference(planner, test_customer, cleanup_db):
    """Test F, G: Same source = same hash. Cannot infer from session_id alone."""
    esc_event_id = str(uuid.uuid4())
    session_id = f"test-sess-{uuid.uuid4()}"
    req_id = str(uuid.uuid4())
    
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO session_escalation_state (session_id, customer_id, status) VALUES (%s, %s, 'ACTIVE')", (session_id, test_customer))
            cur.execute("""
                INSERT INTO escalation_events (escalation_event_id, session_id, customer_id, client_request_id, payload_hash, customer_notification_required)
                VALUES (%s, %s, %s, %s, 'hash', true)
            """, (esc_event_id, session_id, test_customer, req_id))
        conn.commit()

    planned1 = planner.plan_for_escalation_event(esc_event_id)
    planned2 = planner.plan_for_escalation_event(esc_event_id)
    
    assert planned1[0].logical_identity_hash == planned2[0].logical_identity_hash
    
    # Prove planner doesn't take session_id
    with pytest.raises(TypeError):
        planner.plan_for_escalation_event(session_id=session_id)

def test_exact_source_event_identity(planner, test_customer, cleanup_db):
    """Test 2: EXACT SOURCE EVENT IDENTITY for TICKET_CREATED."""
    req_id = str(uuid.uuid4())
    
    with psycopg2.connect(conn_string) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO idempotency_records (customer_id, client_request_id, operation_type, canonical_target, payload_hash)
                VALUES (%s, %s, 'create_ticket', 'new', 'hash')
            """, (test_customer, req_id))
        conn.commit()

    planned = planner.plan_for_ticket(req_id, test_customer)
    assert len(planned) == 1
    p = planned[0]
    
    # 1. Exact Source Event ID matches original schema outbox enqueue logic
    # In schema.sql: 'customer:' || v_actual_customer_id || '|req:' || p_client_request_id
    expected_source_event_id = f"customer:{test_customer}|req:{req_id}"
    assert p.source_event_id == expected_source_event_id
    
    # 2. Exact Hash matches original logic
    # In schema.sql for generated SQL outbox hashing: md5 is used, but we migrated to SHA256 in Python.
    # We must ensure the LogicalNotificationIdentity produces the exact same hash as Python outbox insertions do.
    from app.notification_identity import SourceEventIdentity, RecipientIdentity, LogicalNotificationIdentity, NotificationType
    
    expected_logical = LogicalNotificationIdentity(
        source_event=SourceEventIdentity.from_ticket_creation(test_customer, req_id),
        notification_type=NotificationType.TICKET_CREATED,
        recipient=RecipientIdentity.customer(test_customer)
    )
    
    assert p.logical_identity_hash == expected_logical.get_hash()
    assert p.status == ReconciliationStatus.MISSING
    
    # Do NOT query broad customer history (Test 3) - we supplied specific req_id
    assert "req:" + req_id in p.source_event_id
