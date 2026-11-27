import json
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatchcase
from html import escape
from pathlib import Path
from unittest.mock import Mock

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import routes
from app.agents import graph
from app.config import settings
from app.outbox.worker import render_notification
from app.rag import retriever


@pytest.mark.parametrize(("action", "tool", "params", "args", "kwargs"), [
    ("lookup_customer", "get_customer_by_id", {"identifier": "b@example.com"}, (123,), {}),
    ("get_chat_history", "get_chat_history", {"customer_id": 456}, (), {"customer_id": 123}),
    ("track_order", "track_order", {"customer_id": 456, "order_id": 54}, (),
     {"customer_id": 123, "order_id": 54}),
])
def test_customer_tool_uses_authenticated_identity(monkeypatch, action, tool, params, args, kwargs):
    lookup = Mock(return_value='{"customer_id":123,"result":"customer A"}')
    monkeypatch.setattr(graph, tool, lookup)

    assert json.loads(graph._execute_tool(action, params, 123)) == {
        "customer_id": 123, "result": "customer A",
    }
    lookup.assert_called_once_with(*args, **kwargs)


@pytest.mark.parametrize("customer_id", [None, 0, -1, True, "123"])
@pytest.mark.parametrize(("action", "tool"), [
    ("lookup_customer", "get_customer_by_id"),
    ("get_chat_history", "get_chat_history"),
    ("track_order", "track_order"),
])
def test_customer_tools_reject_invalid_principal(monkeypatch, action, tool, customer_id):
    lookup = Mock()
    monkeypatch.setattr(graph, tool, lookup)
    response = graph._execute_tool(action, {"customer_id": 456}, customer_id)
    assert json.loads(response) == {"error": "Authenticated customer required"}
    lookup.assert_not_called()


@pytest.mark.parametrize(("path", "excluded"), [
    ("token.json", True), ("credentials.json", True),
    ("nested/token.json", True), ("nested/credentials.json", True),
    (".env", True), (".env.local", True), (".env.example", False),
    ("logs/service.log", True), ("scratch/local.txt", True),
    (".pytest_cache/README.md", True), ("app/main.py", False),
])
def test_dockerignore_wave1_patterns(path, excluded):
    rules = Path(__file__).resolve().parents[1].joinpath(".dockerignore").read_text().splitlines()
    ignored = False
    for rule in rules:
        pattern = rule.lstrip("!").rstrip("/")
        if not pattern or pattern.startswith("#"):
            continue
        # These rules use anchored globs, directory prefixes, and **/filename.
        if ("/" not in path and fnmatchcase(path, pattern)) or (
            rule.endswith("/") and path.startswith(pattern + "/")
        ) or (pattern.startswith("**/") and fnmatchcase(path, pattern)):
            ignored = not rule.startswith("!")
    assert ignored is excluded


@pytest.fixture
def admin_client(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    monkeypatch.setattr(settings, "admin_api_key", "unit-test-admin-key-with-32-characters")
    monkeypatch.setattr(routes, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(routes, "get_system_info", lambda: {"service": "test"})
    ingestion = Mock(return_value={"processed": 1})
    monkeypatch.setattr(retriever, "ingest_knowledge_base", ingestion, raising=False)
    return TestClient(app), ingestion


def _auth(role):
    if role == "key":
        return {"X-API-Key": settings.admin_api_key}
    if role is None:
        return {}
    token = jwt.encode(
        {"sub": "123", "role": role, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(("role", "expected"), [
    ("agent", 403), ("customer", 403), ("admin", 200), ("key", 200), (None, 401),
])
def test_admin_write_roles(admin_client, role, expected):
    client, ingestion = admin_client
    headers = _auth(role)
    response = client.post("/api/v1/admin/llm-settings", headers=headers, json={"model": "test-model"})
    assert response.status_code == expected
    response = client.post("/api/v1/admin/knowledge-base/reingest", headers=headers)
    assert response.status_code == expected
    if expected == 200:
        assert json.loads(routes.SETTINGS_FILE.read_text()) == {"model": "test-model", "provider": routes.settings.llm_provider}
        ingestion.assert_called_once_with()
    else:
        assert not routes.SETTINGS_FILE.exists()
        ingestion.assert_not_called()


def test_agent_keeps_operator_reads_but_cannot_read_admin_controls(admin_client):
    client, _ = admin_client
    headers = _auth("agent")
    assert client.get("/api/v1/admin/system-info", headers=headers).status_code == 200
    assert client.get("/api/v1/admin/llm-settings", headers=headers).status_code == 403
    assert client.get("/api/v1/admin/knowledge-base", headers=headers).status_code == 403


def test_email_escapes_body_but_preserves_subject():
    source = '<script>alert("input" & \'example\')</script>'
    subject, body = render_notification(
        "CUSTOM_EMAIL", {"subject": source, "message_content": source, "message": source},
    )
    assert subject == source
    assert source not in body
    assert body.count(escape(source, quote=True)) == 3


@pytest.mark.parametrize(("kind", "field"), [
    ("TICKET_CREATED", "ticket_id"), ("ORDER_UPDATED", "order_id"),
])
def test_email_escapes_interpolated_ids(kind, field):
    source = "<b>123</b>"
    _, body = render_notification(kind, {field: source})
    assert source not in body
    assert escape(source, quote=True) in body


def test_admin_preview_uses_markdown_without_raw_html():
    source = (
        Path(__file__).resolve().parents[2] / "frontend/app/admin/knowledge/page.js"
    ).read_text(encoding="utf-8")
    assert "<ReactMarkdown skipHtml>" in source
    assert "dangerouslySetInnerHTML" not in source
    assert "renderSimpleMarkdown" not in source
