"""Offline regressions for Wave 5's HTTP, WebSocket, and provider boundaries."""

import asyncio
import json
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.util import get_remote_address

from app import llm_factory, routes, tools
from app.agents import domain_graph
from app.agents.graph import GraphLLMAdapter
from app.agents.product_agent import DefaultProductLLMAdapter
from app.config import settings
from app.main import app
from app.persistence import session as sessions
from app.persistence.transcript import TranscriptWriter
from app.websocket import chat_handler
from app.websocket.connection import ConnectionManager
from scripts import ingest_knowledge


@pytest.fixture
def agent_headers(monkeypatch):
    monkeypatch.setattr(settings, "agent_secret", "wave5-agent")
    return {"X-Agent-Token": "wave5-agent"}


def test_default_rate_limit_is_enforced_on_real_route(monkeypatch, agent_headers):
    limiter = Limiter(key_func=get_remote_address, default_limits=["2/minute"], storage_uri="memory://")
    monkeypatch.setattr(app.state, "limiter", limiter)
    monkeypatch.setattr(routes, "get_dashboard_stats", lambda: '{"total_tickets": 0}')
    client = TestClient(app)
    codes = [
        client.get("/api/v1/dashboard/stats", headers=agent_headers).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]


@pytest.fixture
def local_sessions(monkeypatch):
    store = sessions.SessionStore()
    monkeypatch.setattr(sessions, "redis_client", None)
    monkeypatch.setattr(chat_handler, "session_store", store)
    monkeypatch.setattr(chat_handler, "manager", ConnectionManager())
    return store


def snapshot(revision, owner=17):
    return {
        "customer_id": owner,
        "customer_name": "Customer",
        "_session_revision": revision,
        "conversation_history": [{"role": "customer", "content": f"Revision {revision}"}],
    }


@pytest.mark.asyncio
async def test_transcript_writer_drains_retries_and_reports_permanent_error(monkeypatch, caplog):
    from app.persistence import transcript

    monkeypatch.setattr(transcript, "_RETRY_DELAYS", (0,))
    writer = TranscriptWriter()
    wrote_twelve = asyncio.Event()
    release = asyncio.Event()
    writes = []
    persisted = {}

    async def upsert(data):
        sid = data["p_session_id"]
        revision = data["p_session_revision"]
        writes.append((sid, revision))
        if sid == "permanent-error":
            raise ValueError("provider failure")
        if revision == 12:
            if writes.count((sid, 12)) == 1:
                raise httpx.ConnectError("temporary failure")
            wrote_twelve.set()
            await release.wait()
        current = persisted.get(sid)
        if current is None or (
            current["p_customer_id"] == data["p_customer_id"]
            and current["p_session_revision"] < revision
        ):
            persisted[sid] = data

    writer.start(upsert)
    writer.submit("active", snapshot(12))
    await asyncio.wait_for(wrote_twelve.wait(), 2)
    writer.submit("active", snapshot(11))
    writer.submit("unowned", snapshot(99, owner=None))
    writer.submit("permanent-error", snapshot(1))
    drain = asyncio.create_task(writer.stop())
    await asyncio.sleep(0)
    assert not drain.done()
    release.set()
    await asyncio.wait_for(drain, 2)
    assert writes == [("active", 12), ("active", 12), ("active", 11), ("permanent-error", 1)]
    assert persisted["active"]["p_transcript"] == snapshot(12)["conversation_history"]
    assert "Retrying transcript write for session active revision 12" in caplog.text
    assert "Transcript write failed for session permanent-error revision 1 (ValueError)" in caplog.text


@pytest.mark.asyncio
async def test_customer_disconnect_flushes_without_waiting_and_removes_presence(
    monkeypatch, local_sessions,
):
    await local_sessions.save_session("active", snapshot(12))
    monkeypatch.setattr(tools, "supabase", Mock())
    query = tools.supabase.table.return_value.select.return_value.eq.return_value
    query.execute.return_value = SimpleNamespace(data=[{"customer_id": 17, "session_revision": 11}])
    writer = TranscriptWriter()
    monkeypatch.setattr(chat_handler, "transcript_writer", writer)
    started = asyncio.Event()
    release = asyncio.Event()
    writes = []
    visible = []

    async def write(data):
        writes.append(data)
        started.set()
        await release.wait()

    socket = AsyncMock()

    async def receive():
        visible.extend(session["session_id"] for session in await chat_handler.get_all_active_sessions())
        raise WebSocketDisconnect()

    socket.receive_text.side_effect = receive
    writer.start(write)
    try:
        await asyncio.wait_for(chat_handler.handle_customer_ws(socket, "active", 17), 2)
        await asyncio.wait_for(started.wait(), 2)
        assert visible == ["active"]
        assert writes[0]["p_session_revision"] == 12
        assert writes[0]["p_ended_at"] is not None
        assert await chat_handler.get_all_active_sessions() == []
        assert (await local_sessions.get_session("active"))["conversation_history"]
    finally:
        release.set()
        await writer.stop()


@pytest.mark.asyncio
async def test_presence_loads_only_live_ids_from_redis(monkeypatch, local_sessions):
    redis = AsyncMock()
    redis.mget.return_value = [json.dumps(snapshot(3))]
    monkeypatch.setattr(sessions, "redis_client", redis)
    socket = AsyncMock()
    await chat_handler.manager.connect_customer(socket, "live")
    try:
        assert [s["session_id"] for s in await chat_handler.get_all_active_sessions()] == ["live"]
    finally:
        chat_handler.manager.disconnect_customer("live", socket)
    assert await chat_handler.get_all_active_sessions() == []
    redis.mget.assert_awaited_once_with("session:live")
    redis.keys.assert_not_called()


@pytest.mark.asyncio
async def test_restored_session_uses_durable_revision_for_next_mutation(local_sessions, monkeypatch):
    submit = Mock()
    monkeypatch.setattr(chat_handler, "transcript_writer", SimpleNamespace(submit=submit))
    prior = snapshot(12)["conversation_history"]
    restored = await chat_handler._get_session(
        "restored", 17, persisted={"customer_id": 17, "session_revision": 12, "transcript": prior},
    )
    assert restored["conversation_history"] == prior
    result, saved = await chat_handler._mutate_session("restored", lambda session: True, customer_id=17)
    assert saved
    assert result["_session_revision"] == 13
    submit.assert_called_once_with("restored", result)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["http", "guardian", "agent-takeover", "agent-release"])
async def test_blocking_queries_allow_async_heartbeats(monkeypatch, local_sessions, agent_headers, boundary):
    entered = threading.Event()
    release = threading.Event()

    def slow_query(*args):
        entered.set()
        assert release.wait(2), "The event loop did not run while the query was blocked"
        return result

    if boundary == "http":
        result = '{"total_tickets": 1}'
        monkeypatch.setattr(routes, "get_dashboard_stats", slow_query)

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/v1/dashboard/stats", headers=agent_headers)
                assert response.status_code == 200

    elif boundary == "guardian":
        result = ([], 0, False)
        monkeypatch.setattr(domain_graph, "_ticket_history", slow_query)

        async def run():
            state = {"message": "What are your hours?", "response": "We open at 9.", "customer_id": 17}
            assert (await domain_graph.guardian_response_node(state))["response"] == state["response"]

    else:
        result = SimpleNamespace(data=[])
        supabase = Mock()
        supabase.table.return_value.select.return_value.eq.return_value.execute = slow_query
        monkeypatch.setattr(tools, "supabase", supabase)
        socket = AsyncMock()
        socket.receive_text.side_effect = [
            json.dumps({"type": boundary.removeprefix("agent-"), "client_request_id": "req"}),
            WebSocketDisconnect(),
        ]

        async def run():
            await chat_handler.handle_agent_ws(socket, "agent-session")

    work = asyncio.create_task(run())
    try:
        assert await asyncio.to_thread(entered.wait, 1), f"{boundary} never made the blocking query"
        await asyncio.sleep(0)
        assert not work.done()
        assert not release.is_set()
    finally:
        release.set()
        await asyncio.wait_for(work, 3)


@pytest.mark.parametrize("count", [0, 1250])
def test_dashboard_rest_and_mcp_use_one_aggregate_rpc(monkeypatch, agent_headers, count):
    from app.mcp import db_server

    aggregate = {
        "total_tickets": count, "open_tickets": count, "escalated_tickets": 0,
        "avg_satisfaction": 4 if count else None,
        "by_status": {"open": count} if count else {},
        "by_type": {"general": count} if count else {},
        "by_priority": {"medium": count} if count else {},
    }
    supabase = Mock()
    supabase.rpc.return_value.execute.return_value = SimpleNamespace(data=aggregate)
    monkeypatch.setattr(tools, "supabase", supabase)
    response = TestClient(app).get("/api/v1/dashboard/stats", headers=agent_headers)
    assert response.status_code == 200
    assert response.json() == aggregate
    mcp_tool = db_server.get_dashboard_stats
    assert json.loads(getattr(mcp_tool, "fn", mcp_tool)()) == aggregate
    assert supabase.rpc.call_count == 2
    supabase.rpc.assert_called_with("get_ticket_statistics", {})
    supabase.table.assert_not_called()


def test_dashboard_returns_503_for_failed_rpc(monkeypatch, agent_headers):
    monkeypatch.setattr(routes, "get_dashboard_stats", lambda: '{"error": "database unavailable"}')
    assert TestClient(app).get("/api/v1/dashboard/stats", headers=agent_headers).status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_type", [GraphLLMAdapter, DefaultProductLLMAdapter])
async def test_model_change_applies_on_next_turn_and_reuses_adapter(monkeypatch, tmp_path, adapter_type):
    config = tmp_path / "settings.json"
    monkeypatch.setattr(llm_factory, "SETTINGS_FILE", str(config))
    create = Mock(side_effect=lambda model, **kwargs: SimpleNamespace(
        ainvoke=AsyncMock(return_value=SimpleNamespace(content=model)),
    ))
    monkeypatch.setattr(llm_factory, "get_llm", create)
    config.write_text('{"model":"first-model"}', encoding="utf-8")
    adapter = adapter_type()
    assert await adapter.invoke("system", "first turn") == "first-model"
    config.write_text('{"model":"second-model"}', encoding="utf-8")
    assert await adapter.invoke("system", "second turn") == "second-model"
    assert await adapter.invoke("system", "third turn") == "second-model"
    assert [call.kwargs["model"] for call in create.call_args_list] == ["first-model", "second-model"]


@pytest.mark.asyncio
async def test_llm_health_requires_observed_success_for_current_model(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_factory, "_provider_success", {})
    monkeypatch.setattr(llm_factory, "SETTINGS_FILE", str(tmp_path / "unused.json"))
    monkeypatch.setattr(settings, "llm_model", "health-model")
    monkeypatch.setattr(settings, "vertex_project_id", "test-project")
    provider = AsyncMock(side_effect=[RuntimeError("provider offline"), SimpleNamespace(generations=[])])
    monkeypatch.setattr(llm_factory.ChatGoogleGenerativeAI, "_agenerate", provider)
    llm = llm_factory._NormalizedChatGoogleGenerativeAI.model_construct(
        model="models/health-model", project="test-project", location=settings.vertex_location,
    )
    assert tools.check_llm_health()["status"] == "configured"
    assert "checked_at" not in tools.check_llm_health()
    with pytest.raises(RuntimeError, match="provider offline"):
        await llm._agenerate([])
    assert tools.check_llm_health()["status"] == "configured"
    await llm._agenerate([])
    health = tools.check_llm_health()
    assert health["status"] == "healthy"
    assert datetime.fromisoformat(health["checked_at"]).utcoffset().total_seconds() == 0
    monkeypatch.setattr(settings, "llm_model", "different-model")
    assert tools.check_llm_health()["status"] == "configured"


def test_split_keeps_paragraphs_and_bounds_long_words():
    assert ingest_knowledge.split_text(" \n\n  ", 500, 50) == []
    long_chunks = ingest_knowledge.split_text("x" * 700, 500, 50)
    assert len(long_chunks) == 2
    assert all(0 < len(chunk) <= 500 for chunk in long_chunks)
    assert long_chunks[0][-50:] == long_chunks[1][:50]
    paragraphs = "First paragraph.\n\nSecond paragraph."
    assert ingest_knowledge.split_text(paragraphs, 500, 0) == [paragraphs]
    with pytest.raises(ValueError, match="0 <= overlap < chunk_size"):
        ingest_knowledge.split_text("word", 500, 500)


@pytest.mark.parametrize("blank_file", [False, True])
def test_ingestion_fails_before_external_calls_when_no_documents(monkeypatch, tmp_path, blank_file):
    monkeypatch.setattr(settings, "knowledge_base_dir", str(tmp_path))
    if blank_file:
        (tmp_path / "blank.md").write_text("   \n", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        ingest_knowledge.ingest()


@pytest.mark.asyncio
async def test_admin_reingestion_surfaces_nonzero_exit(monkeypatch):
    import subprocess
    from fastapi import HTTPException
    from app.rag import retriever

    monkeypatch.delattr(retriever, "ingest_knowledge_base", raising=False)
    run = Mock(return_value=SimpleNamespace(returncode=1))
    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(HTTPException) as error:
        await routes.admin_reingest_kb(True)
    assert error.value.status_code == 503
    assert run.call_args.args[0][1:] == ["-m", "scripts.ingest_knowledge"]
