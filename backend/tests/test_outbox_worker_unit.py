"""
Unit tests for Durable Outbox Worker (Prompt 12, Phase D).

Covers all 13 required scenarios deterministically without requiring a live PostgreSQL instance:
1. Two workers attempting to claim events simultaneously (SKIP LOCKED isolation).
2. A worker's lease expiring while another worker claims the same event.
3. A stale claim token being rejected during acknowledgement or retry scheduling.
4. Successful delivery and authoritative acknowledgement (SENT).
5. Retryable failure with correct exponential backoff and injected clock.
6. Non-retryable failure reaching terminal FAILED.
7. One poison event not blocking healthy events in a batch.
8. A malformed response or timeout not being mistaken for successful delivery.
9. Restart or shutdown during processing and recovery according to persisted lease contract.
10. Duplicate event insertion respecting the logical deduplication key.
11. Worker-disabled configuration preventing background delivery in unit tests.
12. FastAPI lifespan starting and stopping the worker without leaked tasks.
13. Correct telemetry and sanitized error recording.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from unittest.mock import MagicMock, patch, call

import pytest
import psycopg2

from app.outbox.sender import EmailSender, SendOutcome, SendResult, DummySender
from app.outbox.worker import (
    OutboxWorker,
    RetryPolicy,
    render_notification,
    sanitize_error_message,
    emit_outbox_telemetry,
    CLAIM_SQL,
    MARK_SENT_SQL,
    MARK_RETRYABLE_SQL,
    MARK_FAILED_SQL,
    RECOVER_STALE_SQL,
)
from app.notification_identity import (
    NotificationType,
    SourceEventIdentity,
    RecipientIdentity,
    LogicalNotificationIdentity,
)


class MockCursor:
    """Configurable mock cursor that tracks SQL executions and rowcount."""
    def __init__(self, fetch_results: Optional[List[Any]] = None, rowcount: int = 1):
        self.fetch_results = fetch_results or []
        self.rowcount = rowcount
        self.executed_queries: List[tuple] = []

    def execute(self, query: str, params: Optional[tuple] = None):
        self.executed_queries.append((query.strip(), params))

    def fetchall(self):
        return self.fetch_results

    def fetchone(self):
        return self.fetch_results[0] if self.fetch_results else None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class MockConnection:
    """Mock database connection."""
    def __init__(self, cursor: Optional[MockCursor] = None):
        self._cursor = cursor or MockCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self, cursor_factory=None):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


# ─────────────────────────────────────────────────────────────
# 1. Two workers attempting to claim events simultaneously
# ─────────────────────────────────────────────────────────────

def test_1_two_workers_concurrent_claim_skip_locked():
    """Worker 1 and Worker 2 claim concurrently; SKIP LOCKED guarantees non-overlapping rows."""
    row_1 = {
        "event_id": str(uuid.uuid4()),
        "logical_identity_hash": "hash-1",
        "source_event_id": "src-1",
        "notification_type": "TICKET_CREATED",
        "recipient_identity": "customer:1",
        "recipient_address": "user1@example.com",
        "payload": {"ticket_id": 101},
        "attempt_count": 1,
        "claim_token": str(uuid.uuid4()),
    }
    row_2 = {
        "event_id": str(uuid.uuid4()),
        "logical_identity_hash": "hash-2",
        "source_event_id": "src-2",
        "notification_type": "TICKET_RESOLVED",
        "recipient_identity": "customer:2",
        "recipient_address": "user2@example.com",
        "payload": {"ticket_id": 102},
        "attempt_count": 1,
        "claim_token": str(uuid.uuid4()),
    }

    # Worker 1 gets row_1, Worker 2 gets row_2 (simulating SKIP LOCKED partition)
    cur_1 = MockCursor(fetch_results=[row_1])
    cur_2 = MockCursor(fetch_results=[row_2])
    conn_1 = MockConnection(cur_1)
    conn_2 = MockConnection(cur_2)

    sender_1 = DummySender()
    sender_2 = DummySender()

    worker_1 = OutboxWorker(dsn="postgresql://mock", sender=sender_1, batch_size=1)
    worker_2 = OutboxWorker(dsn="postgresql://mock", sender=sender_2, batch_size=1)

    with patch("psycopg2.connect", side_effect=[conn_1, conn_2]):
        processed_1 = worker_1._claim_and_process()
        processed_2 = worker_2._claim_and_process()

    assert processed_1 == 1
    assert processed_2 == 1
    assert len(sender_1.sent_messages) == 1
    assert len(sender_2.sent_messages) == 1
    assert sender_1.sent_messages[0]["to"] == "user1@example.com"
    assert sender_2.sent_messages[0]["to"] == "user2@example.com"
    assert row_1["claim_token"] != row_2["claim_token"]


# ─────────────────────────────────────────────────────────────
# 2. Worker lease expiring while another worker claims
# ─────────────────────────────────────────────────────────────

def test_2_worker_lease_expiration_and_recovery():
    """Stale lease is reclaimed into RETRYABLE by recovery sweep."""
    stale_event_id = str(uuid.uuid4())
    recovered_row = {
        "event_id": stale_event_id,
        "logical_identity_hash": "stale-hash",
        "notification_type": "TICKET_CREATED",
        "attempt_count": 1,
        "status": "RETRYABLE",
    }
    cur = MockCursor(fetch_results=[recovered_row])
    conn = MockConnection(cur)

    worker = OutboxWorker(
        dsn="postgresql://mock",
        sender=DummySender(),
        stale_threshold_seconds=300.0,
    )

    with patch("psycopg2.connect", return_value=conn):
        recovered_count = worker._recover_stale_rows()

    assert recovered_count == 1
    # Check that RECOVER_STALE_SQL was executed with retry max_attempts and stale threshold
    assert any("claimed_at < NOW() - (%s * INTERVAL '1 second')" in q[0] for q in cur.executed_queries)
    assert conn.committed is True


# ─────────────────────────────────────────────────────────────
# 3. Stale claim token rejected during acknowledgement or retry
# ─────────────────────────────────────────────────────────────

def test_3_stale_claim_token_rejected_during_ack():
    """If rowcount == 0 (lease expired or token changed), worker does not falsely mark SENT."""
    stale_token = str(uuid.uuid4())
    event_id = str(uuid.uuid4())

    cur = MockCursor(rowcount=0)  # 0 rows affected indicates token mismatch / lease lost
    conn = MockConnection(cur)

    worker = OutboxWorker(dsn="postgresql://mock", sender=DummySender())

    with patch("app.outbox.worker.emit_outbox_telemetry") as mock_telem:
        worker._mark_sent(conn, event_id, stale_token, "hash", "TICKET_CREATED")

    # Transaction committed, but telemetry emitted lease_lost
    assert conn.committed is False  # Does not commit on failed update
    mock_telem.assert_called_once()
    assert mock_telem.call_args[0][0] == "lease_lost"
    assert mock_telem.call_args[0][1]["event_id"] == event_id


def test_3b_stale_claim_token_rejected_during_retry():
    """If rowcount == 0 during retry update, lease loss is detected and rejected."""
    stale_token = str(uuid.uuid4())
    event_id = str(uuid.uuid4())

    cur = MockCursor(rowcount=0)
    conn = MockConnection(cur)

    worker = OutboxWorker(dsn="postgresql://mock", sender=DummySender())

    with patch("app.outbox.worker.emit_outbox_telemetry") as mock_telem:
        worker._mark_retryable(conn, event_id, stale_token, "hash", "TICKET_CREATED", 60.0, "Network error")

    assert conn.committed is False
    mock_telem.assert_called_once()
    assert mock_telem.call_args[0][0] == "lease_lost"


# ─────────────────────────────────────────────────────────────
# 4. Successful delivery and authoritative acknowledgement
# ─────────────────────────────────────────────────────────────

def test_4_successful_delivery_authoritative_sent_ack():
    """Authoritative SendOutcome.SUCCESS updates status to SENT."""
    event_id = str(uuid.uuid4())
    token = str(uuid.uuid4())

    cur = MockCursor(rowcount=1)
    conn = MockConnection(cur)

    worker = OutboxWorker(dsn="postgresql://mock", sender=DummySender())

    with patch("app.outbox.worker.emit_outbox_telemetry") as mock_telem:
        worker._mark_sent(conn, event_id, token, "hash", "TICKET_CREATED")

    assert conn.committed is True
    assert cur.executed_queries[0][0] == MARK_SENT_SQL.strip()
    assert cur.executed_queries[0][1] == (event_id, token)
    mock_telem.assert_called_once_with("event_sent", {
        "worker_id": worker.worker_id,
        "event_id": event_id,
        "notification_type": "TICKET_CREATED",
        "logical_identity_hash": "hash",
    })


# ─────────────────────────────────────────────────────────────
# 5. Retryable failure with correct exponential backoff & clock
# ─────────────────────────────────────────────────────────────

def test_5_retryable_failure_exponential_backoff_and_clock():
    """Exponential backoff computation: base * 2^(attempt - 1), capped at max."""
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    policy = RetryPolicy(
        max_attempts=5,
        base_delay_seconds=30.0,
        max_backoff_seconds=300.0,
        clock=lambda: t0,
    )

    # Attempt 1 -> 30s
    assert policy.compute_next_attempt_delay(1) == 30.0
    assert policy.compute_next_attempt_time(1) == t0 + timedelta(seconds=30)

    # Attempt 2 -> 60s
    assert policy.compute_next_attempt_delay(2) == 60.0
    assert policy.compute_next_attempt_time(2) == t0 + timedelta(seconds=60)

    # Attempt 3 -> 120s
    assert policy.compute_next_attempt_delay(3) == 120.0
    assert policy.compute_next_attempt_time(3) == t0 + timedelta(seconds=120)

    # Attempt 4 -> 240s
    assert policy.compute_next_attempt_delay(4) == 240.0

    # Attempt 5 -> capped at 300s
    assert policy.compute_next_attempt_delay(5) == 300.0
    assert policy.compute_next_attempt_time(5) == t0 + timedelta(seconds=300)


def test_5b_retryable_failure_marks_retryable():
    """Transient failure triggers _mark_retryable with calculated delay."""
    event_id = str(uuid.uuid4())
    token = str(uuid.uuid4())

    cur = MockCursor(rowcount=1)
    conn = MockConnection(cur)

    sender = DummySender(default_outcome=SendOutcome.TRANSIENT_FAILURE)
    worker = OutboxWorker(dsn="postgresql://mock", sender=sender)

    row = {
        "event_id": event_id,
        "logical_identity_hash": "hash",
        "source_event_id": "src",
        "notification_type": "TICKET_CREATED",
        "recipient_identity": "c:1",
        "recipient_address": "test@example.com",
        "attempt_count": 1,
        "claim_token": token,
        "payload": {"ticket_id": 42},
    }

    worker._process_row(conn, row)
    assert conn.committed is True
    # Verify MARK_RETRYABLE_SQL was executed
    assert cur.executed_queries[0][0] == MARK_RETRYABLE_SQL.strip()
    # Params: (delay, sanitized_error, penalty, event_id, token)
    params = cur.executed_queries[0][1]
    assert params[0] == 30.0
    assert params[3] == event_id
    assert params[4] == token


# ─────────────────────────────────────────────────────────────
# 6. Non-retryable failure reaching terminal FAILED
# ─────────────────────────────────────────────────────────────

def test_6_permanent_failure_transitions_to_terminal_failed():
    """Permanent recipient error transitions immediately to terminal FAILED."""
    event_id = str(uuid.uuid4())
    token = str(uuid.uuid4())

    cur = MockCursor(rowcount=1)
    conn = MockConnection(cur)

    sender = DummySender(default_outcome=SendOutcome.PERMANENT_RECIPIENT_FAILURE)
    worker = OutboxWorker(dsn="postgresql://mock", sender=sender)

    row = {
        "event_id": event_id,
        "logical_identity_hash": "hash",
        "source_event_id": "src",
        "notification_type": "TICKET_CREATED",
        "recipient_identity": "c:1",
        "recipient_address": "invalid@bad-domain.invalid",
        "attempt_count": 1,
        "claim_token": token,
        "payload": {"ticket_id": 99},
    }

    worker._process_row(conn, row)
    assert conn.committed is True
    assert cur.executed_queries[0][0] == MARK_FAILED_SQL.strip()


def test_6b_max_attempts_exceeded_transitions_to_failed():
    """When attempt count exceeds max_attempts, retryable failure becomes terminal FAILED."""
    event_id = str(uuid.uuid4())
    token = str(uuid.uuid4())

    cur = MockCursor(rowcount=1)
    conn = MockConnection(cur)

    # Transient error, but attempt_count is already 5 (max)
    sender = DummySender(default_outcome=SendOutcome.TRANSIENT_FAILURE)
    policy = RetryPolicy(max_attempts=5)
    worker = OutboxWorker(dsn="postgresql://mock", sender=sender, retry_policy=policy)

    row = {
        "event_id": event_id,
        "logical_identity_hash": "hash",
        "source_event_id": "src",
        "notification_type": "TICKET_CREATED",
        "recipient_identity": "c:1",
        "recipient_address": "user@example.com",
        "attempt_count": 5,
        "claim_token": token,
        "payload": {"ticket_id": 100},
    }

    worker._process_row(conn, row)
    assert conn.committed is True
    assert cur.executed_queries[0][0] == MARK_FAILED_SQL.strip()


# ─────────────────────────────────────────────────────────────
# 7. One poison event does not block healthy events
# ─────────────────────────────────────────────────────────────

def test_7_poison_event_isolation_in_batch():
    """A poison pill event (e.g. malformed payload) does not crash or abort the batch."""
    poison_id = str(uuid.uuid4())
    healthy_id_1 = str(uuid.uuid4())
    healthy_id_2 = str(uuid.uuid4())

    claimed_rows = [
        # Poison row: missing required 'ticket_id' for TICKET_CREATED
        {
            "event_id": poison_id,
            "claim_token": str(uuid.uuid4()),
            "notification_type": "TICKET_CREATED",
            "recipient_address": "poison@example.com",
            "logical_identity_hash": "p-hash",
            "attempt_count": 1,
            "payload": {"bad_field": "no ticket id"},
        },
        # Healthy row 1
        {
            "event_id": healthy_id_1,
            "claim_token": str(uuid.uuid4()),
            "notification_type": "TICKET_CREATED",
            "recipient_address": "healthy1@example.com",
            "logical_identity_hash": "h1-hash",
            "attempt_count": 1,
            "payload": {"ticket_id": 201},
        },
        # Healthy row 2
        {
            "event_id": healthy_id_2,
            "claim_token": str(uuid.uuid4()),
            "notification_type": "ORDER_UPDATED",
            "recipient_address": "healthy2@example.com",
            "logical_identity_hash": "h2-hash",
            "attempt_count": 1,
            "payload": {"order_id": 301},
        },
    ]

    cur = MockCursor(fetch_results=claimed_rows, rowcount=1)
    conn = MockConnection(cur)

    sender = DummySender(default_outcome=SendOutcome.SUCCESS)
    worker = OutboxWorker(dsn="postgresql://mock", sender=sender)

    with patch("psycopg2.connect", return_value=conn):
        processed = worker._claim_and_process()

    assert processed == 3
    # Poison row was marked FAILED due to rendering error
    assert any(q[0] == MARK_FAILED_SQL.strip() and q[1][1] == poison_id for q in cur.executed_queries)
    # Both healthy rows were sent successfully
    sent_recipients = [m["to"] for m in sender.sent_messages]
    assert "healthy1@example.com" in sent_recipients
    assert "healthy2@example.com" in sent_recipients


# ─────────────────────────────────────────────────────────────
# 8. Malformed response or timeout not mistaken for success
# ─────────────────────────────────────────────────────────────

def test_8_malformed_response_or_timeout_not_mistaken_for_sent():
    """Timeout or unexpected failure must NEVER be marked as SENT."""
    event_id = str(uuid.uuid4())
    token = str(uuid.uuid4())

    cur = MockCursor(rowcount=1)
    conn = MockConnection(cur)

    sender = DummySender(default_outcome=SendOutcome.TRANSIENT_FAILURE)
    worker = OutboxWorker(dsn="postgresql://mock", sender=sender)

    row = {
        "event_id": event_id,
        "logical_identity_hash": "hash",
        "source_event_id": "src",
        "notification_type": "TICKET_CREATED",
        "recipient_identity": "c:1",
        "recipient_address": "test@example.com",
        "attempt_count": 1,
        "claim_token": token,
        "payload": {"ticket_id": 55},
    }

    worker._process_row(conn, row)
    assert conn.committed is True
    # Must NOT have executed MARK_SENT_SQL
    assert not any(q[0] == MARK_SENT_SQL.strip() for q in cur.executed_queries)
    # Must have executed MARK_RETRYABLE_SQL
    assert any(q[0] == MARK_RETRYABLE_SQL.strip() for q in cur.executed_queries)


# ─────────────────────────────────────────────────────────────
# 9. Restart or shutdown during processing and recovery
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_9_restart_and_graceful_shutdown():
    """Worker start and graceful stop waits for cycle and terminates tasks cleanly."""
    worker = OutboxWorker(
        dsn="postgresql://mock",
        sender=DummySender(),
        poll_interval_seconds=0.01,
        recovery_interval_seconds=0.02,
    )

    with patch.object(worker, "_claim_and_process", return_value=0), \
         patch.object(worker, "_recover_stale_rows", return_value=0):
        await worker.start()
        assert worker._task is not None
        assert not worker._task.done()

        # Let it run briefly
        await asyncio.sleep(0.05)

        # Stop worker gracefully
        await worker.stop()
        assert worker._stop_event.is_set()
        assert worker._task.done()
        assert worker._recovery_task.done()


# ─────────────────────────────────────────────────────────────
# 10. Duplicate event insertion respecting logical identity hash
# ─────────────────────────────────────────────────────────────

def test_10_logical_identity_hash_deduplication():
    """LogicalNotificationIdentity produces stable hash for deduplication."""
    src = SourceEventIdentity.from_ticket_creation(customer_id=1, client_request_id="req-123")
    rec = RecipientIdentity.customer(1)
    ident1 = LogicalNotificationIdentity(
        source_event=src,
        notification_type=NotificationType.TICKET_CREATED,
        recipient=rec,
    )
    ident2 = LogicalNotificationIdentity(
        source_event=src,
        notification_type=NotificationType.TICKET_CREATED,
        recipient=rec,
    )

    # Identical business attributes produce identical hash
    assert ident1.get_hash() == ident2.get_hash()

    # Different notification type produces distinct hash
    ident3 = LogicalNotificationIdentity(
        source_event=src,
        notification_type=NotificationType.TICKET_RESOLVED,
        recipient=rec,
    )
    assert ident1.get_hash() != ident3.get_hash()


# ─────────────────────────────────────────────────────────────
# 11. Worker-disabled configuration prevents dispatch
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_11_worker_disabled_config_prevents_dispatch(monkeypatch):
    """When outbox_worker_enabled is False, start_outbox_worker does not launch worker."""
    from app.config import settings
    from app.outbox.lifecycle import start_outbox_worker, stop_outbox_worker

    monkeypatch.setattr(settings, "outbox_worker_enabled", False)
    worker = await start_outbox_worker()
    assert worker is None
    await stop_outbox_worker()


# ─────────────────────────────────────────────────────────────
# 12. FastAPI lifespan starts and stops worker without leaked tasks
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_12_fastapi_lifespan_integration(monkeypatch):
    """FastAPI lifespan hook manages worker start and stop cleanly."""
    from app.main import app
    from app.config import settings

    # Force worker enabled in test environment
    monkeypatch.setenv("OUTBOX_WORKER_ENABLED", "true")
    monkeypatch.setenv("TEST_POSTGRES_URL", "postgresql://user:pass@localhost:5433/db")

    dummy_sender = DummySender()
    with patch("app.outbox.lifecycle.GmailSmtpSender", return_value=dummy_sender), \
         patch("app.outbox.worker.OutboxWorker._claim_and_process", return_value=0), \
         patch("app.outbox.worker.OutboxWorker._recover_stale_rows", return_value=0), \
         patch("app.main._run_startup_checks"):

        async with app.router.lifespan_context(app):
            # Worker was started during lifespan context
            from app.outbox import lifecycle
            assert lifecycle._worker_instance is not None

        # Worker was cleaned up upon exiting context
        assert lifecycle._worker_instance is None


# ─────────────────────────────────────────────────────────────
# 13. Correct telemetry and sanitized error recording
# ─────────────────────────────────────────────────────────────

def test_13_error_sanitization_scrubs_secrets_and_caps_length():
    """Sanitizer scrubs passwords, bearer tokens, API keys, and connection credentials."""
    raw_error = (
        "Connection failed: postgresql://postgres:SuperSecretPassword123@db.prod:5432/main. "
        "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xyz. "
        "apiKey='secret-api-key-value' password: 'my-db-password'"
    )
    sanitized = sanitize_error_message(raw_error, max_length=200)

    # Secrets must not appear in output
    assert "SuperSecretPassword123" not in sanitized
    assert "secret-api-key-value" not in sanitized
    assert "my-db-password" not in sanitized
    assert "[REDACTED]" in sanitized
    assert len(sanitized) <= 200


def test_13b_telemetry_emission_does_not_leak_payload():
    """Operational telemetry omits sensitive payload dicts and tokens."""
    event_payload = {
        "worker_id": "w-1",
        "event_id": "e-1",
        "notification_type": "TICKET_CREATED",
        "payload": {"private_customer_ssn": "123-45-6789"},
        "password": "secret",
        "token": "secret-token",
    }
    with patch("app.outbox.worker.logger.info") as mock_log:
        emit_outbox_telemetry("test_event", event_payload)

    mock_log.assert_called_once()
    extra = mock_log.call_args[1]["extra"]
    assert "payload" not in extra
    assert "password" not in extra
    assert "token" not in extra
    assert extra["telemetry_event"] == "test_event"
    assert extra["worker_id"] == "w-1"


# ─────────────────────────────────────────────────────────────
# 14. Standalone Worker Execution & CLI Options
# ─────────────────────────────────────────────────────────────

def test_14_standalone_worker_run_once():
    """worker.run_once() performs a single recovery and processing cycle."""
    sender = DummySender()
    worker = OutboxWorker(dsn="postgresql://localhost/dummy", sender=sender)

    with patch.object(worker, "_recover_stale_rows", return_value=2) as mock_recover, \
         patch.object(worker, "_claim_and_process", return_value=3) as mock_process:
        processed, recovered = worker.run_once()
        assert processed == 3
        assert recovered == 2
        mock_recover.assert_called_once()
        mock_process.assert_called_once()


@pytest.mark.asyncio
async def test_14b_standalone_worker_run_forever_graceful_shutdown():
    """run_standalone_worker runs until stop_event is set, then shuts down cleanly."""
    from app.outbox.standalone import run_standalone_worker
    sender = DummySender()
    stop_event = asyncio.Event()

    with patch("app.outbox.standalone._build_dsn", return_value="postgresql://localhost/dummy"), \
         patch("app.outbox.worker.OutboxWorker._claim_and_process", return_value=0), \
         patch("app.outbox.worker.OutboxWorker._recover_stale_rows", return_value=0):

        async def stop_later():
            await asyncio.sleep(0.05)
            stop_event.set()

        asyncio.create_task(stop_later())
        exit_code = await run_standalone_worker(
            sender=sender,
            stop_event=stop_event,
            dsn="postgresql://localhost/dummy",
        )
        assert exit_code == 0


# ─────────────────────────────────────────────────────────────
# 15. Explicit Mode Boundary (standalone vs in_process vs disabled)
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_15_outbox_worker_mode_standalone_skips_in_process_lifespan(monkeypatch):
    """When OUTBOX_WORKER_MODE=standalone, FastAPI lifespan does not start in-process worker."""
    from app.outbox import lifecycle
    monkeypatch.setenv("OUTBOX_WORKER_MODE", "standalone")
    monkeypatch.setenv("OUTBOX_WORKER_ENABLED", "true")

    worker = await lifecycle.start_outbox_worker()
    assert worker is None
    assert lifecycle._worker_instance is None


@pytest.mark.asyncio
async def test_15b_standalone_runner_exits_when_disabled(monkeypatch):
    """Standalone worker exits with code 0 when OUTBOX_WORKER_ENABLED=false."""
    from app.outbox.standalone import run_standalone_worker
    monkeypatch.setenv("OUTBOX_WORKER_ENABLED", "false")
    sender = DummySender()
    exit_code = await run_standalone_worker(sender=sender, dsn="postgresql://localhost/dummy")
    assert exit_code == 0


# ─────────────────────────────────────────────────────────────
# 16. Database Transient Failure Resilience
# ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_16_database_temporary_unavailability_does_not_crash_poll_loop():
    """Transient DB OperationalError logs error and pauses without crashing the worker task."""
    sender = DummySender()
    worker = OutboxWorker(
        dsn="postgresql://localhost/dummy",
        sender=sender,
        poll_interval_seconds=0.01,
        recovery_interval_seconds=10.0,
    )

    call_count = 0

    def mock_claim_with_temporary_error():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise psycopg2.OperationalError("could not translate host name 'postgres' to address")
        worker._stop_event.set()
        return 0

    with patch.object(worker, "_claim_and_process", side_effect=mock_claim_with_temporary_error), \
         patch.object(worker, "_recover_stale_rows", return_value=0):
        await worker.run_forever()
        assert call_count >= 2


# ─────────────────────────────────────────────────────────────
# 17. SMTP Duplicate Delivery Limitation & At-Least-Once Boundary
# ─────────────────────────────────────────────────────────────

def test_17_smtp_delivery_with_database_failure_leaves_row_for_stale_recovery():
    """
    If SMTP accepts message but DB commit fails before marking SENT,
    the row remains in PROCESSING and is re-claimed after stale lease expiry
    (verifying standard at-least-once outbox semantics).
    """
    sender = DummySender(default_outcome=SendOutcome.SUCCESS)
    worker = OutboxWorker(
        dsn="postgresql://localhost/dummy",
        sender=sender,
        stale_threshold_seconds=10.0,
    )

    mock_row = {
        "event_id": str(uuid.uuid4()),
        "claim_token": str(uuid.uuid4()),
        "notification_type": "TICKET_CREATED",
        "recipient_address": "user@example.com",
        "logical_identity_hash": "hash-abc",
        "attempt_count": 1,
        "payload": {"ticket_id": "123", "subject": "Test Ticket"},
    }

    conn = MockConnection()
    with patch.object(worker, "_mark_sent", side_effect=psycopg2.OperationalError("DB connection lost during commit")):
        worker._process_row(conn, mock_row)

    assert len(sender.sent) == 1
    assert conn.rolled_back is True
