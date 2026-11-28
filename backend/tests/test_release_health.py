import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest

from app import llm_factory, tools
from app.config import settings
from app.main import app, health_check
from app.outbox import lifecycle


def test_health_reports_version_last_success_and_standalone_worker(tmp_path, monkeypatch):
    marker = tmp_path / "heartbeat.json"
    marker.write_text('{"dispatch": "running", "reconciliation": "running"}', encoding="utf-8")
    monkeypatch.setenv("OUTBOX_HEARTBEAT_PATH", str(marker))
    monkeypatch.setenv("BUILD_GIT_SHA", "a" * 40)
    monkeypatch.setattr(settings, "outbox_worker_mode", "standalone")
    monkeypatch.setattr(settings, "outbox_worker_enabled", True)
    monkeypatch.setattr(settings, "reconciliation_enabled", True)
    monkeypatch.setattr(settings, "llm_provider", "bedrock")
    monkeypatch.setattr(llm_factory, "resolve_model", lambda: "deepseek.v3.2")
    monkeypatch.setattr(llm_factory, "_last_success", {})
    monkeypatch.setattr(llm_factory, "_bedrock_success", {})
    model_key = ("deepseek.v3.2", settings.aws_region)
    llm_factory._record_bedrock_success(model_key)
    last_success = llm_factory._bedrock_success.pop(model_key)
    monkeypatch.setattr(tools, "check_supabase_health", lambda: {"status": "healthy"})

    result = health_check()
    assert result["version"] == app.version
    assert result["git_sha"] == "a" * 40
    assert result["last_model_success_at"] == last_success
    assert result["outbox"] == {"mode": "standalone", "dispatch": "running", "reconciliation": "running"}

    stale = time.time() - 60
    os.utime(marker, (stale, stale))
    assert health_check()["outbox"]["dispatch"] == "unavailable"

    monkeypatch.setattr(settings, "outbox_worker_enabled", False)
    monkeypatch.setattr(settings, "reconciliation_enabled", False)
    assert health_check()["outbox"] == {"mode": "standalone", "dispatch": "disabled", "reconciliation": "disabled"}


@pytest.mark.asyncio
async def test_standalone_heartbeat_tracks_worker_and_shutdown(tmp_path, monkeypatch):
    marker = tmp_path / "heartbeat.json"
    monkeypatch.setenv("OUTBOX_HEARTBEAT_PATH", str(marker))
    monkeypatch.setattr(settings, "reconciliation_enabled", False)
    async with lifecycle.publish_standalone_heartbeat(SimpleNamespace(running=True)):
        await asyncio.sleep(0)
        assert json.loads(marker.read_text(encoding="utf-8")) == {
            "dispatch": "running", "reconciliation": "disabled",
        }
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "dispatch": "unavailable", "reconciliation": "disabled",
    }
