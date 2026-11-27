import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4, uuid5

import httpx
import jwt
import pytest

from app import tools
from app.config import settings
from app.main import app
from app.mcp.http_server import mcp_http
from app.persistence import transcript
from scripts import ingest_knowledge
from tests.test_wave5_hardening import snapshot
from tests.test_wave5_postgres import cursor


@pytest.mark.requires_postgres
@pytest.mark.parametrize("with_outbox", [False, True])
def test_ticket_transaction_stores_handoff_and_rolls_back_invalid_metadata(cursor, with_outbox):
    from psycopg2 import Error
    from psycopg2.extras import Json

    turn, child = str(uuid4()), str(uuid4())
    payload = {
        "subject": turn, "description": "Handoff test", "type": "inquiry",
        "status": "open", "priority": "normal", "channel": "chat",
        "pending_handoff": {
            "session_id": "durable-session", "handoff_request_id": child,
            "message": "Handoff test", "escalation_reason": "test",
        },
    }
    args = (1, turn, "create_ticket", "new", "test-hash", Json(payload))
    if with_outbox:
        sql = "SELECT execute_ticket_mutation_with_outbox(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        extra = (turn, turn, "TICKET_CREATED", "customer:1", Json({}))
    else:
        sql = "SELECT execute_idempotent_operation(%s,%s,%s,%s,%s,%s)"
        extra = ()

    cursor.execute("SAVEPOINT invalid_ticket")
    with pytest.raises(Error):
        cursor.execute(sql, (*args[:-1], Json({**payload, "pending_handoff": {"session_id": "durable-session"}}), *extra))
    cursor.execute("ROLLBACK TO SAVEPOINT invalid_ticket")
    cursor.execute("SELECT count(*) FROM tickets WHERE subject = %s", (turn,))
    assert cursor.fetchone() == (0,)

    cursor.execute(sql, (*args, *extra))
    ticket_id = cursor.fetchone()[0]["ticket_id"]
    cursor.execute(sql, (*args, *extra))
    assert cursor.fetchone()[0]["ticket_id"] == ticket_id
    if with_outbox:
        cursor.execute("SELECT count(*) FROM outbox_events WHERE source_event_id = %s", (turn,))
        assert cursor.fetchone() == (1,)

    cursor.execute("SELECT get_pending_handoff(1,%s), get_pending_handoff(2,%s)", (turn, turn))
    row, wrong_customer = cursor.fetchone()
    assert row["ticket_id"] == ticket_id
    assert row["handoff_request_id"] == child
    assert wrong_customer is None
    cursor.execute("SELECT complete_pending_handoff(1,%s), complete_pending_handoff(1,%s)", (turn, turn))
    cursor.execute("SELECT get_pending_handoff(1,%s)", (turn,))
    assert cursor.fetchone() == (None,)


@pytest.mark.asyncio
async def test_exhausted_transcript_restored_by_new_writer(monkeypatch, tmp_path):
    path = tmp_path / "runtime/transcripts.json"
    monkeypatch.setattr(transcript, "_RETRY_DELAYS", ())
    writer = transcript.TranscriptWriter(path)
    reached = asyncio.Event()

    async def unavailable(_):
        reached.set()
        raise httpx.ConnectError("offline")

    writer.start(unavailable)
    writer.submit("session", snapshot(4))
    writer.submit("session", snapshot(3))
    await asyncio.wait_for(reached.wait(), 2)
    await writer.stop()
    assert json.loads(path.read_text())["session"]["p_session_revision"] == 4

    recovered = []
    writer2 = transcript.TranscriptWriter(path)
    async def write(data):
        recovered.append(data)

    writer2.start(write)
    await writer2.stop()
    assert [row["p_session_revision"] for row in recovered] == [4]
    assert json.loads(path.read_text()) == {}


def test_ingestion_flushes_before_reading_next_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "knowledge_base_dir", str(tmp_path))
    monkeypatch.setattr(ingest_knowledge, "BATCH_SIZE", 2)
    (tmp_path / "a.md").write_text("A" * 1600, encoding="utf-8")
    (tmp_path / "b.md").write_text("B" * 200, encoding="utf-8")
    calls = []
    original = ingest_knowledge.iter_file_chunks

    def track(path, limit):
        calls.append(path.name)
        yield from original(path, limit)

    pinecone = Mock()
    pinecone.inference.embed.side_effect = lambda **kwargs: [SimpleNamespace(values=[0.5]) for _ in kwargs["inputs"]]
    def upsert(*, vectors):
        calls.append(("upsert", len(vectors)))
    pinecone.Index.return_value.upsert.side_effect = upsert
    monkeypatch.setattr(ingest_knowledge, "Pinecone", lambda **_: pinecone)
    monkeypatch.setattr(ingest_knowledge, "iter_file_chunks", track)
    ingest_knowledge.ingest()
    assert calls.index(("upsert", 2)) < calls.index("b.md")
    assert all(item[1] <= 2 for item in calls if isinstance(item, tuple))

    monkeypatch.setattr(settings, "max_kb_file_bytes", 4)
    before = list(calls)
    with pytest.raises(ValueError, match="MAX_KB_FILE_BYTES"):
        ingest_knowledge.ingest()
    assert calls == before


@pytest.mark.parametrize("name", ["db_server", "filesystem_server", "web_search_server", "gmail_server"])
def test_standalone_mcp_import(name):
    assert importlib.import_module(f"app.mcp.{name}").mcp


@pytest.mark.asyncio
async def test_mcp_http_requires_staff_and_dispatches_tools(monkeypatch):
    monkeypatch.setattr(tools, "supabase", Mock())
    tools.supabase.rpc.return_value.execute.return_value.data = {"total_tickets": 2}
    token = lambda role: jwt.encode({"role": role, "sub": "operator"}, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    protocol = {"Accept": "application/json, text/event-stream"}
    async with mcp_http.lifespan(mcp_http):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            assert (await client.post("/mcp", json=payload)).status_code == 401
            assert (await client.post("/mcp", headers={"Authorization": "Bearer " + token("customer")}, json=payload)).status_code == 403
            for role in ("agent", "admin"):
                headers = {**protocol, "Authorization": "Bearer " + token(role)}
                response = await client.post("/mcp", headers=headers, json=payload)
                assert response.status_code == 200, response.text
                names = {t["name"] for t in response.json()["result"]["tools"]}
                assert {"get_dashboard_stats", "create_ticket", "update_ticket", "cancel_order"} <= names
                response = await client.post("/mcp", headers=headers, json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "get_dashboard_stats", "arguments": {}},
                })
                result = response.json()["result"]
                assert not result.get("isError"), result
                assert "total_tickets" in result["content"][0]["text"]
    assert tools.supabase.rpc.call_count == 2

@pytest.mark.asyncio
async def test_guardian_adds_trusted_handoff_metadata(monkeypatch):
    from app.agents import domain_graph
    from app.agents.guardian import GuardianAction, GuardianDecision

    create = Mock(return_value='{"ticket_id":42}')
    monkeypatch.setattr(domain_graph, "create_ticket", create)
    turn = uuid4()
    decision = GuardianDecision(action=GuardianAction.CREATE_TICKET, reason="Billing needs review")
    assert await domain_graph._apply_guardian_ticket_decision({
        "client_request_id": str(turn), "customer_id": 1, "session_id": "session",
        "message": "Billing issue", "intent": "billing_issue", "semantic_domain": "payment",
        "sentiment": "negative", "urgency": "high",
    }, decision) == (42, False)
    metadata = create.call_args.kwargs["pending_handoff"]
    assert metadata["handoff_request_id"] == str(uuid5(turn, "escalate_session:session"))
    assert metadata["session_id"] == "session"
    assert metadata["escalation_reason"] == decision.reason
