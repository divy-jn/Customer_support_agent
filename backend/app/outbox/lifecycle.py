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
from typing import Optional

from app.config import settings
from app.outbox.sender import GmailSmtpSender
from app.outbox.worker import OutboxWorker

logger = logging.getLogger("outbox.lifecycle")

_worker_instance: Optional[OutboxWorker] = None


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
