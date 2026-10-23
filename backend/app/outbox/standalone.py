"""
Standalone Outbox Worker Entrypoint

Independently runnable background dispatch worker that polls the PostgreSQL
outbox_events table, claims events with SKIP LOCKED leases, delivers notifications
via the EmailSender abstraction (GmailSmtpSender or test mock), and manages
bounded exponential backoff and stale-lease recovery.

Usage:
    python -m app.outbox.standalone [OPTIONS]
    python -m backend.app.outbox.standalone [OPTIONS]

Operational Notes & Delivery Semantics:
- Multi-Worker Concurrency:
    Row coordination is exclusively via SELECT ... FOR UPDATE SKIP LOCKED. Multiple
    worker processes can safely run across independent containers or replicas without
    double-claiming active leases.
- Lease Expiration & Stale Recovery:
    Events claimed by a worker carry a claim_token and claimed_at timestamp. If a
    process crashes or is abruptly killed (SIGKILL), the unacknowledged lease expires
    after stale_threshold_seconds (default: 300s) and is reclaimed by the periodic
    recovery sweep on any active worker.
- Deterministic Retry & Backoff:
    Transient delivery errors transition to RETRYABLE with bounded exponential backoff
    capped at max_backoff_seconds. Non-retryable errors or events exceeding max_attempts
    transition to terminal FAILED status.
- SMTP Duplicate Delivery Limitation:
    Standard SMTP provides NO application-level exactly-once guarantee. If a crash
    or network partition occurs after SMTP accepts a message but before the worker
    can persist the 'SENT' transition and commit the database transaction, the row
    will remain in 'PROCESSING' until the lease expires (stale_threshold_seconds),
    at which point it will be recovered as 'RETRYABLE' and resent. This at-least-once
    behavior is standard for outbox delivery across non-transactional external systems.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from typing import Optional

from app.config import settings
from app.logger import setup_logger
from app.outbox.lifecycle import _build_dsn, start_reconciliation_scheduler, stop_reconciliation_scheduler
from app.outbox.sender import EmailSender, GmailSmtpSender
from app.outbox.worker import OutboxWorker, RetryPolicy, emit_outbox_telemetry

logger = logging.getLogger("outbox.standalone")


def parse_args(args: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse CLI options for standalone outbox worker execution."""
    parser = argparse.ArgumentParser(
        description="Run the standalone Customer Support Outbox Background Dispatch Worker."
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=None,
        help="Poll interval in seconds (default: from settings or 5.0).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="Maximum outbox events to claim per batch (default: from settings or 10).",
    )
    parser.add_argument(
        "--stale-threshold",
        type=float,
        default=None,
        help="Lease expiration threshold in seconds for crash recovery (default: 300.0).",
    )
    parser.add_argument(
        "--recovery-interval",
        type=float,
        default=None,
        help="Interval in seconds between stale-lease recovery sweeps (default: 60.0).",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help="Maximum retry attempts before terminal failure (default: from settings or 5).",
    )
    parser.add_argument(
        "--worker-id",
        type=str,
        default=None,
        help="Custom worker instance identifier.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        default=False,
        help="Execute a single processing and recovery pass, then exit immediately.",
    )
    parser.add_argument(
        "--no-reconciliation",
        action="store_true",
        default=False,
        help="Disable the background escalation reconciliation scheduler in this process.",
    )
    return parser.parse_args(args)


def build_worker(
    args: Optional[argparse.Namespace] = None,
    sender: Optional[EmailSender] = None,
    dsn: Optional[str] = None,
) -> OutboxWorker:
    """Construct an OutboxWorker using CLI overrides and settings defaults."""
    worker_dsn = dsn or _build_dsn()
    delivery_sender = sender or GmailSmtpSender()

    poll_interval = (
        args.poll_interval
        if args and args.poll_interval is not None
        else settings.outbox_poll_interval_seconds
    )
    batch_size = (
        args.batch_size
        if args and args.batch_size is not None
        else settings.outbox_batch_size
    )
    stale_threshold = (
        args.stale_threshold
        if args and args.stale_threshold is not None
        else settings.outbox_stale_threshold_seconds
    )
    recovery_interval = (
        args.recovery_interval
        if args and args.recovery_interval is not None
        else settings.outbox_recovery_interval_seconds
    )
    max_attempts = (
        args.max_attempts
        if args and args.max_attempts is not None
        else settings.outbox_max_attempts
    )
    worker_id = args.worker_id if args and args.worker_id else None

    retry_policy = RetryPolicy(max_attempts=max_attempts)

    return OutboxWorker(
        dsn=worker_dsn,
        sender=delivery_sender,
        poll_interval_seconds=poll_interval,
        batch_size=batch_size,
        stale_threshold_seconds=stale_threshold,
        recovery_interval_seconds=recovery_interval,
        retry_policy=retry_policy,
        worker_id=worker_id,
    )


async def run_standalone_worker(
    args: Optional[argparse.Namespace] = None,
    sender: Optional[EmailSender] = None,
    stop_event: Optional[asyncio.Event] = None,
    dsn: Optional[str] = None,
) -> int:
    """
    Run the standalone worker process loop until stopped.

    Returns exit code (0 for clean shutdown, 1 for configuration/fatal error).
    """
    setup_logger(log_level=settings.log_level)

    # 1. Mode and enabled boundary verification
    mode = (os.environ.get("OUTBOX_WORKER_MODE") or settings.outbox_worker_mode).lower().strip()
    if (
        mode == "disabled"
        or not settings.outbox_worker_enabled
        or os.environ.get("OUTBOX_WORKER_ENABLED", "").lower() in ("false", "0", "no")
    ):
        logger.warning(
            "Standalone outbox worker is disabled via configuration "
            "(OUTBOX_WORKER_ENABLED=false or OUTBOX_WORKER_MODE='disabled'). Exiting."
        )
        return 0

    if mode == "in_process":
        logger.warning(
            "OUTBOX_WORKER_MODE is configured as 'in_process' while running standalone entrypoint. "
            "To prevent competing in-process workers on the API server, set OUTBOX_WORKER_MODE='standalone' "
            "for the FastAPI deployment."
        )

    # 2. Database readiness check
    if not settings.database_password and not os.environ.get("TEST_POSTGRES_URL") and not dsn:
        logger.error(
            "Cannot start standalone outbox worker: DATABASE_PASSWORD or TEST_POSTGRES_URL is not set."
        )
        return 1

    worker = build_worker(args=args, sender=sender, dsn=dsn)

    # 3. Operational startup logging (sanitized, zero secrets)
    logger.info(
        "=== Standalone Outbox Worker Initialized ===",
        extra={
            "worker_id": worker.worker_id,
            "pid": os.getpid(),
            "mode": mode,
            "poll_interval_seconds": worker.poll_interval_seconds,
            "batch_size": worker.batch_size,
            "stale_threshold_seconds": worker.stale_threshold_seconds,
            "max_attempts": worker.retry_policy.max_attempts,
            "sender_type": type(worker.sender).__name__,
        },
    )
    emit_outbox_telemetry("standalone_worker_started", {
        "worker_id": worker.worker_id,
        "pid": os.getpid(),
        "mode": mode,
        "poll_interval": worker.poll_interval_seconds,
        "batch_size": worker.batch_size,
    })

    # 4. Handle one-shot execution mode
    if args and args.once:
        logger.info("Running single pass (--once) for outbox worker and stale-lease recovery...")
        processed, recovered = worker.run_once()
        logger.info(
            f"Single pass complete: {processed} events processed, {recovered} stale leases recovered.",
            extra={"worker_id": worker.worker_id, "processed": processed, "recovered": recovered},
        )
        return 0

    # 5. Cross-platform signal registration
    loop = asyncio.get_running_loop()
    termination_event = stop_event or asyncio.Event()

    def _trigger_shutdown(sig_name: str) -> None:
        logger.info(
            f"Termination signal {sig_name} received; initiating graceful worker shutdown...",
            extra={"worker_id": worker.worker_id, "signal": sig_name},
        )
        termination_event.set()

    # POSIX signal handlers
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig.name: _trigger_shutdown(s))
    except (NotImplementedError, AttributeError):
        # Fallback for Windows or non-main thread execution
        try:
            signal.signal(signal.SIGINT, lambda s, f: _trigger_shutdown("SIGINT"))
            signal.signal(signal.SIGTERM, lambda s, f: _trigger_shutdown("SIGTERM"))
        except Exception:
            pass

    # 6. Optional reconciliation scheduler lifecycle
    include_reconciliation = (
        not (args and args.no_reconciliation)
        and settings.reconciliation_enabled
        and bool(settings.database_password or os.environ.get("TEST_POSTGRES_URL"))
    )

    if include_reconciliation:
        try:
            await start_reconciliation_scheduler()
            logger.info("Standalone process started background reconciliation scheduler.")
        except Exception as ex:
            logger.error(f"Failed to start reconciliation scheduler in standalone worker: {ex}")

    # 7. Worker execution loop
    try:
        await worker.run_forever(stop_event=termination_event)
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info(
            "Worker execution interrupted; draining current tasks...",
            extra={"worker_id": worker.worker_id},
        )
    finally:
        if include_reconciliation:
            try:
                await stop_reconciliation_scheduler()
            except Exception:
                pass
        await worker.stop()
        logger.info(
            "=== Standalone Outbox Worker Stopped Cleanly ===",
            extra={"worker_id": worker.worker_id, "pid": os.getpid()},
        )
        emit_outbox_telemetry("standalone_worker_stopped", {
            "worker_id": worker.worker_id,
            "pid": os.getpid(),
        })

    return 0


def main() -> None:
    """CLI entrypoint for standalone worker execution."""
    args = parse_args()
    try:
        exit_code = asyncio.run(run_standalone_worker(args=args))
        sys.exit(exit_code)
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:
        logging.getLogger("outbox.standalone").critical(f"Fatal worker exception: {exc}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
