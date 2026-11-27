"""Offline regression checks for the default Bedrock integration."""

import json
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app import llm_factory
from app.agents.graph import GraphLLMAdapter
from app.config import settings
from app.tools import check_llm_health


@pytest.fixture
def bedrock_settings(monkeypatch, tmp_path):
    path = tmp_path / "model-settings.json"
    monkeypatch.setattr(llm_factory, "SETTINGS_FILE", path)
    monkeypatch.setattr(llm_factory, "_bedrock_success", {})
    monkeypatch.setattr(settings, "llm_provider", "bedrock")
    monkeypatch.setattr(settings, "llm_model", "deepseek.v3.2")
    monkeypatch.setattr(settings, "aws_region", "us-east-1")
    return path


def test_get_llm_passes_raw_bedrock_model_and_region(monkeypatch, bedrock_settings):
    assert issubclass(llm_factory._ObservedBedrockConverse, llm_factory.ChatBedrockConverse)
    constructor = Mock()
    monkeypatch.setattr(llm_factory, "_ObservedBedrockConverse", constructor)
    llm_factory.get_llm(temperature=0.1, request_timeout=20)
    args = constructor.call_args.kwargs
    assert args["model"] == "deepseek.v3.2"
    assert args["region_name"] == "us-east-1"
    assert args["temperature"] == 0.1
    assert args["config"].read_timeout == 20


def test_adapter_switches_model_on_next_turn(monkeypatch, bedrock_settings):
    constructor = Mock(side_effect=[object(), object()])
    monkeypatch.setattr(llm_factory, "_ObservedBedrockConverse", constructor)
    adapter = GraphLLMAdapter()
    first = adapter.llm
    assert adapter.llm is first

    bedrock_settings.write_text(json.dumps({"provider": "bedrock", "model": "qwen.qwen3-next-80b-a3b"}))
    assert adapter.llm is not first
    assert [call.kwargs["model"] for call in constructor.call_args_list] == [
        "deepseek.v3.2", "qwen.qwen3-next-80b-a3b",
    ]


def test_bedrock_default_ignores_legacy_gemini_override(bedrock_settings):
    bedrock_settings.write_text('{"model":"gemini-2.5-flash"}', encoding="utf-8")
    assert llm_factory.resolve_model() == "deepseek.v3.2"


@pytest.mark.asyncio
async def test_bedrock_health_follows_success_and_failure(monkeypatch, bedrock_settings):
    assert check_llm_health() == {
        "provider": "bedrock", "model": "deepseek.v3.2", "region": "us-east-1", "status": "configured",
    }
    llm = llm_factory._ObservedBedrockConverse.model_construct(model_id="deepseek.v3.2", region_name="us-east-1")
    response = ChatResult(generations=[ChatGeneration(message=AIMessage(content=[
        {"type": "reasoning", "reasoning": "internal"},
        {"type": "text", "text": "Hello"},
    ]))])
    provider = Mock(side_effect=[response, RuntimeError("provider unavailable")])
    monkeypatch.setattr(llm_factory.ChatBedrockConverse, "_generate", provider)

    result = await llm._agenerate([])
    assert result.generations[0].message.content == "Hello"
    health = check_llm_health()
    assert health["status"] == "healthy"
    success_at = datetime.fromisoformat(health["checked_at"])
    assert success_at.utcoffset() == timedelta(0)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await llm._agenerate([])
    assert check_llm_health()["status"] == "configured"
    assert "checked_at" not in check_llm_health()


def test_bedrock_health_expires(bedrock_settings):
    from datetime import timezone

    llm_factory._bedrock_success[("deepseek.v3.2", "us-east-1")] = (
        datetime.now(timezone.utc) - timedelta(minutes=5, seconds=1)
    ).isoformat()
    assert check_llm_health()["status"] == "configured"
