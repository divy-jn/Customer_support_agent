import asyncio
import os
from unittest.mock import Mock, patch

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langsmith import Client, tracing_context
from langsmith.run_helpers import trace

from app import _configure_tracing_environment
from app.agents import db_agent
from app.llm_factory import _ObservedBedrockConverse


def test_dotenv_bootstrap_respects_process_aliases(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LANGSMITH_TRACING=true\nLANGCHAIN_PROJECT=from-file\nSUPABASE_KEY=ignored\n",
        encoding="utf-8",
    )
    for key in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING", "LANGSMITH_PROJECT", "LANGCHAIN_PROJECT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LANGCHAIN_PROJECT", "from-process")
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    try:
        _configure_tracing_environment(env_file)
        assert os.environ["LANGSMITH_TRACING"] == "true"
        assert os.environ["LANGCHAIN_PROJECT"] == "from-process"
        assert "LANGSMITH_PROJECT" not in os.environ
        assert "SUPABASE_KEY" not in os.environ
    finally:
        os.environ.pop("LANGSMITH_TRACING", None)


async def test_bedrock_and_legacy_planner_trace_under_one_turn(monkeypatch):
    bedrock = _ObservedBedrockConverse.model_construct(model_id="deepseek.v3.2", region_name="us-east-1")
    response = ChatResult(generations=[ChatGeneration(message=AIMessage(content="test"))])
    planner = FakeListChatModel(responses=["test"])
    monkeypatch.setattr(db_agent, "get_db_llm", lambda: planner)
    monkeypatch.setattr(type(planner), "with_structured_output", lambda self, *a, **kw: self)

    client = Mock(spec=Client)
    with tracing_context(enabled=True, client=client), trace("test-turn") as turn:
        with patch("langchain_aws.ChatBedrockConverse._generate", return_value=response):
            await bedrock.ainvoke("synthetic input")
        await asyncio.to_thread(db_agent.determine_db_action, "synthetic input", "order_status", 1)

    assert [child.run_type for child in turn.child_runs] == ["llm", "llm"]
    assert {child.parent_run_id for child in turn.child_runs} == {turn.id}
    assert client.create_run.call_count == 3



def test_workflow_telemetry_keeps_labels_and_excludes_payloads(caplog):
    import logging

    from app.middleware.tracking import emit_workflow_turn

    with caplog.at_level(logging.INFO, logger="tracking"):
        emit_workflow_turn({
            "intent": "view_order_history", "raw_classifier_intent": "  OrdeR_History_Request  ",
            "semantic_confidence": 0.95, "is_continuation": True,
            "entity_keys": ["order_id", "private name"],
            "entities": {"order_id": "private value"}, "semantic_failure_type": "success",
            "pre_turn_workflow_domain": "order", "message": "private message",
        })
        emit_workflow_turn({"intent": "synthetic-person@example.test", "raw_classifier_intent": "private arbitrary text"})
    event, unexpected_intent = caplog.records
    assert event.raw_classifier_intent == "  OrdeR_History_Request  "
    assert event.semantic_confidence == 0.95
    assert event.is_continuation is True
    assert event.entity_keys == ["order_id"]
    assert event.semantic_failure_type == "success"
    assert event.pre_turn_workflow_domain == "order"
    assert "entities" not in event.__dict__
    assert "private message" not in str(event.__dict__)
    assert unexpected_intent.raw_classifier_intent == unexpected_intent.intent == "[redacted]"
