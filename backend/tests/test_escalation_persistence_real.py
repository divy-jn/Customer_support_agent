import os
import pytest
import uuid
import psycopg2
from concurrent.futures import ThreadPoolExecutor, as_completed

TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD")
if not TEST_DB_PASSWORD:
    raise ValueError("TEST_DB_PASSWORD environment variable is required to run real postgres tests. Provide it in the local Docker environment.")

conn_string = f"dbname='postgres' user='postgres' host='localhost' port='5433' password='{TEST_DB_PASSWORD}'"

def execute_concurrently(func, *args, **kwargs):
    concurrency = 3
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(func, *args, **kwargs) for _ in range(concurrency)]
        for future in as_completed(futures):
            results.append(future.result())
    return results

def try_insert_state(session_id, customer_id):
    """
    Attempts to insert a session escalation state, mimicking the Supabase PostgREST 
    autocommit behavior where unique violations fail the query but not the overall
    connection state.
    """
    conn = psycopg2.connect(conn_string)
    # Supabase HTTP API operates in autocommit equivalent for single statements
    conn.autocommit = True 
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO session_escalation_state (session_id, customer_id, status)
            VALUES (%s, %s, 'NONE')
            """,
            (session_id, customer_id)
        )
        cur.close()
        return "SUCCESS"
    except psycopg2.errors.UniqueViolation:
        return "UNIQUE_VIOLATION"
    except Exception as e:
        return f"ERROR: {str(e)}"
    finally:
        conn.close()
        
def try_insert_and_rollback(session_id, customer_id, delay_seconds=0.5):
    """
    Simulates a transaction that inserts but then rolls back (e.g. if initialization fails midway).
    """
    conn = psycopg2.connect(conn_string)
    conn.autocommit = False # Manual transaction
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO session_escalation_state (session_id, customer_id, status)
            VALUES (%s, %s, 'NONE')
            """,
            (session_id, customer_id)
        )
        import time
        time.sleep(delay_seconds)
        conn.rollback()
        cur.close()
        return "ROLLED_BACK"
    finally:
        conn.close()

def get_state(session_id):
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT status, customer_id FROM session_escalation_state WHERE session_id = %s;", (session_id,))
        row = cur.fetchone()
        cur.close()
        return row
    finally:
        conn.close()

def get_row_count(session_id):
    conn = psycopg2.connect(conn_string)
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM session_escalation_state WHERE session_id = %s;", (session_id,))
        row = cur.fetchone()
        cur.close()
        return row[0]
    finally:
        conn.close()

class TestRealEscalationPersistence:
    
    def test_concurrent_initialization(self):
        """
        Verify durable initialization is genuinely concurrency-safe.
        Required result: exactly one durable state row, status = NONE, no duplicate rows.
        """
        session_id = str(uuid.uuid4())
        customer_id = 1
        
        # Two concurrent requests attempting to initialize the same session_id
        results = execute_concurrently(try_insert_state, session_id, customer_id)
        
        # Verify exactly one succeeded, and the rest got unique constraint violations
        success_count = sum(1 for r in results if r == "SUCCESS")
        conflict_count = sum(1 for r in results if r == "UNIQUE_VIOLATION")
        
        assert success_count == 1, f"Expected exactly 1 success, got {success_count}. Results: {results}"
        assert success_count + conflict_count == len(results)
        
        # Verify exactly one durable state row exists
        assert get_row_count(session_id) == 1
        
        # Verify status = NONE
        state = get_state(session_id)
        assert state is not None
        assert state[0] == "NONE"
        assert state[1] == customer_id

    def test_initializer_rollback(self):
        """
        Cover the case where the first initializer rolls back and the second initializer 
        can subsequently create the row.
        """
        session_id = str(uuid.uuid4())
        customer_id = 1
        
        results = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            # 1. Start a slow transaction that inserts then rolls back
            f1 = executor.submit(try_insert_and_rollback, session_id, customer_id, 1.0)
            
            # Yield briefly to ensure f1 grabs the row lock/uncommitted insert first
            import time
            time.sleep(0.1)
            
            # 2. Concurrently attempt a fast autocommit insert
            # This should block until f1 rolls back, then succeed.
            f2 = executor.submit(try_insert_state, session_id, customer_id)
            
            results = [f1.result(), f2.result()]
            
        assert "ROLLED_BACK" in results
        assert "SUCCESS" in results
        
        # Verify row actually got created and committed by the second attempt
        assert get_row_count(session_id) == 1
        state = get_state(session_id)
        assert state is not None
        assert state[0] == "NONE"

    def test_customer_isolation_enforced(self):
        """
        Verify customer/session isolation in real Postgres.
        If a session already exists for customer A, querying/inserting by B 
        must not modify or leak state.
        """
        session_id = str(uuid.uuid4())
        
        # Customer A initializes
        assert try_insert_state(session_id, customer_id=1) == "SUCCESS"
        
        # Customer B attempts to initialize
        assert try_insert_state(session_id, customer_id=2) == "UNIQUE_VIOLATION"
        
        # Validate that the actual state in DB remains strictly tied to Customer A
        state = get_state(session_id)
        assert state[1] == 1, "Customer isolation failed: DB row modified to customer 2"
        
        # The python layer (app.persistence.escalation) must enforce fetch validation 
        # (This test ensures the DB layer correctly throws unique violations without mutating)
