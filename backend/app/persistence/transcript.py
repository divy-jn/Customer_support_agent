"""Ordered, coalescing transcript writes owned by the API lifespan."""

import asyncio
import logging
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone

import httpx
from postgrest.exceptions import APIError
from supabase import AsyncClientOptions, acreate_client

from app.config import settings

logger = logging.getLogger(__name__)
_TRANSIENT_CODES = {"PGRST000", "PGRST001", "PGRST002", "PGRST003", "40001", "40P01", "57014"}
_RETRY_DELAYS = (0.25, 1.0)
_FLUSH_INTERVAL = 30.0


class TranscriptWriter:
    def __init__(self):
        self._pending = {}
        self._task = None
        self._stopping = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._stopping

    def start(self, write) -> None:
        self._available = asyncio.Event()
        self._stopping = False
        self._task = asyncio.create_task(self._run(write))

    def submit(self, session_id: str, session: dict, *, disconnected: bool = False) -> None:
        if not self.running:
            return
        owner = session.get("customer_id")
        history = session.get("conversation_history")
        revision = session.get("_session_revision")
        if type(owner) is not int or owner <= 0 or not history or type(revision) is not int:
            return

        queued = self._pending.get(session_id)
        if queued and queued["p_session_revision"] >= revision:
            if queued["p_session_revision"] == revision and disconnected and not queued["p_ended_at"]:
                self._pending[session_id] = {**queued, "p_ended_at": datetime.now(timezone.utc).isoformat()}
                self._available.set()
            return
        self._pending[session_id] = {
            "p_session_id": session_id,
            "p_customer_id": owner,
            "p_transcript": deepcopy(history),
            "p_session_revision": revision,
            "p_escalated": bool(session.get("escalated") or session.get("mode") == "human"
                                or any(msg.get("escalated") for msg in history)),
            "p_started_at": session.get("created_at"),
            "p_ended_at": datetime.now(timezone.utc).isoformat() if disconnected else None,
        }
        self._available.set()

    async def stop(self) -> None:
        if self._task is not None:
            self._stopping = True
            self._available.set()
            try:
                await self._task
            finally:
                self._task = None

    async def _run(self, write) -> None:
        while True:
            if self._pending:
                try:
                    await asyncio.wait_for(self._available.wait(), timeout=_FLUSH_INTERVAL)
                except TimeoutError:
                    pass
            else:
                await self._available.wait()

            self._available.clear()
            stopping = self._stopping
            for session_id, snapshot in list(self._pending.items()):
                if self._pending.get(session_id) is not snapshot:
                    continue
                if await self._write_with_retry(write, snapshot) and self._pending.get(session_id) is snapshot:
                    del self._pending[session_id]
            if stopping:
                return

    async def _write_with_retry(self, write, snapshot) -> bool:
        session_id = snapshot["p_session_id"]
        revision = snapshot["p_session_revision"]
        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                await asyncio.wait_for(write(snapshot), timeout=8.0)
                return True
            except Exception as exc:
                retryable = isinstance(exc, (httpx.TransportError, TimeoutError)) or (
                    isinstance(exc, APIError) and (
                        exc.code in _TRANSIENT_CODES or str(exc.code).startswith("08")
                    )
                )
                if not retryable or attempt == len(_RETRY_DELAYS):
                    logger.error(
                        "Transcript write failed for session %s revision %s (%s)",
                        session_id, revision, type(exc).__name__,
                    )
                    return False
                logger.warning("Retrying transcript write for session %s revision %s", session_id, revision)
                await asyncio.sleep(_RETRY_DELAYS[attempt])


transcript_writer = TranscriptWriter()


@asynccontextmanager
async def transcript_persistence():
    async with httpx.AsyncClient(timeout=5.0) as transport:
        client = await acreate_client(
            settings.supabase_url,
            settings.supabase_key,
            options=AsyncClientOptions(
                httpx_client=transport,
                auto_refresh_token=False,
                persist_session=False,
            ),
        )

        async def write(snapshot):
            await client.rpc("persist_conversation", snapshot).execute()

        transcript_writer.start(write)
        try:
            yield
        finally:
            await transcript_writer.stop()
