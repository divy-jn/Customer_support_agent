import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from app.agents import domain_graph, grounded_response, order_agent
from app.config import settings
from app.models import RouterDiagnostics, RouterFailureType, SemanticRouteResult


def _classification(domain="unknown", intent="unknown"):
    return SemanticRouteResult(
        domain=domain, intent=intent, sentiment="neutral", urgency="low",
        confidence=0.9, diagnostics=RouterDiagnostics(failure_type=RouterFailureType.SUCCESS),
    )


@pytest.fixture
def offline_lookup(monkeypatch):
    monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
    classifier = AsyncMock(return_value=_classification())
    monkeypatch.setattr(domain_graph.semantic_router, "classify_semantic_intent", classifier)
    general = AsyncMock(side_effect=AssertionError("Lookup should not call the general agent"))
    monkeypatch.setattr(domain_graph, "handle_general_request", general)
    cases = Mock(return_value='{"cases": [], "recent_count": 0}')
    monkeypatch.setattr(domain_graph, "find_customer_support_cases", cases)
    for name in ("create_ticket", "update_ticket"):
        monkeypatch.setattr(domain_graph, name, Mock(side_effect=AssertionError("Unexpected ticket write")))
    model = SimpleNamespace(model_id="deepseek.v3.2", ainvoke=AsyncMock())
    factory = Mock(return_value=model)
    monkeypatch.setattr(grounded_response, "get_llm", factory)
    return SimpleNamespace(classifier=classifier, model=model, factory=factory, cases=cases)


async def _turn(message, previous=None, history=()):
    return await domain_graph.domain_support_graph.ainvoke({
        "message": message, "session_id": "test-lookup-context", "customer_id": 7,
        "workflow_state": previous and previous["workflow_state"],
        "conversation_history": [*history, {"role": "customer", "content": message}],
    })


def _mock_ticket(monkeypatch):
    read = Mock(return_value=json.dumps({
        "id": 642, "customer_id": 7, "subject": "Technical support",
        "status": "open", "priority": "medium",
        "updated_at": "2026-09-26T04:57:45.979908+00:00",
        "customers": {"name": "Internal Name", "email": "internal@example.test"},
    }))
    monkeypatch.setattr(domain_graph, "get_ticket", read)
    return read


@pytest.mark.asyncio
async def test_ticket_why_followup_synthesizes_new_question_from_verified_ticket(monkeypatch, offline_lookup):
    read = _mock_ticket(monkeypatch)
    model = offline_lookup.model.ainvoke
    model.side_effect = [
        SimpleNamespace(content="Ticket #642 is open."),
        SimpleNamespace(content="Ticket #642 is still open at medium priority. The last update was 26 Sep 2026, 04:57 UTC."),
    ]
    first = await _turn("status of ticket 642")
    followup = await _turn("why it is still not resolved", first, [
        {"role": "customer", "content": "status of ticket 642"},
        {"role": "agent", "content": first["response"]},
    ])

    messages = model.call_args.args[0]
    prompt = json.loads(messages[1]["content"])
    assert prompt["question"] == "why it is still not resolved"
    assert [turn["content"] for turn in prompt["recent_history"]] == ["status of ticket 642", first["response"]]
    assert prompt["reason_not_recorded"]
    assert prompt["verified_records"] == {
        "kind": "ticket", "records": [{
            "id": 642, "subject": "Technical support", "status": "Open", "priority": "Medium",
            "last_updated": "26 Sep 2026, 04:57 UTC",
        }],
    }
    assert followup["response"].startswith("Ticket #642 is still open at medium priority.")
    assert "doesn’t show a specific reason" in followup["response"]
    assert "Would you like a human agent to review this?" in followup["response"]
    assert read.call_args_list == [call(642, customer_id=7), call(642, customer_id=7)]
    assert first["workflow_state"]["lookup_context"] == {"kind": "ticket", "record_id": 642}
    assert followup["workflow_state"]["lookup_context"] == first["workflow_state"]["lookup_context"]
    assert not followup["escalated"]
    offline_lookup.cases.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError])
async def test_model_failure_uses_deterministic_ticket_template(monkeypatch, offline_lookup, failure):
    _mock_ticket(monkeypatch)
    offline_lookup.model.ainvoke.side_effect = failure
    result = await _turn("status of ticket 642")
    assert result["response"] == (
        "**Ticket #642** — Technical support. Status: Open. "
        "Priority: Medium. Last updated: 26 Sep 2026, 04:57 UTC."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("payload", "expected"), [
    ({"error": "missing", "error_code": "not_found"}, "couldn’t find"),
    ({"id": 642, "customer_id": 9, "subject": "private", "status": "open"}, "couldn’t find"),
    ({"error": "private connector error", "error_code": "lookup_failed"}, "Please try again"),
])
async def test_invalid_ticket_results_bypass_synthesis(monkeypatch, offline_lookup, payload, expected):
    monkeypatch.setattr(domain_graph, "get_ticket", Mock(return_value=json.dumps(payload)))
    result = await _turn("status of ticket 642")
    assert expected in result["response"]
    assert "private" not in result["response"]
    offline_lookup.factory.assert_not_called()


@pytest.mark.asyncio
async def test_active_ticket_followup_requeries_the_list(monkeypatch, offline_lookup):
    offline_lookup.classifier.return_value = _classification("general", "ticket_status")
    offline_lookup.cases.return_value = json.dumps({
        "cases": [{"id": 642, "subject": "Support", "status": "open"}],
        "recent_count": 0,
    })
    offline_lookup.model.ainvoke.side_effect = [
        SimpleNamespace(content="One ticket is open."),
        SimpleNamespace(content="Ticket #642 is the one still open."),
    ]
    first = await _turn("my active tickets")
    offline_lookup.classifier.return_value = _classification()
    followup = await _turn("which one is still open?", first)
    assert first["workflow_state"]["lookup_context"] == {"kind": "active_tickets", "record_id": None}
    assert followup["response"] == "Ticket #642 is the one still open."
    assert offline_lookup.cases.call_count == 2
    assert all(entry.args == (7,) for entry in offline_lookup.cases.call_args_list)


@pytest.mark.asyncio
async def test_order_history_followup_synthesizes_list_instead_of_tracking_stale_order(monkeypatch, offline_lookup):
    orders = Mock(return_value=json.dumps({"customer_id": 7, "recent_orders": [
        {"id": 42, "status": "shipped", "order_date": "2026-09-15T10:55:47.580611+00:00"},
        {"id": 43, "status": "delivered"},
    ]}))
    track = Mock(side_effect=AssertionError("Unexpected single order lookup"))
    monkeypatch.setattr(order_agent, "get_customer_history", orders)
    monkeypatch.setattr(order_agent, "track_order", track)
    offline_lookup.classifier.return_value = _classification("order", "view_order_history")
    offline_lookup.model.ainvoke.side_effect = [
        SimpleNamespace(content="Your orders are #42 and #43."),
        SimpleNamespace(content='{"reply":"Order #42 is the one that shipped.","order_id":42}'),
    ]
    first = await _turn("show my order history", {
        "workflow_state": {
            "session_id": "test-lookup-context", "customer_id": 7, "active_domain": "order",
            "global_status": "in_progress", "order_state": {"domain_status": "in_progress", "order_id": "999"},
        },
    })
    offline_lookup.classifier.return_value = _classification("order", "track_order")
    followup = await _turn("which one is shipped?", first)
    prompt = json.loads(offline_lookup.model.ainvoke.call_args.args[0][1]["content"])
    assert prompt["question"] == "which one is shipped?"
    assert prompt["verified_records"]["records"][0]["placed_at"] == "15 Sep 2026, 10:55 UTC"
    assert followup["response"] == "Order #42 is the one that shipped."
    assert followup["workflow_state"]["lookup_context"] == {"kind": "order", "record_id": 42}
    assert orders.call_args_list == [call(7), call(7)]
    track.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe_reply", [
    "Order #42 is delayed because the warehouse is closed.",
    "Order #42 will arrive today.",
    "I’m escalating this request to a human agent.",
    "Your handoff is in progress.",
    "Your refund has been approved.",
])
async def test_single_order_followup_requeries_owned_order_and_rejects_unsafe_reply(
    monkeypatch, offline_lookup, unsafe_reply,
):
    track = Mock(return_value='{"id":42,"customer_id":7,"status":"shipped","tracking_number":"SYN-42"}')
    monkeypatch.setattr(order_agent, "track_order", track)
    offline_lookup.classifier.return_value = _classification("order", "track_order")
    offline_lookup.model.ainvoke.side_effect = [
        SimpleNamespace(content="Order #42 has shipped."),
        SimpleNamespace(content=unsafe_reply),
    ]
    first = await _turn("track order #42")
    offline_lookup.classifier.return_value = _classification()
    followup = await _turn("why is it delayed?", first)
    assert track.call_args_list == [call(42, 7), call(42, 7)]
    assert followup["response"].startswith("Order #42 is currently shipped.")
    assert "doesn’t show a specific reason" in followup["response"]
    assert not followup["escalated"]


@pytest.mark.asyncio
@pytest.mark.parametrize("unresolved_attempts", [0, 3])
async def test_explicit_escalation_rechecks_active_ticket_and_skips_synthesis(
    monkeypatch, offline_lookup, unresolved_attempts,
):
    read = _mock_ticket(monkeypatch)
    offline_lookup.model.ainvoke.return_value = SimpleNamespace(content="Ticket #642 is open.")
    first = await _turn("status of ticket 642")
    first["workflow_state"]["unresolved_attempts"] = unresolved_attempts
    followup = await _turn("escalate it", first)
    assert read.call_args_list == [call(642, customer_id=7), call(642, customer_id=7)]
    assert followup["guardian_action"] == "escalate"
    assert followup["guardian_ticket_id"] == 642
    assert followup["explicit_lookup_escalation"]
    offline_lookup.model.ainvoke.assert_awaited_once()



@pytest.fixture
def recent_orders(monkeypatch):
    rows = [
        {"id": 451, "customer_id": 7, "status": "active", "order_date": "2026-09-16T10:55:47Z",
         "products": {"name": "PixelPro 9"}},
        {"id": 1, "customer_id": 7, "status": "delivered", "order_date": "2026-09-15T10:55:47Z",
         "products": {"name": "PixelPro 9"}},
        {"id": 151, "customer_id": 7, "status": "delivered", "order_date": "2026-04-18T10:55:47Z",
         "products": {"name": "Headphones"}},
    ]
    history = Mock(return_value=json.dumps({"customer_id": 7, "recent_orders": rows}))
    exact = Mock(return_value=json.dumps(rows[1]))
    monkeypatch.setattr(order_agent, "get_customer_history", history)
    monkeypatch.setattr(order_agent, "track_order", exact)
    return SimpleNamespace(history=history, exact=exact)


def _model_selection(reply, order_id):
    return SimpleNamespace(content=json.dumps({"reply": reply, "order_id": order_id}))


@pytest.mark.asyncio
async def test_latest_order_details_fetch_owned_orders_without_an_id(offline_lookup, recent_orders):
    offline_lookup.model.ainvoke.return_value = _model_selection("Order #451 for PixelPro 9 is active.", 451)

    result = await _turn("can u gove me order details of latest order")

    assert result["response"] == "Order #451 for PixelPro 9 is active."
    assert result["workflow_state"]["order_state"]["order_id"] == "451"
    assert result["workflow_state"]["lookup_context"] == {"kind": "order", "record_id": 451}
    recent_orders.history.assert_called_once_with(7)
    recent_orders.exact.assert_not_called()


@pytest.mark.asyncio
async def test_order_followup_chain_resolves_descriptions_and_missing_date(offline_lookup, recent_orders):
    questions = ["which was the latest delivered", "and delivered on which date?", "this pixel pro order"]
    offline_lookup.model.ainvoke.side_effect = [
        _model_selection("Order #1 was the latest placed order marked delivered.", 1),
        SimpleNamespace(content="Order #1 is marked delivered. Its exact delivery date is not recorded."),
        _model_selection("Order #1 is your delivered PixelPro 9 order.", 1),
    ]
    results = []
    history = []
    previous = None
    for question in questions:
        previous = await _turn(question, previous, history)
        results.append(previous)
        history.extend([
            {"role": "customer", "content": question},
            {"role": "agent", "content": previous["response"]},
        ])

    assert all(result["workflow_state"]["lookup_context"] == {"kind": "order", "record_id": 1} for result in results)
    prompts = [json.loads(item.args[0][1]["content"]) for item in offline_lookup.model.ainvoke.await_args_list]
    assert [prompt["question"] for prompt in prompts] == questions
    assert prompts[0]["verified_records"]["latest_matching_order_id"] == 1
    assert prompts[1]["verified_records"]["records"] == [{
        "id": 1, "product": "PixelPro 9", "status": "delivered", "tracking_number": None,
        "placed_at": "15 Sep 2026, 10:55 UTC", "delivered_at": None,
    }]
    assert prompts[2]["verified_records"]["active_order_id"] == 1
    assert prompts[2]["recent_history"][-2]["content"] == questions[1]
    assert "exact delivery date is not recorded" in results[1]["response"]
    assert "Order #1" in results[2]["response"]
    assert recent_orders.history.call_args_list == [call(7), call(7)]
    recent_orders.exact.assert_called_once_with(1, 7)


@pytest.mark.asyncio
@pytest.mark.parametrize("selected_id", [None, 999])
async def test_ambiguous_or_unverified_order_selection_does_not_set_an_active_id(
    offline_lookup, recent_orders, selected_id,
):
    reply = "Which PixelPro 9 order did you mean?" if selected_id is None else "Order #999 has shipped."
    offline_lookup.model.ainvoke.return_value = _model_selection(reply, selected_id)

    result = await _turn("this pixel pro order")

    assert result["workflow_state"]["lookup_context"] == {"kind": "order_history", "record_id": None}
    assert result["workflow_state"]["order_state"]["order_id"] is None
    assert "999" not in result["response"]
    if selected_id is None:
        assert result["response"] == reply
    recent_orders.history.assert_called_once_with(7)
    recent_orders.exact.assert_not_called()



@pytest.mark.asyncio
async def test_latest_order_uses_full_timestamp_when_orders_share_a_minute(offline_lookup, recent_orders):
    payload = json.loads(recent_orders.history.return_value)
    payload["recent_orders"][0]["order_date"] = "2026-09-15T10:55:48Z"
    recent_orders.history.return_value = json.dumps(payload)
    # The model selects a real but older order. Fall back to the verified latest.
    offline_lookup.model.ainvoke.return_value = _model_selection("Order #1 was your latest.", 1)

    result = await _turn("order details of latest order")

    assert "Order #451" in result["response"]
    assert "Placed: 15 Sep 2026, 10:55 UTC" in result["response"]
    assert result["workflow_state"]["lookup_context"] == {"kind": "order", "record_id": 451}


@pytest.mark.asyncio
async def test_order_selection_accepts_fenced_json(offline_lookup, recent_orders):
    body = _model_selection("Order #1 is your delivered PixelPro 9 order.", 1).content
    fence = chr(96) * 3
    offline_lookup.model.ainvoke.return_value = SimpleNamespace(content=f"{fence}json\n{body}\n{fence}")

    result = await _turn("this delivered pixel pro order")

    assert result["response"] == "Order #1 is your delivered PixelPro 9 order."
    assert result["workflow_state"]["lookup_context"] == {"kind": "order", "record_id": 1}
