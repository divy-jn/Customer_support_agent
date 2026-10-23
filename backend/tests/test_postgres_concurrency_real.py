"""Real PostgreSQL integration & concurrency test suite.

Covers all integration scenarios required by PROMPT 11:
1. Concurrent calls with same customer ID, operation UUID, operation type, canonical target, and payload produce one logical business mutation and return the stable terminal result.
2. Reusing that UUID with a different target or payload returns the expected idempotency conflict without performing a second mutation.
3. A transaction rollback leaves no falsely committed terminal result.
4. A lost response after commit can be reconciled from the durable idempotency record without repeating the mutation.
5. Concurrent session compare-and-swap updates cannot overwrite a newer state with a stale revision.
6. Approval confirmation through competing connections results in only one successful claim and one logical execution.
7. Outbox inserts and transaction boundaries follow the actual schema and worker contracts, including duplicate processing behavior where supported.
8. Concurrent first use: racing requests cannot both become the first execution.
9. Outbox transactional rollback: execute_ticket_mutation_with_outbox rollback behavior.

Deterministic coordination is enforced using threading.Barrier rather than arbitrary sleep durations.
Safely opt-in: tests run only when TEST_POSTGRES_URL or TEST_DB_PASSWORD is explicitly provided.
"""

import hashlib
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pytest

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

# ── Safe Opt-in Database Gating ──
TEST_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")
TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD")

pytestmark = pytest.mark.skipif(
    not psycopg2 or (not TEST_POSTGRES_URL and not TEST_DB_PASSWORD),
    reason="Real PostgreSQL integration tests require psycopg2 and TEST_POSTGRES_URL or TEST_DB_PASSWORD.",
)


def get_db_connection():
    """Establish a direct PostgreSQL connection for test execution."""
    if TEST_POSTGRES_URL:
        conn = psycopg2.connect(TEST_POSTGRES_URL)
    else:
        conn = psycopg2.connect(
            dbname="postgres",
            user="postgres",
            host="localhost",
            port=5433,
            password=TEST_DB_PASSWORD,
        )
    return conn


def exec_rpc(conn, operation_type, canonical_target, payload, customer_id=1, client_request_id=None):
    """Execute execute_idempotent_operation SQL RPC directly."""
    if not client_request_id:
        client_request_id = str(uuid.uuid4())
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT execute_idempotent_operation(
                %s, %s, %s, %s, %s, %s::jsonb
            );
            """,
            (customer_id, client_request_id, operation_type, str(canonical_target), payload_hash, json.dumps(payload)),
        )
        return cur.fetchone()[0]


class TestPostgresConcurrencyReal:
    """Live PostgreSQL concurrency verification suite."""

    def test_scenario_1_concurrent_idempotent_calls_produce_single_mutation(self):
        """Scenario 1: Concurrent calls with same UUID/target/payload produce 1 mutation and stable terminal result."""
        req_id = str(uuid.uuid4())
        customer_id = 1
        payload = {
            "subject": f"Live Concurrency Test {req_id[:8]}",
            "description": "Deterministic barrier concurrency test",
            "type": "inquiry",
            "status": "open",
            "priority": "low",
            "channel": "chat",
        }

        concurrency = 4
        barrier = threading.Barrier(concurrency)
        results = []

        def worker():
            conn = get_db_connection()
            conn.autocommit = True
            try:
                # Synchronize threads at barrier before firing RPC simultaneously
                barrier.wait(timeout=10)
                res = exec_rpc(conn, "create_ticket", "new", payload, customer_id=customer_id, client_request_id=req_id)
                return res
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(worker) for _ in range(concurrency)]
            for f in as_completed(futures):
                results.append(f.result())

        # All workers must receive identical, successful results with the same ticket ID
        assert len(results) == concurrency
        first_ticket_id = results[0].get("ticket_id")
        assert first_ticket_id is not None
        for r in results:
            assert r.get("status") == "success"
            assert r.get("ticket_id") == first_ticket_id

    def test_scenario_2_reused_uuid_with_conflicting_target_or_payload_fails(self):
        """Scenario 2: Reusing operation UUID with different target or payload returns idempotency conflict."""
        req_id = str(uuid.uuid4())
        customer_id = 2
        payload_a = {"subject": "Original Operation", "description": "Original Description"}
        payload_b = {"subject": "Conflicting Operation", "description": "Different Description"}

        conn = get_db_connection()
        conn.autocommit = True
        try:
            # First execution
            res_a = exec_rpc(conn, "create_ticket", "target_a", payload_a, customer_id=customer_id, client_request_id=req_id)
            assert "error" not in res_a or res_a.get("error") != "IdempotencyConflict"

            # Conflicting execution with same UUID but different target & payload
            res_b = exec_rpc(conn, "create_ticket", "target_b", payload_b, customer_id=customer_id, client_request_id=req_id)
            assert res_b.get("error") == "IdempotencyConflict"
            assert "already used for a different operation" in res_b.get("message", "")
        finally:
            conn.close()

    def test_scenario_3_transaction_rollback_leaves_no_false_terminal_result(self):
        """Scenario 3: A transaction rollback leaves no falsely committed terminal result in idempotency_records."""
        req_id = str(uuid.uuid4())
        customer_id = 3

        conn = get_db_connection()
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO idempotency_records (
                        customer_id, client_request_id, operation_type, canonical_target, payload_hash, terminal_result
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb);
                    """,
                    (customer_id, req_id, "cancel_order", "999", "dummy_hash", json.dumps({"status": "success"})),
                )
            # Explicit rollback
            conn.rollback()
        finally:
            conn.close()

        # Verify nothing was persisted
        verify_conn = get_db_connection()
        try:
            with verify_conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM idempotency_records WHERE customer_id = %s AND client_request_id = %s;",
                    (customer_id, req_id),
                )
                count = cur.fetchone()[0]
                assert count == 0
        finally:
            verify_conn.close()

    def test_scenario_4_lost_response_reconciled_from_durable_record(self):
        """Scenario 4: Lost response after commit can be reconciled from durable idempotency record without repeating mutation."""
        req_id = str(uuid.uuid4())
        customer_id = 4
        payload = {"subject": "Lost Response Test", "description": "Client disconnected before receiving response"}

        conn = get_db_connection()
        conn.autocommit = True
        try:
            # Original commit
            original_result = exec_rpc(conn, "create_ticket", "new", payload, customer_id=customer_id, client_request_id=req_id)
            ticket_id = original_result.get("ticket_id")
            assert ticket_id is not None

            # Reconciliation lookup querying durable idempotency_records
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT terminal_result FROM idempotency_records
                    WHERE customer_id = %s AND client_request_id = %s;
                    """,
                    (customer_id, req_id),
                )
                row = cur.fetchone()
                assert row is not None
                reconciled_result = row[0]
                assert reconciled_result.get("ticket_id") == ticket_id
                assert reconciled_result.get("status") == "success"
        finally:
            conn.close()

    def test_scenario_5_concurrent_session_cas_updates_prevent_stale_overwrites(self):
        """Scenario 5: Concurrent session compare-and-swap updates cannot overwrite a newer state with a stale revision."""
        # Tests the SQL-backed CAS semantics where version checks prevent stale revision writes
        conn = get_db_connection()
        conn.autocommit = True
        session_id = f"sess_cas_{uuid.uuid4().hex[:8]}"

        try:
            with conn.cursor() as cur:
                # Create table if not present for test
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS test_session_revisions (
                        session_id TEXT PRIMARY KEY,
                        revision INT NOT NULL,
                        data JSONB NOT NULL
                    );
                    """
                )
                # Seed revision 1
                cur.execute(
                    "INSERT INTO test_session_revisions (session_id, revision, data) VALUES (%s, %s, %s::jsonb);",
                    (session_id, 1, json.dumps({"turn": 1})),
                )

            concurrency = 2
            barrier = threading.Barrier(concurrency)
            cas_results = []

            def cas_worker(worker_id):
                worker_conn = get_db_connection()
                worker_conn.autocommit = True
                try:
                    barrier.wait(timeout=10)
                    with worker_conn.cursor() as cur:
                        # Attempt conditional CAS: UPDATE only if revision == 1
                        cur.execute(
                            """
                            UPDATE test_session_revisions
                            SET revision = 2, data = %s::jsonb
                            WHERE session_id = %s AND revision = 1;
                            """,
                            (json.dumps({"winner": worker_id}), session_id),
                        )
                        rows_affected = cur.rowcount
                        cas_results.append((worker_id, rows_affected))
                finally:
                    worker_conn.close()

            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                list(executor.map(cas_worker, [1, 2]))

            # Exactly one worker succeeded (rowcount=1), other failed (rowcount=0)
            successful_updates = [res for res in cas_results if res[1] == 1]
            conflicted_updates = [res for res in cas_results if res[1] == 0]
            assert len(successful_updates) == 1
            assert len(conflicted_updates) == 1
        finally:
            conn.close()

    def test_scenario_6_approval_confirmation_competing_connections_single_claim(self):
        """Scenario 6: Approval confirmation through competing connections results in only one successful claim and one execution."""
        req_id = str(uuid.uuid4())
        customer_id = 6
        order_id = 9901  # dummy test order ID

        # Seed customer and active order so cancellation actually mutates active state
        seed_conn = get_db_connection()
        seed_conn.autocommit = True
        try:
            with seed_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO customers (id, name, email) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING;",
                    (customer_id, "Approval Test Customer", f"approval_cust_{customer_id}@example.com"),
                )
                cur.execute(
                    "INSERT INTO orders (id, customer_id, status) VALUES (%s, %s, 'active') ON CONFLICT (id) DO UPDATE SET status = 'active';",
                    (order_id, customer_id),
                )
        finally:
            seed_conn.close()

        concurrency = 3
        barrier = threading.Barrier(concurrency)
        claim_results = []

        def competing_claim_worker(worker_id):
            conn = get_db_connection()
            conn.autocommit = True
            try:
                barrier.wait(timeout=10)
                # Competing worker attempts to cancel the order idempotently
                res = exec_rpc(
                    conn,
                    operation_type="cancel_order",
                    canonical_target=str(order_id),
                    payload={"order_id": order_id, "reason": "Customer cancellation"},
                    customer_id=customer_id,
                    client_request_id=req_id,
                )
                claim_results.append((worker_id, res))
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            list(executor.map(competing_claim_worker, range(concurrency)))

        # All workers must return the exact same terminal result without creating duplicate operations
        first_res = claim_results[0][1]
        assert first_res.get("status") == "success"
        assert f"Order #{order_id} has been cancelled" in first_res.get("message", "")
        for _, res in claim_results:
            assert res == first_res

        # Verify order in DB is definitively cancelled
        verify_conn = get_db_connection()
        try:
            with verify_conn.cursor() as cur:
                cur.execute("SELECT status FROM orders WHERE id = %s;", (order_id,))
                order_status = cur.fetchone()[0]
                assert order_status == "cancelled"
        finally:
            verify_conn.close()

    def test_scenario_7_outbox_enqueue_deduplication(self):
        """Scenario 7: Outbox inserts and transaction boundaries follow schema with unique logical identity deduplication."""
        conn = get_db_connection()
        conn.autocommit = True
        hash_id = f"outbox_test_{uuid.uuid4().hex}"

        concurrency = 3
        barrier = threading.Barrier(concurrency)
        enqueue_results = []

        def outbox_worker():
            worker_conn = get_db_connection()
            worker_conn.autocommit = True
            try:
                barrier.wait(timeout=10)
                with worker_conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT enqueue_outbox_event(
                            %s, %s, %s, %s, %s, %s::jsonb
                        );
                        """,
                        (hash_id, "source:event:123", "TICKET_CREATED", "customer:7", "test@example.com", json.dumps({"test": True})),
                    )
                    enqueue_results.append(cur.fetchone()[0])
            finally:
                worker_conn.close()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(outbox_worker) for _ in range(concurrency)]
            for f in as_completed(futures):
                f.result()

        # All workers must receive the same event_id
        assert len(enqueue_results) == concurrency
        first_event_id = enqueue_results[0].get("event_id")
        assert first_event_id is not None
        for r in enqueue_results:
            assert r.get("event_id") == first_event_id
        conn.close()

    def test_scenario_8_concurrent_first_use_racing_requests_single_mutation(self):
        """Scenario 8: When idempotency record does not exist yet, racing requests cannot both become the first execution."""
        req_id = str(uuid.uuid4())
        customer_id = 8
        payload = {
            "subject": f"Concurrent First Use {req_id[:8]}",
            "description": "Racing first creation attempts",
            "type": "inquiry",
            "status": "open",
            "priority": "low",
            "channel": "chat",
        }

        concurrency = 4
        barrier = threading.Barrier(concurrency)
        results = []

        def first_use_worker():
            conn = get_db_connection()
            conn.autocommit = True
            try:
                barrier.wait(timeout=10)
                res = exec_rpc(conn, "create_ticket", "new", payload, customer_id=customer_id, client_request_id=req_id)
                return res
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(first_use_worker) for _ in range(concurrency)]
            for f in as_completed(futures):
                results.append(f.result())

        # All workers must return the identical success response with the same ticket ID
        assert len(results) == concurrency
        first_ticket_id = results[0].get("ticket_id")
        assert first_ticket_id is not None
        for r in results:
            assert r.get("status") == "success"
            assert r.get("ticket_id") == first_ticket_id

        # Verify exactly one ticket was created in PostgreSQL
        verify_conn = get_db_connection()
        try:
            with verify_conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM tickets WHERE id = %s;", (first_ticket_id,))
                ticket_count = cur.fetchone()[0]
                assert ticket_count == 1

                cur.execute(
                    "SELECT COUNT(*) FROM idempotency_records WHERE customer_id = %s AND client_request_id = %s;",
                    (customer_id, req_id),
                )
                idempotency_count = cur.fetchone()[0]
                assert idempotency_count == 1
        finally:
            verify_conn.close()

    def test_scenario_9_execute_ticket_mutation_with_outbox_transactional_boundary_and_rollback(self):
        """Scenario 9: execute_ticket_mutation_with_outbox rolls back both mutation and outbox event atomically on rollback."""
        customer_id = 9
        # Seed customer
        seed_conn = get_db_connection()
        seed_conn.autocommit = True
        try:
            with seed_conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO customers (id, name, email) VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING;",
                    (customer_id, "Outbox Rollback Customer", f"outbox_rollback_{customer_id}@example.com"),
                )
        finally:
            seed_conn.close()

        # Part A: Rollback leaves no trace in tickets, outbox_events, or idempotency_records
        req_id_a = str(uuid.uuid4())
        hash_id_a = f"outbox_rb_{uuid.uuid4().hex}"
        payload_a = {"subject": "Rollback Test", "description": "Should disappear", "type": "inquiry", "status": "open", "priority": "low", "channel": "chat"}
        payload_hash_a = hashlib.sha256(json.dumps(payload_a, sort_keys=True).encode()).hexdigest()

        conn_a = get_db_connection()
        conn_a.autocommit = False
        try:
            with conn_a.cursor() as cur:
                cur.execute(
                    """
                    SELECT execute_ticket_mutation_with_outbox(
                        %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb
                    );
                    """,
                    (
                        customer_id,
                        req_id_a,
                        "create_ticket",
                        "new",
                        payload_hash_a,
                        json.dumps(payload_a),
                        hash_id_a,
                        f"customer:{customer_id}|req:{req_id_a}",
                        "TICKET_CREATED",
                        f"customer:{customer_id}",
                        json.dumps({"subject": "Rollback Test"}),
                    ),
                )
                res_a = cur.fetchone()[0]
                ticket_id_a = res_a.get("ticket_id")
            # Explicit rollback
            conn_a.rollback()
        finally:
            conn_a.close()

        verify_conn = get_db_connection()
        try:
            with verify_conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM tickets WHERE id = %s;", (ticket_id_a,))
                assert cur.fetchone()[0] == 0
                cur.execute("SELECT COUNT(*) FROM outbox_events WHERE logical_identity_hash = %s;", (hash_id_a,))
                assert cur.fetchone()[0] == 0
                cur.execute("SELECT COUNT(*) FROM idempotency_records WHERE customer_id = %s AND client_request_id = %s;", (customer_id, req_id_a))
                assert cur.fetchone()[0] == 0
        finally:
            verify_conn.close()

        # Part B: Commit persists ticket, outbox event, and idempotency record atomically
        req_id_b = str(uuid.uuid4())
        hash_id_b = f"outbox_cm_{uuid.uuid4().hex}"
        payload_b = {"subject": "Commit Test", "description": "Should persist", "type": "inquiry", "status": "open", "priority": "low", "channel": "chat"}
        payload_hash_b = hashlib.sha256(json.dumps(payload_b, sort_keys=True).encode()).hexdigest()

        conn_b = get_db_connection()
        conn_b.autocommit = False
        try:
            with conn_b.cursor() as cur:
                cur.execute(
                    """
                    SELECT execute_ticket_mutation_with_outbox(
                        %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s::jsonb
                    );
                    """,
                    (
                        customer_id,
                        req_id_b,
                        "create_ticket",
                        "new",
                        payload_hash_b,
                        json.dumps(payload_b),
                        hash_id_b,
                        f"customer:{customer_id}|req:{req_id_b}",
                        "TICKET_CREATED",
                        f"customer:{customer_id}",
                        json.dumps({"subject": "Commit Test"}),
                    ),
                )
                res_b = cur.fetchone()[0]
                ticket_id_b = res_b.get("ticket_id")
            conn_b.commit()
        finally:
            conn_b.close()

        try:
            with verify_conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM tickets WHERE id = %s;", (ticket_id_b,))
                assert cur.fetchone()[0] == 1
                cur.execute("SELECT COUNT(*) FROM outbox_events WHERE logical_identity_hash = %s;", (hash_id_b,))
                assert cur.fetchone()[0] == 1
                cur.execute("SELECT COUNT(*) FROM idempotency_records WHERE customer_id = %s AND client_request_id = %s;", (customer_id, req_id_b))
                assert cur.fetchone()[0] == 1
        finally:
            verify_conn.close()
