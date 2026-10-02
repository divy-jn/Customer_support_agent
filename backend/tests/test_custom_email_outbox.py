import os
import uuid
import json
import pytest
import psycopg2
import hashlib
from app.notification_identity import SourceEventIdentity, LogicalNotificationIdentity, NotificationType, RecipientIdentity

def get_db_connection():
    return psycopg2.connect(
        dbname="postgres",
        user="postgres",
        password=os.environ.get("TEST_DB_PASSWORD", "postgres"),
        host="localhost",
        port="5433"
    )

@pytest.fixture
def real_db_cursor():
    conn = get_db_connection()
    conn.autocommit = False
    cur = conn.cursor()
    try:
        # Create a test customer and ticket
        cur.execute("INSERT INTO customers (id, name, email) VALUES (9999, 'Test Cust', 'c@c.com') ON CONFLICT (id) DO NOTHING;")
        cur.execute("INSERT INTO tickets (id, customer_id, subject, description, status) VALUES (9999, 9999, 'Subj', 'Desc', 'open') ON CONFLICT (id) DO NOTHING;")
        yield cur
    finally:
        cur.close()
        conn.rollback()
        conn.close()

def build_rpc_args(client_request_id, subject="Subject", content="Hello World", cust_id=9999):
    payload = {
        "template": "CUSTOM_TICKET_EMAIL",
        "customer_id": cust_id,
        "ticket_id": 9999,
        "subject": subject,
        "message_content": content,
        "recipient_email": "user@example.com"
    }
    
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    
    source_event = SourceEventIdentity.from_custom_email(cust_id, client_request_id)
    recipient = RecipientIdentity.customer(cust_id)
    logical_identity = LogicalNotificationIdentity(
        source_event=source_event,
        notification_type=NotificationType.CUSTOM_EMAIL,
        recipient=recipient
    )
    
    return (
        cust_id, client_request_id, 9999, "user@example.com", 
        json.dumps(payload), payload_hash, logical_identity.get_hash(), 
        source_event.source_event_id, NotificationType.CUSTOM_EMAIL.value, recipient.principal
    )


def test_custom_email_rpc_success(real_db_cursor):
    client_request_id = str(uuid.uuid4())
    
    args = build_rpc_args(client_request_id)
    
    real_db_cursor.execute("""
        SELECT * FROM enqueue_custom_email_outbox_event(
            %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s
        )
    """, args)
    
    result = real_db_cursor.fetchone()[0]
    
    assert result["status"] == "success"
    assert "outbox_event_id" in result
    
    outbox_id = result["outbox_event_id"]
    
    # Verify outbox event
    real_db_cursor.execute("SELECT payload, notification_type FROM outbox_events WHERE event_id = %s", (outbox_id,))
    row = real_db_cursor.fetchone()
    assert row is not None
    payload, n_type = row
    assert n_type == "CUSTOM_EMAIL"
    assert payload["template"] == "CUSTOM_TICKET_EMAIL"
    assert payload["ticket_id"] == 9999
    assert payload["message_content"] == "Hello World"
    
    # Verify idempotency record
    real_db_cursor.execute("SELECT operation_type, canonical_target, terminal_result FROM idempotency_records WHERE customer_id = 9999 AND client_request_id = %s", (client_request_id,))
    idem_row = real_db_cursor.fetchone()
    assert idem_row is not None
    assert idem_row[0] == "custom_email"
    assert idem_row[1] == "9999"
    assert idem_row[2] == result

def test_custom_email_rpc_exact_replay(real_db_cursor):
    client_request_id = str(uuid.uuid4())
    args = build_rpc_args(client_request_id)
    
    # Call 1
    real_db_cursor.execute("""
        SELECT * FROM enqueue_custom_email_outbox_event(
            %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s
        )
    """, args)
    result1 = real_db_cursor.fetchone()[0]
    
    # Call 2 (exact replay)
    real_db_cursor.execute("""
        SELECT * FROM enqueue_custom_email_outbox_event(
            %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s
        )
    """, args)
    result2 = real_db_cursor.fetchone()[0]
    
    assert result1 == result2

def test_custom_email_rpc_idempotency_conflict(real_db_cursor):
    client_request_id = str(uuid.uuid4())
    args1 = build_rpc_args(client_request_id)
    
    # Call 1
    real_db_cursor.execute("""
        SELECT * FROM enqueue_custom_email_outbox_event(
            %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s
        )
    """, args1)
    result1 = real_db_cursor.fetchone()[0]
    assert result1["status"] == "success"
    
    # Call 2 (different payload)
    args2 = build_rpc_args(client_request_id, content="Different payload")
    real_db_cursor.execute("""
        SELECT * FROM enqueue_custom_email_outbox_event(
            %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s
        )
    """, args2)
    result2 = real_db_cursor.fetchone()[0]
    
    assert result2.get("error") == "IdempotencyConflict"
    assert result2.get("previous_result") == result1

def test_custom_email_rpc_auth_failure(real_db_cursor):
    client_request_id = str(uuid.uuid4())
    
    # Call with wrong customer_id (8888 not owner of ticket 9999)
    args = build_rpc_args(client_request_id, cust_id=8888)
    real_db_cursor.execute("""
        SELECT * FROM enqueue_custom_email_outbox_event(
            %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s
        )
    """, args)
    result = real_db_cursor.fetchone()[0]
    
    assert "error" in result
    assert "not found or does not belong to you" in result["error"]
