from unittest.mock import AsyncMock

import pytest

from app.persistence.session import SaveResult
from app.websocket import chat_handler


@pytest.mark.asyncio
async def test_cache_loss_never_revives_an_actionable_approval_from_transcript(monkeypatch):
    store = chat_handler.session_store
    monkeypatch.setattr(store, "get_session", AsyncMock(return_value=None))
    create = AsyncMock(return_value=SaveResult.SUCCESS)
    monkeypatch.setattr(store, "create_session_if_absent", create)
    stale = [{"role": "agent", "content": status, "approval": {"status": status}}
             for status in ("pending", "ambiguous", "executing")]
    completed = {"role": "agent", "content": "receipt", "approval": {"status": "succeeded"}}
    transcript = stale + [completed]
    session = await chat_handler._get_session(
        "synthetic-session", 7, persisted={"customer_id": 7, "session_revision": 12, "transcript": transcript},
    )

    assert session["pending_approval"] is None
    history = session["conversation_history"]
    assert [entry["content"] for entry in history] == ["pending", "ambiguous", "executing", "receipt"]
    assert all("approval" not in entry for entry in history[:3])
    assert history[-1] == completed
    assert all("approval" in entry for entry in stale)
    assert create.call_args.args[1] is session
