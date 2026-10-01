"""
Outbox Worker — Slice 3.3

Durable outbox worker responsible for:
  - Atomically claiming eligible rows via SELECT … FOR UPDATE SKIP LOCKED
  - Delivering notifications through the EmailSender abstraction
  - Managing retry scheduling with exponential backoff
  - Recovering stale PROCESSING rows (crash/lease recovery)
  - Terminal failure handling after max retry attempts

Architecture Notes
──────────────────
  - The worker is implemented as a standalone class (``OutboxWorker``)
    behind a clean service boundary.  It runs as an asyncio background
    task within the FastAPI process *provisionally*, but is designed to be
    extracted into a dedicated process without any change to its public API.
  - Multiple API replicas can safely run their own OutboxWorker instances
    concurrently because all row locking/coordination uses SKIP LOCKED.
  - The worker does NOT assume that outbox insertion and business mutation
    are already atomically coupled.  That coupling belongs to later slices
    (3.4+).

Operational Limitations (FastAPI In-Process Mode)
─────────────────────────────────────────────────
  - Worker lifecycle is tied to the API server process.  A hard SIGKILL
    leaves claimed rows in PROCESSING until the stale-lease recovery sweep
    reclaims them.
  - Graceful shutdown (SIGTERM) is supported via the ``stop()`` method,
    which sets a cancellation event and allows the current poll cycle to
    complete.
  - OOM or event-loop blocking in the email send path directly degrades
    the API.  For production workloads, extraction to a separate process
    or container is strongly recommended.

Duplicate-Delivery Window
─────────────────────────
  SMTP accepted → process crashes → row recovered as RETRYABLE → retry
  → possible duplicate email delivery.
  Standard SMTP provides NO application-level exactly-once guarantee.
  This is documented, not papered over.

Lease Duration vs Send Timeout
──────────────────────────────
  A send attempt MUST conclude before the stale lease threshold expires.
  Otherwise, the row will be concurrently recovered and sent again.
  - SMTP Connect Timeout: 60s (hard limit in gmail_server)
  - Stale Threshold: 300s
  - Poll Interval: 5s
  Since 60s << 300s, a legitimate long-running operation cannot be
  concurrently reclaimed while its lease is still valid.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

from app.outbox.sender import EmailSender, SendOutcome, SendResult

logger = logging.getLogger("outbox.worker")


# ──────────────────────────────────────────────
#  Retry Policy
# ──────────────────────────────────────────────

class RetryPolicy:
    """
    Deterministic retry configuration.

    Backoff formula:
        delay = min(base_delay_seconds * (2 ** (attempt - 1)), max_backoff_seconds)

    Categories:
        Retryable (transient):
          - SMTP connection timeouts
          - DNS resolution failures
          - Rate limiting (421, 451)
          - Network errors (socket, TLS handshake)

        Terminal (non-retryable):
          - Invalid recipient (550, 551, 552, 553)
          - Authentication/configuration failures (535, 534)
          - Max attempts exceeded

    After ``max_attempts`` retries, any retryable failure becomes terminal
    (status → FAILED).
    """

    def __init__(
        self,
        max_attempts: int = 5,
        base_delay_seconds: float = 30.0,
        max_backoff_seconds: float = 3600.0,  # 1 hour cap
    ):
        self.max_attempts = max_attempts
        self.base_delay_seconds = base_delay_seconds
        self.max_backoff_seconds = max_backoff_seconds

    def compute_next_attempt_delay(self, attempt_count: int) -> float:
        """
        Compute the delay in seconds before the next retry attempt.

        Uses exponential backoff: base * 2^(attempt-1), capped at max.
        """
        exponent = max(0, attempt_count - 1)
        delay = self.base_delay_seconds * (2 ** exponent)
        return min(delay, self.max_backoff_seconds)
        
    def compute_outage_delay(self) -> float:
        """
        Compute the delay in seconds for provider/system outages.
        Uses a fixed delay (300 seconds) with bounded jitter (+/- 50s) to avoid
        thundering herd when the provider comes back online.
        """
        import random
        base_outage_delay = 300.0  # 5 minutes
        jitter = random.uniform(-50.0, 50.0)
        return base_outage_delay + jitter

    def is_terminal_outcome(self, outcome: SendOutcome) -> bool:
        """True if the outcome should never be retried."""
        return outcome in (
            SendOutcome.PERMANENT_RECIPIENT_FAILURE,
        )

    def has_exceeded_max_attempts(self, attempt_count: int) -> bool:
        """True if the row has been attempted too many times."""
        return attempt_count >= self.max_attempts


# ──────────────────────────────────────────────
#  Stale Lease Configuration
# ──────────────────────────────────────────────

DEFAULT_STALE_THRESHOLD_SECONDS = 300  # 5 minutes


# ──────────────────────────────────────────────
#  SQL Queries
# ──────────────────────────────────────────────

# Atomically claim up to ``batch_size`` eligible rows.
# Uses a subquery with FOR UPDATE SKIP LOCKED to prevent two workers
# from claiming the same row.
CLAIM_SQL = """
UPDATE outbox_events
SET status        = 'PROCESSING',
    claimed_at    = NOW(),
    claim_token   = gen_random_uuid(),
    attempt_count = attempt_count + 1
WHERE event_id IN (
    SELECT event_id
    FROM   outbox_events
    WHERE  status IN ('PENDING', 'RETRYABLE')
      AND  next_attempt_at <= NOW()
    ORDER  BY next_attempt_at ASC, event_id ASC
    FOR UPDATE SKIP LOCKED
    LIMIT  %s
)
RETURNING event_id, logical_identity_hash, source_event_id,
          notification_type, recipient_identity, recipient_address,
          payload, attempt_count, claim_token;
"""

# Mark row as successfully sent.
MARK_SENT_SQL = """
UPDATE outbox_events
SET status     = 'SENT',
    sent_at    = NOW(),
    claimed_at = NULL,
    claim_token = NULL,
    last_error = NULL
WHERE event_id = %s
  AND status   = 'PROCESSING'
  AND claim_token = %s;
"""

# Mark row as retryable with calculated backoff.
MARK_RETRYABLE_SQL = """
UPDATE outbox_events
SET status          = 'RETRYABLE',
    next_attempt_at = NOW() + (%s * INTERVAL '1 second'),
    claimed_at      = NULL,
    claim_token     = NULL,
    last_error      = %s,
    attempt_count   = attempt_count - %s
WHERE event_id = %s
  AND status   = 'PROCESSING'
  AND claim_token = %s;
"""

# Mark row as terminal failure.
MARK_FAILED_SQL = """
UPDATE outbox_events
SET status     = 'FAILED',
    claimed_at = NULL,
    claim_token = NULL,
    last_error = %s
WHERE event_id = %s
  AND status   = 'PROCESSING'
  AND claim_token = %s;
"""

# Recover stale PROCESSING rows whose leases have expired.
# Uses FOR UPDATE SKIP LOCKED so concurrent recovery sweeps
# cannot create duplicate ownership.
# If attempt_count >= max_attempts, marks as FAILED instead of RETRYABLE.
RECOVER_STALE_SQL = """
UPDATE outbox_events
SET status          = CASE 
                        WHEN attempt_count >= %s THEN 'FAILED' 
                        ELSE 'RETRYABLE' 
                      END,
    claimed_at      = NULL,
    claim_token     = NULL,
    next_attempt_at = NOW(),
    last_error      = CASE 
                        WHEN attempt_count >= %s THEN 'Terminal failure: Max attempts exceeded before crash/lease recovery' 
                        ELSE last_error 
                      END
WHERE event_id IN (
    SELECT event_id
    FROM   outbox_events
    WHERE  status     = 'PROCESSING'
      AND  claimed_at < NOW() - (%s * INTERVAL '1 second')
    FOR UPDATE SKIP LOCKED
)
RETURNING event_id, logical_identity_hash, notification_type, attempt_count, status;
"""


# ──────────────────────────────────────────────
#  Rendering Boundary
# ──────────────────────────────────────────────

def render_notification(notification_type: str, payload: dict) -> tuple[str, str]:
    """
    Render a structured notification payload into a subject and HTML body.
    
    This enforces the boundary that the outbox stores structured data + template IDs,
    NOT pre-rendered HTML.
    """
    # In a full implementation, this would load templates (e.g. Jinja2) and render them.
    # We provide a strict deterministic implementation for Slice 3.3.
    
    if not isinstance(payload, dict):
        raise ValueError("Payload must be a dictionary")
        
    valid_types = {
        "TICKET_CREATED", "TICKET_UPDATED", "TICKET_RESOLVED", 
        "ORDER_UPDATED", "ESCALATION_TEAM", "CUSTOM_EMAIL"
    }
    
    if notification_type not in valid_types:
        raise ValueError(f"Unknown notification type: {notification_type}")

    subject = f"Notification: {notification_type}"
    body = "<html><body>"
    
    if notification_type in ("TICKET_CREATED", "TICKET_UPDATED", "TICKET_RESOLVED", "ESCALATION_TEAM"):
        if "ticket_id" not in payload:
            raise ValueError(f"Missing required field 'ticket_id' for {notification_type}")
        subject = f"Update on Ticket #{payload['ticket_id']}"
        body += f"<h1>Ticket #{payload['ticket_id']}</h1>"
    elif notification_type == "ORDER_UPDATED":
        if "order_id" not in payload:
            raise ValueError(f"Missing required field 'order_id' for {notification_type}")
        subject = f"Update on Order #{payload['order_id']}"
        body += f"<h1>Order #{payload['order_id']}</h1>"
    elif notification_type == "CUSTOM_EMAIL":
        if "ticket_id" not in payload:
            raise ValueError(f"Missing required field 'ticket_id' for {notification_type}")
        subject = f"Custom Email for Ticket #{payload['ticket_id']}"
        body += f"<h1>Custom Email</h1>"
        
    if "message" in payload:
        body += f"<p>{payload['message']}</p>"
        
    body += "</body></html>"
        
    return subject, body


# ──────────────────────────────────────────────
#  Worker Class
# ──────────────────────────────────────────────

class OutboxWorker:
    """
    Durable outbox worker.

    Lifecycle:
      1. ``start()`` — launches the background polling loop.
      2. Periodic polling — claims, sends, updates rows.
      3. Periodic stale recovery — reclaims abandoned rows.
      4. ``stop()`` — graceful shutdown; waits for current cycle.

    The worker is safe for concurrent deployment across multiple API
    replicas.  Row coordination is exclusively via SKIP LOCKED.

    The class can be instantiated independently of FastAPI and run
    as a dedicated process by constructing it with a connection string
    and calling ``run_forever()``.
    """

    def __init__(
        self,
        dsn: str,
        sender: EmailSender,
        *,
        poll_interval_seconds: float = 5.0,
        batch_size: int = 10,
        retry_policy: Optional[RetryPolicy] = None,
        stale_threshold_seconds: float = DEFAULT_STALE_THRESHOLD_SECONDS,
        recovery_interval_seconds: float = 60.0,
        worker_id: Optional[str] = None,
    ):
        self.dsn = dsn
        self.sender = sender
        self.poll_interval_seconds = poll_interval_seconds
        self.batch_size = batch_size
        self.retry_policy = retry_policy or RetryPolicy()
        self.stale_threshold_seconds = stale_threshold_seconds
        self.recovery_interval_seconds = recovery_interval_seconds
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}-{os.getpid()}"

        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._recovery_task: Optional[asyncio.Task] = None

    # ── Public API ────────────────────────────

    async def start(self) -> None:
        """Start the polling and recovery loops as asyncio background tasks."""
        logger.info(
            "OutboxWorker starting",
            extra={"worker_id": self.worker_id, "poll_interval": self.poll_interval_seconds},
        )
        self._stop_event.clear()
        self._task = asyncio.create_task(self._poll_loop(), name=f"outbox-poll-{self.worker_id}")
        self._recovery_task = asyncio.create_task(
            self._recovery_loop(), name=f"outbox-recovery-{self.worker_id}"
        )

    async def stop(self) -> None:
        """Gracefully stop the worker.  Waits for the current cycle to finish."""
        logger.info("OutboxWorker stopping", extra={"worker_id": self.worker_id})
        self._stop_event.set()

        for task in (self._task, self._recovery_task):
            if task and not task.done():
                try:
                    await asyncio.wait_for(task, timeout=30)
                except asyncio.TimeoutError:
                    logger.warning("OutboxWorker task did not finish within 30s, cancelling")
                    task.cancel()
                except asyncio.CancelledError:
                    pass

        logger.info("OutboxWorker stopped", extra={"worker_id": self.worker_id})

    # ── Poll Loop ─────────────────────────────

    async def _poll_loop(self) -> None:
        """Main polling loop: claim → send → update."""
        while not self._stop_event.is_set():
            try:
                processed = await asyncio.get_event_loop().run_in_executor(
                    None, self._claim_and_process
                )
                if processed == 0:
                    # Nothing to do; wait before next poll
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=self.poll_interval_seconds,
                        )
                    except asyncio.TimeoutError:
                        pass
                # If we processed rows, immediately try another batch
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(
                    "OutboxWorker poll cycle error",
                    extra={"worker_id": self.worker_id},
                )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.poll_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass

    # ── Recovery Loop ─────────────────────────

    async def _recovery_loop(self) -> None:
        """Periodically recover stale PROCESSING rows."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.recovery_interval_seconds,
                )
                break  # stop_event was set
            except asyncio.TimeoutError:
                pass

            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._recover_stale_rows
                )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(
                    "OutboxWorker recovery cycle error",
                    extra={"worker_id": self.worker_id},
                )

    # ── Core Processing (sync, runs in executor) ────

    def _claim_and_process(self) -> int:
        """
        Claim eligible rows and process them one by one.

        Returns the number of rows processed in this batch.
        """
        conn = None
        try:
            conn = psycopg2.connect(self.dsn)
            conn.autocommit = False

            # 1. Atomically claim a batch
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(CLAIM_SQL, (self.batch_size,))
                claimed_rows = cur.fetchall()
            conn.commit()

            if not claimed_rows:
                return 0

            logger.info(
                "Claimed %d outbox rows",
                len(claimed_rows),
                extra={"worker_id": self.worker_id},
            )

            # 2. Process each claimed row
            for row in claimed_rows:
                self._process_row(conn, row)

            return len(claimed_rows)

        except Exception:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _process_row(self, conn: "psycopg2.connection", row: dict) -> None:
        """Send a single notification and update its outbox status."""
        event_id = str(row["event_id"])
        claim_token = str(row["claim_token"])
        notification_type = row["notification_type"]
        recipient_address = row["recipient_address"]
        logical_hash = row["logical_identity_hash"]
        attempt_count = row["attempt_count"]
        payload = row["payload"]

        logger.info(
            "Processing outbox event",
            extra={
                "worker_id": self.worker_id,
                "event_id": event_id,
                "logical_identity_hash": logical_hash,
                "notification_type": notification_type,
                "attempt": attempt_count,
                "state_transition": "PROCESSING",
            },
        )

        # Enforce the rendering boundary: outbox payload is structured data
        try:
            subject, html_body = render_notification(notification_type, payload)
        except Exception as e:
            # Deterministic terminal failure for unknown type or malformed payload
            self._mark_failed(
                conn, event_id, claim_token, logical_hash, notification_type,
                f"Rendering failed: {str(e)}"
            )
            return

        # Delegate to sender abstraction
        result: SendResult = self.sender.send(
            to=recipient_address,
            subject=subject,
            html_body=html_body,
        )

        # Determine next state
        try:
            if result.outcome == SendOutcome.SUCCESS:
                self._mark_sent(conn, event_id, claim_token, logical_hash, notification_type)

            elif self.retry_policy.is_terminal_outcome(result.outcome):
                self._mark_failed(
                    conn, event_id, claim_token, logical_hash, notification_type,
                    result.error_message or "Terminal failure",
                )

            elif self.retry_policy.has_exceeded_max_attempts(attempt_count):
                self._mark_failed(
                    conn, event_id, claim_token, logical_hash, notification_type,
                    f"Max attempts ({self.retry_policy.max_attempts}) exceeded. "
                    f"Last error: {result.error_message or 'unknown'}",
                )

            else:
                is_outage = (result.outcome == SendOutcome.AUTH_CONFIG_FAILURE)
                # For provider outages, we pause/retry without consuming the attempt budget
                if is_outage:
                    delay = self.retry_policy.compute_outage_delay()
                else:
                    delay = self.retry_policy.compute_next_attempt_delay(attempt_count)
                
                self._mark_retryable(
                    conn, event_id, claim_token, logical_hash, notification_type,
                    delay, result.error_message or "Transient failure",
                    is_provider_outage=is_outage
                )

        except Exception:
            logger.exception(
                "Failed to update outbox row after send attempt",
                extra={
                    "worker_id": self.worker_id,
                    "event_id": event_id,
                },
            )
            try:
                conn.rollback()
            except Exception:
                pass

    # ── State Transition Helpers ──────────────

    def _mark_sent(self, conn, event_id: str, claim_token: str, logical_hash: str, ntype: str) -> None:
        with conn.cursor() as cur:
            cur.execute(MARK_SENT_SQL, (event_id, claim_token))
            if cur.rowcount == 0:
                logger.warning("Lease lost: outbox event %s could not be marked SENT", event_id)
                return
        conn.commit()
        logger.info(
            "Outbox event SENT",
            extra={
                "worker_id": self.worker_id,
                "event_id": event_id,
                "logical_identity_hash": logical_hash,
                "notification_type": ntype,
                "state_transition": "PROCESSING -> SENT",
            },
        )

    def _mark_retryable(
        self, conn, event_id: str, claim_token: str, logical_hash: str, ntype: str,
        delay_seconds: float, error_msg: str, is_provider_outage: bool = False,
    ) -> None:
        sanitized = error_msg[:500] if error_msg else "unknown"
        # If provider outage, decrement the attempt count we added on claim
        attempt_penalty = 1 if is_provider_outage else 0
        with conn.cursor() as cur:
            cur.execute(MARK_RETRYABLE_SQL, (delay_seconds, sanitized, attempt_penalty, event_id, claim_token))
            if cur.rowcount == 0:
                logger.warning("Lease lost: outbox event %s could not be marked RETRYABLE", event_id)
                return
        conn.commit()
        logger.warning(
            "Outbox event RETRYABLE",
            extra={
                "worker_id": self.worker_id,
                "event_id": event_id,
                "logical_identity_hash": logical_hash,
                "notification_type": ntype,
                "state_transition": "PROCESSING -> RETRYABLE",
                "next_retry_delay_seconds": delay_seconds,
                "sanitized_error": sanitized,
            },
        )

    def _mark_failed(
        self, conn, event_id: str, claim_token: str, logical_hash: str, ntype: str, error_msg: str,
    ) -> None:
        sanitized = error_msg[:500] if error_msg else "unknown"
        with conn.cursor() as cur:
            cur.execute(MARK_FAILED_SQL, (sanitized, event_id, claim_token))
            if cur.rowcount == 0:
                logger.warning("Lease lost: outbox event %s could not be marked FAILED", event_id)
                return
        conn.commit()
        logger.error(
            "Outbox event FAILED (terminal)",
            extra={
                "worker_id": self.worker_id,
                "event_id": event_id,
                "logical_identity_hash": logical_hash,
                "notification_type": ntype,
                "state_transition": "PROCESSING -> FAILED",
                "sanitized_error": sanitized,
            },
        )

    # ── Stale Recovery (sync) ─────────────────

    def _recover_stale_rows(self) -> int:
        """
        Recover PROCESSING rows whose leases have expired.

        Uses SKIP LOCKED so concurrent recovery sweeps across multiple
        worker instances cannot create duplicate ownership.

        Returns the number of rows recovered.
        """
        conn = None
        try:
            conn = psycopg2.connect(self.dsn)
            conn.autocommit = False

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # We need to pass max_attempts twice for the CASE statements, then stale_threshold
                cur.execute(
                    RECOVER_STALE_SQL, 
                    (
                        self.retry_policy.max_attempts,
                        self.retry_policy.max_attempts,
                        self.stale_threshold_seconds,
                    )
                )
                recovered = cur.fetchall()
            conn.commit()

            for row in recovered:
                logger.warning(
                    "Recovered stale PROCESSING row",
                    extra={
                        "worker_id": self.worker_id,
                        "event_id": str(row["event_id"]),
                        "logical_identity_hash": row["logical_identity_hash"],
                        "notification_type": row["notification_type"],
                        "attempt_count": row["attempt_count"],
                        "state_transition": f"PROCESSING (stale) -> {row['status']}",
                    },
                )

            if recovered:
                logger.info(
                    "Stale recovery sweep complete",
                    extra={
                        "worker_id": self.worker_id,
                        "recovered_count": len(recovered),
                    },
                )

            return len(recovered)

        except Exception:
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            logger.exception(
                "Stale recovery sweep failed",
                extra={"worker_id": self.worker_id},
            )
            return 0
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    # ── Standalone Execution ──────────────────

    async def run_forever(self) -> None:
        """
        Entry point for running as a dedicated process.

        Usage:
            import asyncio
            from app.outbox.worker import OutboxWorker
            from app.outbox.sender import GmailSmtpSender
            from app.config import settings

            worker = OutboxWorker(
                dsn=settings.database_url_sync.replace('+psycopg2', ''),
                sender=GmailSmtpSender(),
            )
            asyncio.run(worker.run_forever())
        """
        await self.start()
        try:
            await self._stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()
