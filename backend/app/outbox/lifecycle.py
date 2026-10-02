"""
Outbox Worker — FastAPI Lifecycle Integration (Slice 3.3)

Provides ``start_outbox_worker`` and ``stop_outbox_worker`` hooks
for use in the FastAPI ``lifespan`` context manager.

This module reads database configuration from ``app.config.settings``
and instantiates a ``GmailSmtpSender`` for production use.

IMPORTANT: This integration is **provisional**.  The worker is
coupled to the API process lifecycle.  It can be replaced with a
dedicated worker process by instantiating ``OutboxWorker`` directly
with a connection string and calling ``run_forever()``.
"""

from __future__ import annotations

import logging
import asyncio
from typing import Optional

from app.config import settings
from app.outbox.sender import GmailSmtpSender
from app.outbox.worker import OutboxWorker
from app.outbox.reconciliation_coordinator import ReconciliationCoordinator

logger = logging.getLogger("outbox.lifecycle")

_worker_instance: Optional[OutboxWorker] = None
_reconciliation_task: Optional[asyncio.Task] = None
_reconciliation_cancel_event: Optional[asyncio.Event] = None
_reconciliation_lock: Optional[asyncio.Lock] = None


def _build_dsn() -> str:
    """
    Build a psycopg2-compatible DSN from the existing settings.

    The settings expose ``database_url_sync`` as
    ``postgresql+psycopg2://user:pass@host:port/db``.
    psycopg2 needs ``postgresql://...`` (no driver suffix).
    """
    url = settings.database_url_sync
    return url.replace("postgresql+psycopg2://", "postgresql://")


async def start_outbox_worker() -> None:
    """Start the outbox worker as a background task.  Call from lifespan startup."""
    global _worker_instance

    dsn = _build_dsn()
    sender = GmailSmtpSender()

    _worker_instance = OutboxWorker(
        dsn=dsn,
        sender=sender,
        poll_interval_seconds=5.0,
        batch_size=10,
        stale_threshold_seconds=300.0,   # 5 minutes
        recovery_interval_seconds=60.0,  # sweep every minute
    )
    await _worker_instance.start()
    logger.info("Outbox worker started (FastAPI in-process)")


async def stop_outbox_worker() -> None:
    """Gracefully stop the outbox worker.  Call from lifespan shutdown."""
    global _worker_instance
    if _worker_instance:
        await _worker_instance.stop()
        _worker_instance = None
        logger.info("Outbox worker stopped")

async def _reconciliation_loop() -> None:
    """
    Background loop that performs periodic reconciliation and an initial startup sweep.
    """
    dsn = _build_dsn()
    coordinator = ReconciliationCoordinator(dsn)
    
    # 1. Startup Catch-up Sweep
    logger.info("Starting initial outbox reconciliation sweep...")
    pages = 0
    max_startup_pages = settings.reconciliation_startup_pages
    
    last_created_at = None
    last_event_id = None
    from datetime import datetime, timezone
    sweep_started_at = datetime.now(timezone.utc)
    
    while pages < max_startup_pages and _reconciliation_cancel_event and not _reconciliation_cancel_event.is_set():
        try:
            async with _reconciliation_lock:
                # Run synchronous psycopg2 operations in a thread
                result = await asyncio.to_thread(
                    coordinator.discover_and_reconcile_escalations,
                    horizon_days=settings.reconciliation_horizon_days,
                    batch_size=settings.reconciliation_batch_size,
                    last_created_at=last_created_at,
                    last_event_id=last_event_id,
                    sweep_started_at=sweep_started_at
                )
            
            batch_result, next_created_at, next_event_id = result
            if not batch_result.results:
                break # Reached the end of the horizon
                
            # Observability logging
            repaired = sum(1 for r in batch_result.results if r.status == "REPAIRED")
            logger.info(f"Reconciliation page {pages+1}: scanned {len(batch_result.results)}, repaired {repaired}")
            
            last_created_at = next_created_at
            last_event_id = next_event_id
            pages += 1
            
        except Exception as e:
            logger.error(f"Error during startup reconciliation: {e}", exc_info=True)
            break
            
    logger.info("Initial reconciliation sweep complete.")
    
    # 2. Periodic Reconciliation Loop
    interval = settings.reconciliation_interval_seconds
    
    # State for the periodic sweep across multiple wakeups
    sweep_started_at = None
    periodic_last_created_at = None
    periodic_last_event_id = None
    
    while _reconciliation_cancel_event and not _reconciliation_cancel_event.is_set():
        try:
            # Wait for cancellation or interval timeout
            await asyncio.wait_for(_reconciliation_cancel_event.wait(), timeout=interval)
            break # Cancelled
        except asyncio.TimeoutError:
            pass # Timeout means interval elapsed
            
        if _reconciliation_cancel_event and _reconciliation_cancel_event.is_set():
            break
            
        # Execute a periodic sweep chunk
        from datetime import datetime, timezone
        if sweep_started_at is None:
            sweep_started_at = datetime.now(timezone.utc)
            
        # We sweep until exhaustion, but add a safety bound (e.g., 50 pages) to avoid infinite loops per run
        periodic_max_pages = 50
        pages = 0
        total_scanned = 0
        total_repaired = 0
        exhausted = False
        
        while pages < periodic_max_pages and _reconciliation_cancel_event and not _reconciliation_cancel_event.is_set():
            try:
                async with _reconciliation_lock:
                    result = await asyncio.to_thread(
                        coordinator.discover_and_reconcile_escalations,
                        horizon_days=settings.reconciliation_horizon_days,
                        batch_size=settings.reconciliation_batch_size,
                        last_created_at=periodic_last_created_at,
                        last_event_id=periodic_last_event_id,
                        sweep_started_at=sweep_started_at
                    )
                batch_result, next_created_at, next_event_id = result
                
                if not batch_result.results:
                    exhausted = True
                    break
                    
                total_scanned += len(batch_result.results)
                total_repaired += sum(1 for r in batch_result.results if r.status == "REPAIRED")
                
                periodic_last_created_at = next_created_at
                periodic_last_event_id = next_event_id
                pages += 1
            except Exception as e:
                logger.error(f"Error during periodic reconciliation sweep: {e}", exc_info=True)
                break
                
        if total_scanned > 0:
            if total_repaired > 0:
                logger.info(f"Periodic reconciliation chunk complete: scanned {total_scanned}, repaired {total_repaired} in {pages} pages")
            else:
                logger.debug(f"Periodic reconciliation chunk complete: scanned {total_scanned}, no repairs needed in {pages} pages")
                
        if exhausted:
            sweep_started_at = None
            periodic_last_created_at = None
            periodic_last_event_id = None


async def start_reconciliation_scheduler() -> None:
    """Start the periodic reconciliation background task."""
    global _reconciliation_task, _reconciliation_cancel_event, _reconciliation_lock
    
    if not settings.reconciliation_enabled:
        logger.info("Outbox reconciliation is disabled via configuration.")
        return
        
    if _reconciliation_cancel_event is None:
        _reconciliation_cancel_event = asyncio.Event()
    if _reconciliation_lock is None:
        _reconciliation_lock = asyncio.Lock()
        
    _reconciliation_cancel_event.clear()
    _reconciliation_task = asyncio.create_task(_reconciliation_loop())
    logger.info("Outbox reconciliation scheduler started")

async def stop_reconciliation_scheduler() -> None:
    """Stop the periodic reconciliation background task."""
    global _reconciliation_task, _reconciliation_cancel_event, _reconciliation_lock
    
    if _reconciliation_cancel_event:
        _reconciliation_cancel_event.set()
    if _reconciliation_task:
        try:
            # Wait with a short timeout to allow graceful exit
            await asyncio.wait_for(_reconciliation_task, timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("Reconciliation task did not exit gracefully, cancelling...")
            _reconciliation_task.cancel()
        except Exception as e:
            logger.error(f"Error stopping reconciliation scheduler: {e}")
            
        _reconciliation_task = None
        logger.info("Outbox reconciliation scheduler stopped")
        
    _reconciliation_cancel_event = None
    _reconciliation_lock = None
