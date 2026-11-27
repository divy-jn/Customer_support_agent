"""Ordered, coalescing transcript writes owned by the API lifespan."""

import asyncio
import json
import os
import logging
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx
from postgrest.exceptions import APIError
from supabase import AsyncClientOptions, acreate_client

from app.config import settings

logger = logging.getLogger(__name__)
_TRANSIENT_CODES = {"PGRST000", "PGRST001", "PGRST002", "PGRST003", "40001", "40P01", "57014"}
_RETRY_DELAYS = (0.25, 1.0)
_FLUSH_INTERVAL = 30.0


class TranscriptWriter:
    def __init__(self, spool_path: Path | None = None):
        self._pending = {}
        self._spooled = {}
        self._spool_path = spool_path
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

    def _load_spool(self) -> dict:
        if not self._spool_path:
            return {}
        try:
            return json.loads(self._spool_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}

    def _save_spool(self, snapshots: dict) -> None:
        path = self._spool_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            try:
                json.dump(snapshots, file)
                file.flush()
                os.fsync(file.fileno())
            except BaseException:
                file.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    async def _update_spool(self, session_id, snapshot) -> None:
        if not self._spool_path or (snapshot is None and session_id not in self._spooled):
            return
        snapshots = dict(self._spooled)
        if snapshot is None:
            snapshots.pop(session_id, None)
        else:
            snapshots[session_id] = snapshot
        try:
            await asyncio.to_thread(self._save_spool, snapshots)
        except (OSError, ValueError):
            logger.exception("Could not update transcript spool")
        else:
            self._spooled = snapshots

    async def _run(self, write) -> None:
        self._spooled = await asyncio.to_thread(self._load_spool) if self._spool_path else {}
        for session_id, snapshot in self._spooled.items():
            pending = self._pending.get(session_id)
            if not pending or pending["p_session_revision"] < snapshot["p_session_revision"]:
                self._pending[session_id] = snapshot
        if self._pending:
            self._available.set()

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
                if await self._write_with_retry(write, snapshot):
                    if self._pending.get(session_id) is snapshot:
                        del self._pending[session_id]
                    await self._update_spool(session_id, None)
                else:
                    await self._update_spool(session_id, self._pending[session_id])
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


transcript_writer = TranscriptWriter(settings.transcript_spool_path)


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
