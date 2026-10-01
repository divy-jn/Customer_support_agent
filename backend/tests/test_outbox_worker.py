"""
Slice 3.3 — Outbox Worker Integration Tests

Tests run against a real local PostgreSQL database (port 5433).
No real email is sent; all tests use the DummySender abstraction.

Test coverage (mapped to the spec requirements):
  1.  Two workers cannot claim the same row simultaneously.
  2.  Multiple eligible rows can be claimed concurrently.
  3.  A stale PROCESSING row is recoverable.
  4.  Successful delivery results in SENT.
  5.  Retryable failure schedules a future retry.
  6.  Terminal failure becomes FAILED.
  7.  Maximum attempts prevents infinite retry.
  8.  Concurrent stale recovery cannot create duplicate processing ownership.
  9.  Only eligible rows are claimed.
  10. SENT and FAILED rows are never claimed by the worker.
  11. Transaction failure during claim does not leave a permanently locked/owned row.
  12. A worker restart can recover abandoned work.
"""

import json
import os
import time
import uuid
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed

import psycopg2
import psycopg2.extras
import pytest

from app.outbox.sender import (
    DummySender,
    SendOutcome,
    SendResult,
)
from app.outbox.worker import (
    CLAIM_SQL,
    MARK_FAILED_SQL,
    MARK_RETRYABLE_SQL,
    MARK_SENT_SQL,
    RECOVER_STALE_SQL,
    OutboxWorker,
    RetryPolicy,
)

# ──────────────────────────────────────────────
#  Database Configuration
# ──────────────────────────────────────────────

TEST_DB_PASSWORD = os.environ.get("TEST_DB_PASSWORD")
if not TEST_DB_PASSWORD:
    raise ValueError(
        "TEST_DB_PASSWORD environment variable is required to run real integration tests."
    )

DSN = f"dbname='postgres' user='postgres' host='localhost' port='5433' password='{TEST_DB_PASSWORD}'"


# ──────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────

def _conn():
    """Return a new psycopg2 connection with autocommit off."""
    return psycopg2.connect(DSN)


def _insert_event(
    *,
    status: str = "PENDING",
    next_attempt_at: str = "NOW()",
    attempt_count: int = 0,
    claimed_at: str | None = None,
    claim_token: str | None = None,
    notification_type: str = "TICKET_CREATED",
    recipient_address: str = "test@example.com",
    payload: dict | None = None,
) -> str:
    """Insert a test outbox event and return its event_id."""
    hash_id = str(uuid.uuid4())
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor()

    claimed_expr = f"'{claimed_at}'::timestamptz" if claimed_at else "NULL"
    claim_token_expr = f"'{claim_token}'::uuid" if claim_token else "NULL"
    payload_json = json.dumps(payload or {"ticket_id": 12345, "message": "Test Message"})

    cur.execute(
        f"""
        INSERT INTO outbox_events (
            logical_identity_hash, source_event_id, notification_type,
            recipient_identity, recipient_address, payload,
            status, next_attempt_at, attempt_count, claimed_at, claim_token
        ) VALUES (
            %s, %s, %s, %s, %s, %s::jsonb,
            %s, {next_attempt_at}, %s, {claimed_expr}, {claim_token_expr}
        )
        RETURNING event_id;
        """,
        (
            hash_id,
            f"src-{hash_id[:8]}",
            notification_type,
            "customer:1",
            recipient_address,
            payload_json,
            status,
            attempt_count,
        ),
    )
    event_id = str(cur.fetchone()[0])
    cur.close()
    conn.close()
    return event_id


def _get_event(event_id: str) -> dict:
    """Fetch a single outbox event by event_id."""
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM outbox_events WHERE event_id = %s", (event_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else {}


def _claim_rows_sync(dsn: str, batch_size: int = 10) -> list[dict]:
    """Claim rows in a single synchronous transaction (for concurrency tests)."""
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(CLAIM_SQL, (batch_size,))
    rows = cur.fetchall()
    conn.commit()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────
#  RetryPolicy Unit Tests
# ──────────────────────────────────────────────

class TestRetryPolicy:
    def test_exponential_backoff_formula(self):
        policy = RetryPolicy(base_delay_seconds=30, max_backoff_seconds=3600)
        assert policy.compute_next_attempt_delay(1) == 30       # 30 * 2^0
        assert policy.compute_next_attempt_delay(2) == 60       # 30 * 2^1
        assert policy.compute_next_attempt_delay(3) == 120      # 30 * 2^2
        assert policy.compute_next_attempt_delay(4) == 240      # 30 * 2^3
        assert policy.compute_next_attempt_delay(5) == 480      # 30 * 2^4

    def test_backoff_capped_at_max(self):
        policy = RetryPolicy(base_delay_seconds=30, max_backoff_seconds=100)
        assert policy.compute_next_attempt_delay(10) == 100

    def test_terminal_outcomes(self):
        policy = RetryPolicy()
        assert policy.is_terminal_outcome(SendOutcome.PERMANENT_RECIPIENT_FAILURE) is True
        assert policy.is_terminal_outcome(SendOutcome.AUTH_CONFIG_FAILURE) is False
        assert policy.is_terminal_outcome(SendOutcome.TRANSIENT_FAILURE) is False
        assert policy.is_terminal_outcome(SendOutcome.SUCCESS) is False

    def test_max_attempts_exceeded(self):
        policy = RetryPolicy(max_attempts=5)
        assert policy.has_exceeded_max_attempts(4) is False
        assert policy.has_exceeded_max_attempts(5) is True
        assert policy.has_exceeded_max_attempts(6) is True


# ──────────────────────────────────────────────
#  Worker Integration Tests (Real PostgreSQL)
# ──────────────────────────────────────────────

class TestOutboxWorkerConcurrency:
    """Tests 1, 2, 8, 9, 10, 11: Concurrency and eligibility."""

    def test_1_two_workers_cannot_claim_same_row(self):
        """Test 1: Two workers cannot claim the same row simultaneously."""
        # Drain any residual claimable rows first
        _claim_rows_sync(DSN, 1000)

        event_id = _insert_event()

        # Run two concurrent claim operations
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(_claim_rows_sync, DSN, 10) for _ in range(2)]
            results = [f.result() for f in as_completed(futures)]

        # Flatten all claimed event_ids
        all_claimed_ids = []
        for batch in results:
            for row in batch:
                all_claimed_ids.append(str(row["event_id"]))

        # The event should appear in exactly one worker's batch
        assert all_claimed_ids.count(event_id) == 1

    def test_2_multiple_eligible_rows_claimed_concurrently(self):
        """Test 2: Multiple eligible rows can be claimed concurrently."""
        ids = [_insert_event() for _ in range(6)]

        # Three workers each claiming up to 10
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(_claim_rows_sync, DSN, 10) for _ in range(3)]
            results = [f.result() for f in as_completed(futures)]

        all_claimed = []
        for batch in results:
            for row in batch:
                all_claimed.append(str(row["event_id"]))

        # All 6 should be claimed (each exactly once)
        for eid in ids:
            assert all_claimed.count(eid) == 1, f"Event {eid} was claimed {all_claimed.count(eid)} times"

    def test_9_only_eligible_rows_are_claimed(self):
        """Test 9: Only PENDING/RETRYABLE with next_attempt_at <= NOW() are claimed."""
        eligible_id = _insert_event(status="PENDING")
        retryable_id = _insert_event(status="RETRYABLE")

        # Future next_attempt_at — should NOT be claimed
        future_id = _insert_event(
            status="RETRYABLE",
            next_attempt_at="NOW() + INTERVAL '1 hour'",
        )

        claimed = _claim_rows_sync(DSN, 50)
        claimed_ids = [str(r["event_id"]) for r in claimed]

        assert eligible_id in claimed_ids
        assert retryable_id in claimed_ids
        assert future_id not in claimed_ids

    def test_10_sent_and_failed_rows_never_claimed(self):
        """Test 10: SENT and FAILED rows are never claimed by the worker."""
        sent_id = _insert_event(status="SENT")
        failed_id = _insert_event(status="FAILED")
        pending_id = _insert_event(status="PENDING")

        claimed = _claim_rows_sync(DSN, 50)
        claimed_ids = [str(r["event_id"]) for r in claimed]

        assert sent_id not in claimed_ids
        assert failed_id not in claimed_ids
        assert pending_id in claimed_ids

    def test_11_transaction_failure_during_claim_no_permanent_lock(self):
        """
        Test 11: Transaction failure during claim does not leave a
        permanently locked/owned row.
        """
        event_id = _insert_event()

        # Simulate a claim that rolls back
        conn = _conn()
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(CLAIM_SQL, (10,))
        claimed = cur.fetchall()
        claimed_ids = [str(r["event_id"]) for r in claimed]
        assert event_id in claimed_ids

        # Rollback — simulates crash during claim
        conn.rollback()
        cur.close()
        conn.close()

        # Row should be back to PENDING, claimable again
        row = _get_event(event_id)
        assert row["status"] == "PENDING"

        # Another worker can now claim it
        second_claimed = _claim_rows_sync(DSN, 10)
        second_ids = [str(r["event_id"]) for r in second_claimed]
        assert event_id in second_ids

    def test_13_lease_ownership_recovery_rejects_old_worker_success(self):
        """
        Worker A claims row. Worker B recovers stale row and claims it.
        Worker B marks it FAILED. Worker A resumes and attempts to mark it SENT.
        Worker A's update is rejected because claim token no longer matches.
        Final state remains FAILED.
        """
        _claim_rows_sync(DSN, 1000)
        event_id = _insert_event()
        
        worker_a = OutboxWorker(dsn=DSN, sender=DummySender(), batch_size=1)
        conn_a = _conn()
        conn_a.autocommit = False
        with conn_a.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(CLAIM_SQL, (1,))
            row_a = cur.fetchone()
        conn_a.commit()
        claim_token_a = row_a["claim_token"]
        
        conn_update = _conn()
        conn_update.autocommit = True
        with conn_update.cursor() as cur:
            cur.execute("UPDATE outbox_events SET claimed_at = '2020-01-01T00:00:00Z' WHERE event_id = %s", (event_id,))
        conn_update.close()
            
        sender_b = DummySender(default_outcome=SendOutcome.PERMANENT_RECIPIENT_FAILURE)
        # Use a large batch_size so it doesn't get starved by other test rows
        worker_b = OutboxWorker(dsn=DSN, sender=sender_b, batch_size=1000, stale_threshold_seconds=60)
        
        # We need to manually sleep or use a different connection for recover to ensure NOW() shifts
        import time; time.sleep(0.1)
        
        worker_b._recover_stale_rows()
        worker_b._claim_and_process()
        
        assert _get_event(event_id)["status"] == "FAILED"
        
        # Worker A tries to mark it SENT
        worker_a._mark_sent(conn_a, event_id, claim_token_a, row_a["logical_identity_hash"], row_a["notification_type"])
        
        assert _get_event(event_id)["status"] == "FAILED"
        conn_a.close()

    def test_14_lease_ownership_inverse_stale_failure_rejected(self):
        """Worker A's stale failure must not overwrite Worker B's successful SENT."""
        _claim_rows_sync(DSN, 1000)
        event_id = _insert_event()
        
        worker_a = OutboxWorker(dsn=DSN, sender=DummySender(), batch_size=1)
        conn_a = _conn()
        conn_a.autocommit = False
        with conn_a.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(CLAIM_SQL, (1,))
            row_a = cur.fetchone()
        conn_a.commit()
        claim_token_a = row_a["claim_token"]
        
        conn_update = _conn()
        conn_update.autocommit = True
        with conn_update.cursor() as cur:
            cur.execute("UPDATE outbox_events SET claimed_at = '2020-01-01T00:00:00Z' WHERE event_id = %s", (event_id,))
        conn_update.close()
            
        worker_b = OutboxWorker(dsn=DSN, sender=DummySender(default_outcome=SendOutcome.SUCCESS), batch_size=100, stale_threshold_seconds=60)
        import time; time.sleep(0.1)
        worker_b._recover_stale_rows()
        worker_b._claim_and_process()
        
        assert _get_event(event_id)["status"] == "SENT"
        
        # Worker A tries to mark it RETRYABLE
        worker_a._mark_retryable(conn_a, event_id, claim_token_a, row_a["logical_identity_hash"], row_a["notification_type"], 60, "timeout")
        
        assert _get_event(event_id)["status"] == "SENT"
        conn_a.close()


class TestOutboxWorkerDelivery:
    """Tests 4, 5, 6, 7: Delivery outcome handling."""

    def test_4_successful_delivery_results_in_sent(self):
        """Test 4: Successful delivery results in SENT."""
        event_id = _insert_event()

        sender = DummySender(default_outcome=SendOutcome.SUCCESS)
        worker = OutboxWorker(dsn=DSN, sender=sender, batch_size=50)
        worker._claim_and_process()

        row = _get_event(event_id)
        assert row["status"] == "SENT"
        assert row["sent_at"] is not None
        assert row["claimed_at"] is None
        assert row["last_error"] is None
        assert len(sender.sent) >= 1

    def test_5_retryable_failure_schedules_future_retry(self):
        """Test 5: Retryable failure schedules a future retry."""
        event_id = _insert_event()

        sender = DummySender()
        sender.enqueue_outcome(
            SendResult(outcome=SendOutcome.TRANSIENT_FAILURE, error_message="Connection timeout")
        )

        worker = OutboxWorker(
            dsn=DSN,
            sender=sender,
            batch_size=50,
            retry_policy=RetryPolicy(base_delay_seconds=60),
        )
        worker._claim_and_process()

        row = _get_event(event_id)
        assert row["status"] == "RETRYABLE"
        assert row["claimed_at"] is None
        assert row["last_error"] is not None
        assert "timeout" in row["last_error"].lower()
        assert row["next_attempt_at"] is not None
        # next_attempt_at should be in the future
        from datetime import datetime, timezone
        assert row["next_attempt_at"] > datetime.now(timezone.utc)

    def test_6_terminal_failure_becomes_failed(self):
        """Test 6: Terminal failure (invalid recipient) becomes FAILED."""
        event_id = _insert_event()

        sender = DummySender()
        sender.enqueue_outcome(
            SendResult(
                outcome=SendOutcome.PERMANENT_RECIPIENT_FAILURE,
                error_message="550 User unknown",
            )
        )

        worker = OutboxWorker(dsn=DSN, sender=sender, batch_size=50)
        worker._claim_and_process()

        row = _get_event(event_id)
        assert row["status"] == "FAILED"
        assert row["claimed_at"] is None
        assert "550" in row["last_error"]

    def test_16_auth_config_failure_is_retryable(self):
        """Test 16: Auth/config failure becomes RETRYABLE, not FAILED, and does not consume attempt budget."""
        event_id = _insert_event(attempt_count=0)

        sender = DummySender()
        sender.enqueue_outcome(
            SendResult(
                outcome=SendOutcome.AUTH_CONFIG_FAILURE,
                error_message="535 Authentication failed",
            )
        )

        worker = OutboxWorker(dsn=DSN, sender=sender, batch_size=50)
        worker._claim_and_process()

        row = _get_event(event_id)
        assert row["status"] == "RETRYABLE"
        assert row["claimed_at"] is None
        assert row["attempt_count"] == 0  # Did not consume attempt budget

    def test_7_max_attempts_prevents_infinite_retry(self):
        """Test 7: Maximum attempts prevents infinite retry."""
        # Insert a row that has already been attempted 4 times (max=5)
        event_id = _insert_event(status="RETRYABLE", attempt_count=4)

        sender = DummySender()
        sender.enqueue_outcome(
            SendResult(outcome=SendOutcome.TRANSIENT_FAILURE, error_message="timeout")
        )

        worker = OutboxWorker(
            dsn=DSN,
            sender=sender,
            batch_size=50,
            retry_policy=RetryPolicy(max_attempts=5),
        )
        worker._claim_and_process()

        row = _get_event(event_id)
        # After the 5th attempt (4 previous + 1 claim increment), it should be FAILED
        assert row["status"] == "FAILED"
        assert "max attempts" in row["last_error"].lower()


class TestOutboxWorkerStaleRecovery:
    """Tests 3, 8, 12: Stale processing recovery."""

    def test_3_stale_processing_row_is_recoverable(self):
        """Test 3: A stale PROCESSING row is recoverable."""
        # Insert a row in PROCESSING with an old claimed_at
        event_id = _insert_event(
            status="PROCESSING",
            claimed_at="2020-01-01T00:00:00+00:00",
            attempt_count=1,
        )

        sender = DummySender()
        worker = OutboxWorker(
            dsn=DSN,
            sender=sender,
            stale_threshold_seconds=60,
        )
        recovered = worker._recover_stale_rows()

        assert recovered >= 1
        row = _get_event(event_id)
        assert row["status"] == "RETRYABLE"
        assert row["claimed_at"] is None

    def test_8_concurrent_stale_recovery_no_duplicate_ownership(self):
        """Test 8: Concurrent stale recovery cannot create duplicate processing ownership."""
        # Insert several stale rows
        stale_ids = []
        for _ in range(5):
            eid = _insert_event(
                status="PROCESSING",
                claimed_at="2020-01-01T00:00:00+00:00",
                attempt_count=1,
            )
            stale_ids.append(eid)

        # Run concurrent recovery sweeps
        def recover_sync(dsn):
            conn = psycopg2.connect(dsn)
            conn.autocommit = False
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(RECOVER_STALE_SQL, (5, 5, 60))
            rows = cur.fetchall()
            conn.commit()
            cur.close()
            conn.close()
            return [str(r["event_id"]) for r in rows]

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(recover_sync, DSN) for _ in range(3)]
            results = [f.result() for f in as_completed(futures)]

        # Flatten and count
        all_recovered = []
        for batch in results:
            all_recovered.extend(batch)

        # Each stale event should be recovered exactly once
        for eid in stale_ids:
            assert all_recovered.count(eid) == 1, (
                f"Event {eid} recovered {all_recovered.count(eid)} times"
            )

    def test_12_worker_restart_can_recover_abandoned_work(self):
        """Test 12: A worker restart can recover abandoned work."""
        # Simulate Worker A crashing: insert a row as PROCESSING with old claimed_at
        event_id = _insert_event(
            status="PROCESSING",
            claimed_at="2020-01-01T00:00:00+00:00",
            attempt_count=1,
        )

        # Worker B starts, runs recovery
        sender = DummySender(default_outcome=SendOutcome.SUCCESS)
        worker_b = OutboxWorker(
            dsn=DSN,
            sender=sender,
            stale_threshold_seconds=60,
        )
        recovered = worker_b._recover_stale_rows()
        assert recovered >= 1

        # Now the row is RETRYABLE, worker B can claim and process it
        row = _get_event(event_id)
        assert row["status"] == "RETRYABLE"

        # Process it
        worker_b._claim_and_process()

        row = _get_event(event_id)
        assert row["status"] == "SENT"
        assert row["sent_at"] is not None

    def test_15_max_attempts_crash_recovery(self):
        """Test 15: PROCESSING at max attempts -> stale recovery -> FAILED (no additional send)."""
        event_id = _insert_event(
            status="PROCESSING",
            claimed_at="2020-01-01T00:00:00+00:00",
            attempt_count=5,
        )

        sender = DummySender()
        worker = OutboxWorker(
            dsn=DSN,
            sender=sender,
            stale_threshold_seconds=60,
            retry_policy=RetryPolicy(max_attempts=5),
        )
        recovered = worker._recover_stale_rows()
        assert recovered >= 1

        row = _get_event(event_id)
        assert row["status"] == "FAILED"
        assert len(sender.sent) == 0


    def test_shutdown_safety_bounded_wait(self):
        """
        Verify that stop() returns within a bounded time (30s timeout) even if the 
        underlying synchronous executor (e.g., SMTP) is blocked for 60s.
        """
        worker = OutboxWorker(dsn=DSN, sender=DummySender(), batch_size=1)
        
        # We simulate a blocked executor by mocking _claim_and_process to sleep for 2 seconds, 
        # and we set the worker's shutdown timeout very low (1s) to test cancellation.
        import time
        
        def slow_claim():
            time.sleep(2)
            return 0
            
        worker._claim_and_process = slow_claim
        
        async def run_test():
            await worker.start()
            # Let it start
            await asyncio.sleep(0.1)
            
            # Monkeypatch wait_for so the test runs fast
            original_wait_for = asyncio.wait_for
            async def fast_wait_for(aw, timeout):
                return await original_wait_for(aw, timeout=0.2)
                
            asyncio.wait_for = fast_wait_for
            try:
                start_time = time.time()
                await worker.stop()
                end_time = time.time()
                
                # Should take approx 0.2s, not 2s
                assert end_time - start_time < 1.0
            finally:
                asyncio.wait_for = original_wait_for
                
        asyncio.run(run_test())


class TestOutboxWorkerEdgeCases:
    """Additional edge case tests."""

    def test_processing_row_not_stale_is_not_recovered(self):
        """A PROCESSING row with a recent claimed_at should NOT be recovered."""
        event_id = _insert_event(
            status="PROCESSING",
            claimed_at="NOW()",
            attempt_count=1,
        )

        sender = DummySender()
        worker = OutboxWorker(
            dsn=DSN,
            sender=sender,
            stale_threshold_seconds=300,  # 5 min threshold
        )
        recovered = worker._recover_stale_rows()

        # The row was just claimed, should not be recovered
        row = _get_event(event_id)
        assert row["status"] == "PROCESSING"

    def test_retryable_increments_attempt_count(self):
        """Claiming a RETRYABLE row increments attempt_count."""
        event_id = _insert_event(status="RETRYABLE", attempt_count=2)

        sender = DummySender(default_outcome=SendOutcome.SUCCESS)
        worker = OutboxWorker(dsn=DSN, sender=sender, batch_size=50)
        worker._claim_and_process()

        row = _get_event(event_id)
        assert row["status"] == "SENT"
        assert row["attempt_count"] == 3  # was 2, incremented by claim

    def test_multiple_rows_processed_in_one_batch(self):
        """Worker can process multiple rows in a single batch."""
        ids = [_insert_event() for _ in range(5)]

        sender = DummySender(default_outcome=SendOutcome.SUCCESS)
        worker = OutboxWorker(dsn=DSN, sender=sender, batch_size=10)
        processed = worker._claim_and_process()

        assert processed >= 5
        for eid in ids:
            row = _get_event(eid)
            assert row["status"] == "SENT"

    def test_mixed_outcomes_in_batch(self):
        """
        Test that the worker correctly handles different outcomes for different rows.
        We test each outcome individually to avoid order-dependency with residual rows.
        """
        # Drain residual claimable rows
        _claim_rows_sync(DSN, 1000)

        # Test success
        success_id = _insert_event()
        sender_s = DummySender(default_outcome=SendOutcome.SUCCESS)
        worker_s = OutboxWorker(dsn=DSN, sender=sender_s, batch_size=50)
        worker_s._claim_and_process()
        assert _get_event(success_id)["status"] == "SENT"

        # Test transient failure
        transient_id = _insert_event()
        sender_t = DummySender()
        sender_t.enqueue_outcome(
            SendResult(outcome=SendOutcome.TRANSIENT_FAILURE, error_message="timeout")
        )
        worker_t = OutboxWorker(dsn=DSN, sender=sender_t, batch_size=50)
        worker_t._claim_and_process()
        assert _get_event(transient_id)["status"] == "RETRYABLE"

        # Test terminal failure
        terminal_id = _insert_event()
        sender_f = DummySender()
        sender_f.enqueue_outcome(
            SendResult(
                outcome=SendOutcome.PERMANENT_RECIPIENT_FAILURE,
                error_message="550 invalid",
            )
        )
        worker_f = OutboxWorker(dsn=DSN, sender=sender_f, batch_size=50)
        worker_f._claim_and_process()
        assert _get_event(terminal_id)["status"] == "FAILED"

    def test_empty_poll_processes_nothing(self):
        """Worker handles an empty poll gracefully."""
        # Clear all eligible rows first by claiming them
        _claim_rows_sync(DSN, 1000)

        sender = DummySender(default_outcome=SendOutcome.SUCCESS)
        worker = OutboxWorker(dsn=DSN, sender=sender, batch_size=10)

        # Insert only SENT/FAILED rows
        _insert_event(status="SENT")
        _insert_event(status="FAILED")

        processed = worker._claim_and_process()
        assert processed == 0
        assert len(sender.sent) == 0


    def test_rendering_boundary_success(self):
        """Test successful rendering of known template."""
        event_id = _insert_event(notification_type="TICKET_CREATED", payload={"ticket_id": 9999, "message": "Test rendering"})
        worker = OutboxWorker(dsn=DSN, sender=DummySender(default_outcome=SendOutcome.SUCCESS), batch_size=10)
        processed = worker._claim_and_process()
        
        assert processed == 1
        assert _get_event(event_id)["status"] == "SENT"
        
        sent = worker.sender.sent[0]
        assert "Update on Ticket #9999" in sent["subject"]
        assert "Test rendering" in sent["html_body"]

    def test_rendering_boundary_unknown_type(self):
        """Test that unknown notification type results in deterministic terminal failure."""
        event_id = _insert_event(notification_type="UNKNOWN_WEIRD_TYPE", payload={"ticket_id": 123})
        worker = OutboxWorker(dsn=DSN, sender=DummySender(default_outcome=SendOutcome.SUCCESS), batch_size=10)
        worker._claim_and_process()
        
        row = _get_event(event_id)
        assert row["status"] == "FAILED"
        assert "Rendering failed" in row["last_error"]
        assert "Unknown notification type" in row["last_error"]

    def test_rendering_boundary_malformed_payload(self):
        """Test that missing required payload fields results in deterministic terminal failure."""
        # TICKET_CREATED requires 'ticket_id'
        event_id = _insert_event(notification_type="TICKET_CREATED", payload={"wrong_field": "oops"})
        worker = OutboxWorker(dsn=DSN, sender=DummySender(default_outcome=SendOutcome.SUCCESS), batch_size=10)
        worker._claim_and_process()
        
        row = _get_event(event_id)
        assert row["status"] == "FAILED"
        assert "Rendering failed" in row["last_error"]
        assert "Missing required field" in row["last_error"]


class TestDummySender:
    """Unit tests for the DummySender test double."""

    def test_default_outcome(self):
        sender = DummySender(default_outcome=SendOutcome.SUCCESS)
        result = sender.send("a@b.com", "subj", "<p>hi</p>")
        assert result.outcome == SendOutcome.SUCCESS
        assert len(sender.sent) == 1

    def test_enqueued_outcomes_fifo(self):
        sender = DummySender()
        sender.enqueue_outcome(SendResult(outcome=SendOutcome.TRANSIENT_FAILURE, error_message="e1"))
        sender.enqueue_outcome(SendResult(outcome=SendOutcome.SUCCESS))

        r1 = sender.send("a@b.com", "s1", "b1")
        r2 = sender.send("a@b.com", "s2", "b2")

        assert r1.outcome == SendOutcome.TRANSIENT_FAILURE
        assert r2.outcome == SendOutcome.SUCCESS

    def test_records_all_sends(self):
        sender = DummySender()
        sender.send("x@y.com", "Hello", "<p>body</p>")
        assert sender.sent[0]["to"] == "x@y.com"
        assert sender.sent[0]["subject"] == "Hello"
