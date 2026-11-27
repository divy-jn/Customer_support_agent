import asyncio
import importlib.util
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4, uuid5

import httpx
import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from app import email_service, llm_factory, routes, tools
from app.agents import approval_service, general_agent
from app.config import Settings, settings
from app.escalation.service import EscalationService
from app.main import app, limiter
from app.models import EscalationLifecycleStatus
from app.persistence import escalation, transcript
from app.websocket import chat_handler, rate_limiter
from tests.test_wave5_hardening import local_sessions, snapshot


@pytest.fixture
def customer_socket(monkeypatch, local_sessions):
    db = Mock()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
    monkeypatch.setattr(tools, "supabase", db)
    monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
    monkeypatch.setattr(chat_handler, "get_pending_handoff", Mock(return_value=None))
    monkeypatch.setattr(chat_handler, "complete_pending_handoff", Mock())
    monkeypatch.setattr(rate_limiter, "consume_customer_message", AsyncMock(return_value=True))
    sent = AsyncMock()
    monkeypatch.setattr(chat_handler.manager, "send_personal_message", sent)
    return AsyncMock(), sent


@pytest.mark.asyncio
async def test_replay_recovers_postgres_handoff_after_session_write_failure(monkeypatch, local_sessions, customer_socket):
    from app.persistence.session import SessionStore

    socket, sent = customer_socket
    await local_sessions.save_session("handoff", snapshot(1))
    monkeypatch.setattr(escalation, "ensure_durable_escalation_state", lambda *_: EscalationLifecycleStatus.NONE)
    turn_id = uuid4()
    handoff_id = uuid5(turn_id, "escalate_session:handoff")
    message = "I have a billing issue"
    committed = {}

    async def run_guardian(_):
        committed.update({
            "session_id": "handoff", "ticket_id": 42, "handoff_request_id": str(handoff_id),
            "escalation_reason": "billing", "payload": {"message": message},
        })
        local_sessions.save_session_conditional = AsyncMock(side_effect=RuntimeError("session write failed"))
        return {"response": "Connecting you about ticket #42.", "escalated": True,
                "guardian_action": "create_ticket", "guardian_ticket_id": 42}

    plan = AsyncMock(side_effect=run_guardian)
    monkeypatch.setattr(chat_handler, "domain_support_graph", SimpleNamespace(ainvoke=plan))
    monkeypatch.setattr(chat_handler, "get_pending_handoff", lambda *_: committed or None)
    execute = Mock(side_effect=[TimeoutError("transition unavailable"), SimpleNamespace(is_success=True)])
    monkeypatch.setattr(EscalationService, "escalate_session", execute)

    raw = json.dumps({"message": message, "client_request_id": str(turn_id)})
    socket.receive_text.side_effect = [raw, WebSocketDisconnect()]
    await chat_handler.handle_customer_ws(socket, "handoff", 17)
    local_sessions.save_session_conditional.assert_awaited_once()
    chat_handler.complete_pending_handoff.assert_not_called()

    fresh_store = SessionStore()
    monkeypatch.setattr(chat_handler, "session_store", fresh_store)
    tools.supabase.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
        {"customer_id": 17, "session_revision": 0},
    ]
    socket.receive_text.side_effect = [raw, WebSocketDisconnect()]
    await chat_handler.handle_customer_ws(socket, "handoff", 17)

    plan.assert_awaited_once()
    assert [call.args[0].client_request_id for call in execute.call_args_list] == [handoff_id, handoff_id]
    chat_handler.complete_pending_handoff.assert_called_once_with(17, str(turn_id))
    assert (await fresh_store.get_session("handoff"))["mode"] == "human"
    assert any(call.args[0].get("escalated") is True for call in sent.await_args_list)


@pytest.mark.asyncio
async def test_schema_cache_failure_keeps_human_session(monkeypatch, local_sessions, customer_socket):
    socket, sent = customer_socket
    await local_sessions.save_session("human", {**snapshot(1), "mode": "human"})
    db = Mock()
    table = db.table.return_value
    table.insert.return_value.execute.side_effect = RuntimeError("PGRST205")
    table.select.return_value.eq.return_value.execute.side_effect = RuntimeError("PGRST205")
    monkeypatch.setattr(escalation, "supabase", db)
    plan = AsyncMock(return_value={"response": "AI response"})
    monkeypatch.setattr(chat_handler, "domain_support_graph", SimpleNamespace(ainvoke=plan))
    socket.receive_text.side_effect = [
        json.dumps({"message": "Hello", "client_request_id": str(uuid4())}), WebSocketDisconnect(),
    ]
    await chat_handler.handle_customer_ws(socket, "human", 17)
    plan.assert_not_awaited()
    assert (await local_sessions.get_session("human"))["mode"] == "human"
    assert any("verifying session state" in call.args[0].get("message", "") for call in sent.await_args_list)
    with pytest.raises(escalation.EscalationStateUnavailable):
        await asyncio.to_thread(escalation.ensure_durable_escalation_state, "human", 17)


@pytest.mark.parametrize("name,args,kwargs", [
    ("create_ticket", (7, "Subject", "Description"), {}),
    ("update_ticket", (42,), {"status": "closed"}),
    ("cancel_order", (13,), {}),
])
def test_staff_mcp_mutations_require_request_id_and_delegate(monkeypatch, name, args, kwargs):
    from app.mcp import db_server

    tool = getattr(db_server, name)
    invoke = getattr(tool, "fn", tool)
    transaction = Mock(return_value='{"status": "success"}')
    monkeypatch.setattr(tools, name, transaction)
    monkeypatch.delenv("MCP_DB_STAFF_MODE", raising=False)
    key = str(uuid4())
    assert "Staff-only" in invoke(*args, **kwargs, client_request_id=key)
    monkeypatch.setenv("MCP_DB_STAFF_MODE", "true")
    assert "client_request_id" in invoke(*args, **kwargs)
    transaction.assert_not_called()
    assert json.loads(invoke(*args, **kwargs, client_request_id=key)) == {"status": "success"}
    assert transaction.call_args.kwargs["client_request_id"] == key


def test_mcp_customer_lookup_is_disabled_without_staff_mode(monkeypatch):
    from app.mcp import db_server

    monkeypatch.delenv("MCP_DB_STAFF_MODE", raising=False)
    db = Mock()
    monkeypatch.setattr(db_server, "supabase", db)
    tool = db_server.lookup_customer
    assert "Staff-only" in getattr(tool, "fn", tool)("Some customer")
    db.table.assert_not_called()


def test_order_update_escapes_html_and_keeps_subject_plain(monkeypatch):
    send = Mock()
    monkeypatch.setattr(email_service, "_send_email", send)
    payload = "<script>alert(1)</script>"
    email_service.send_order_update_email("safe@example.invalid", payload, 7, payload, payload)
    assert payload not in send.call_args.args[2]
    assert send.call_args.args[2].count("&lt;script&gt;") == 3
    assert send.call_args.args[1] == "[Support Team] Order #7 Update"


@pytest.mark.asyncio
async def test_approval_email_without_customer_skips_send_and_reads_off_loop(monkeypatch):
    db = Mock()
    loop_thread = threading.get_ident()
    seen_threads = []
    def fetch():
        seen_threads.append(threading.get_ident())
        return SimpleNamespace(data=[])
    db.table.return_value.select.return_value.eq.return_value.execute.side_effect = fetch
    monkeypatch.setattr(tools, "supabase", db)
    monkeypatch.setattr(tools, "cancel_order", Mock(return_value='{"status":"success"}'))
    send = Mock()
    monkeypatch.setattr(email_service, "send_order_update_email", send)

    approval = approval_service.create_pending_approval(
        action="cancel_order", params={"order_id": 7}, customer_id=7, session_id="order", message="Confirm?",
    )
    result = await approval_service.execute_approved_action(approval, 7, "order")
    assert result.success
    send.assert_not_called()
    assert seen_threads[0] != loop_thread


@pytest.mark.asyncio
async def test_general_name_query_runs_off_event_loop(monkeypatch):
    db = Mock()
    loop_thread = threading.get_ident()
    def fetch():
        assert threading.get_ident() != loop_thread
        return SimpleNamespace(data=[{"name": "Ada"}])
    db.table.return_value.select.return_value.eq.return_value.execute.side_effect = fetch
    monkeypatch.setattr(tools, "supabase", db)
    result = await general_agent.handle_general_request({"message": "what is my name", "customer_id": 7})
    assert "Ada" in result["response"]


@pytest.mark.asyncio
async def test_transcript_is_available_after_exhaustion_and_flushes_again(monkeypatch):
    monkeypatch.setattr(transcript, "_RETRY_DELAYS", ())
    monkeypatch.setattr(transcript, "_FLUSH_INTERVAL", 3600, raising=False)
    writer = transcript.TranscriptWriter()
    attempts = []
    started = asyncio.Event()

    async def write(data):
        attempts.append(data["p_session_revision"])
        if len(attempts) == 1:
            started.set()
            raise httpx.ConnectError("offline")

    writer.start(write)
    writer.submit("session", snapshot(3))
    await asyncio.wait_for(started.wait(), 2)
    await writer.stop()
    assert attempts == [3, 3]
    assert not writer._pending


def test_application_limit_applies_across_routes(monkeypatch):
    monkeypatch.setattr(routes, "get_dashboard_stats", lambda: '{"total_tickets":0}')
    monkeypatch.setattr(settings, "agent_secret", "staff-test")
    from app import tools
    monkeypatch.setattr(tools, "check_supabase_health", lambda: {"status": "healthy"})
    headers = {"X-Agent-Token": "staff-test"}
    # Exercise the configured global limit while keeping this test's budget small.
    group, = limiter._application_limits or limiter._default_limits
    monkeypatch.setattr(group, "_LimitGroup__limit_provider", "2/minute")
    limiter.reset()
    try:
        client = TestClient(app)
        assert client.get("/api/v1/dashboard/stats", headers=headers).status_code == 200
        assert client.get("/health", headers=headers).status_code == 200
        assert client.get("/api/v1/dashboard/stats", headers=headers).status_code == 429
    finally:
        limiter.reset()


@pytest.mark.parametrize("credential", ["AGENT_SECRET", "ADMIN_API_KEY"])
def test_production_approval_secret_cannot_match_bearer_credential(credential):
    secret = "scope-conflict-value-that-is-long-enough"
    bearer_secrets = {"AGENT_SECRET": "", "ADMIN_API_KEY": "", credential: secret}
    with pytest.raises(ValueError, match="dedicated APPROVAL_SIGNING_SECRET"):
        Settings(
            _env_file=None, APP_ENVIRONMENT="production", DEBUG_MODE=False,
            SUPABASE_URL="https://test-project.supabase.co", SUPABASE_KEY="service-key",
            DATABASE_PASSWORD="test-password", SUPPORT_TEAM_EMAIL="support@unit-testing.dev",
            JWT_SECRET="independent-jwt-key-that-is-long-enough",
            APPROVAL_SIGNING_SECRET=secret, **bearer_secrets,
        )


@pytest.mark.asyncio
async def test_llm_success_expires_and_failure_immediately_downgrades(monkeypatch):
    key = ("model", "project", "region")
    monkeypatch.setattr(llm_factory, "_provider_success", {
        key: (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),
    })
    assert llm_factory.last_provider_success(*key) is None
    llm = llm_factory._NormalizedChatGoogleGenerativeAI.model_construct(
        model="models/model", project="project", location="region",
    )
    llm._record_provider_success()
    monkeypatch.setattr(llm_factory.ChatGoogleGenerativeAI, "_agenerate", AsyncMock(side_effect=RuntimeError))
    with pytest.raises(RuntimeError):
        await llm._agenerate([])
    assert llm_factory.last_provider_success(*key) is None


def test_manual_websocket_client_import_has_no_side_effect(monkeypatch):
    dispatch = Mock()
    monkeypatch.setattr(asyncio, "run", dispatch)
    path = Path(__file__).resolve().parents[1] / "test_ws.py"
    spec = importlib.util.spec_from_file_location("manual_ws_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dispatch.assert_not_called()
    assert not hasattr(module, "test")


def test_docker_contexts_include_kb_and_exclude_frontend_environment():
    root = Path(__file__).resolve().parents[2]
    frontend_rules = (root / "frontend/.dockerignore").read_text().splitlines()
    assert {".env", ".env.*", "!.env.example"} <= set(frontend_rules)
    backend_rules = (root / "backend/.dockerignore").read_text().splitlines()
    assert "knowledge_base/" not in backend_rules
    assert (root / "backend/knowledge_base/payment_methods.md").is_file()
