from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app import tools
from app.agents import domain_graph, rag_agent
from app.config import settings
from app.models import RouterDiagnostics, RouterFailureType, SemanticRouteResult


@pytest.fixture
def turn(monkeypatch):
    monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
    create = Mock(return_value='{"ticket_id": 99}')
    update = Mock(return_value='{"status": "success"}')
    legacy = AsyncMock(side_effect=AssertionError("Full V2 must not call the legacy graph"))
    monkeypatch.setattr(domain_graph, "create_ticket", create)
    monkeypatch.setattr(domain_graph, "update_ticket", update)
    monkeypatch.setattr(domain_graph.customer_support_graph, "ainvoke", legacy)
    yield create, update
    legacy.assert_not_awaited()


async def run_turn(monkeypatch, domain, intent, message, history=None):
    route = SemanticRouteResult(
        domain=domain, intent=intent, sentiment="neutral", urgency="medium",
        confidence=0.98, diagnostics=RouterDiagnostics(failure_type=RouterFailureType.SUCCESS),
    )
    monkeypatch.setattr(domain_graph.semantic_router, "classify_semantic_intent", AsyncMock(return_value=route))
    return await domain_graph.domain_support_graph.ainvoke({
        "message": message, "customer_id": 7, "session_id": "wave-3-session",
        "conversation_history": history or [{"role": "customer", "content": message}],
        "client_request_id": str(uuid4()),
    })


def assert_outcome(result, turn, *, action, ticket, creates=0, updates=0, text):
    assert result["guardian_action"] == action
    assert result.get("guardian_ticket_id") == ticket
    assert result["escalated"] is (action == "escalate")
    assert turn[0].call_count == creates
    assert turn[1].call_count == updates
    assert text.casefold() in result["response"].casefold()


@pytest.mark.asyncio
@pytest.mark.parametrize("repeat", [False, True], ids=["single-request", "identical-repeat"])
async def test_human_request_counts_prior_identical_message_only(monkeypatch, turn, repeat):
    monkeypatch.setattr(
        domain_graph, "find_customer_support_cases", Mock(return_value='{"cases": [], "recent_count": 0}')
    )
    history = [{"role": "customer", "content": "human"}]
    if repeat:
        history.extend([{"role": "agent", "content": "How can I help?"}, history[0].copy()])
    assert domain_graph._previously_requested_human(history, "human") is repeat
    result = await run_turn(monkeypatch, "escalation", "human_request", "human", history)
    assert_outcome(
        result, turn, action="escalate" if repeat else "continue", ticket=None,
        text="escalating this to a human" if repeat else "I can try to help",
    )


class TicketQuery:
    """Small in-memory adapter that applies the filters sent to PostgREST."""

    def __init__(self, rows, queries):
        self.rows = rows
        self.queries = queries
        self.filters = {}
        self.count = False

    def select(self, *columns, count=None, head=False):
        self.count = count == "exact" and head
        return self

    def eq(self, key, value):
        assert key == "customer_id"
        self.filters[key] = value
        self.rows = [row for row in self.rows if row[key] == value]
        return self

    def in_(self, key, values):
        self.filters[key] = values
        self.rows = [row for row in self.rows if row[key] in values]
        return self

    def gte(self, key, value):
        self.filters[key] = value
        self.rows = [row for row in self.rows if row[key] >= value]
        return self

    def lt(self, key, value):
        self.rows = [row for row in self.rows if row[key] < value]
        return self

    def order(self, key, *, desc):
        self.rows = sorted(self.rows, key=lambda row: row[key], reverse=desc)
        return self

    def limit(self, _size):
        return self

    def execute(self):
        self.queries.append((self.count, self.filters))
        # Simulate a server result cap below the requested limit.
        return SimpleNamespace(count=len(self.rows), data=[]) if self.count else SimpleNamespace(data=self.rows[:2])


def fake_ticket_db(monkeypatch, rows):
    queries = []
    table = Mock(side_effect=lambda _name: TicketQuery(rows, queries))
    monkeypatch.setattr(tools, "supabase", SimpleNamespace(table=table))
    return queries


def case(id_, *, age, status="open", subject="Issue: Warranty Claim", owner=7):
    return {
        "id": id_, "customer_id": owner, "status": status, "subject": subject,
        "created_at": (datetime.now(timezone.utc) - timedelta(hours=age)).isoformat(),
        "type": "technical_issue",
    }


def test_active_ticket_pagination_fails_closed_if_cursor_does_not_advance(monkeypatch):
    queries = fake_ticket_db(monkeypatch, [case(42, age=72)])
    monkeypatch.setattr(TicketQuery, "lt", lambda query, *_: query)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    result = tools.find_customer_support_cases(7, created_since=cutoff)
    assert "pagination did not advance" in result
    assert len(queries) == 3


@pytest.mark.asyncio
async def test_guardian_reuses_older_active_ticket_across_pages(monkeypatch, turn):
    queries = fake_ticket_db(monkeypatch, [
        case(42, age=72), case(43, age=75, subject="Issue: Other"),
        case(44, age=80, subject="Issue: Other"), case(45, age=72, owner=8),
    ])
    monkeypatch.setattr(domain_graph, "product_node", AsyncMock(return_value={"response": "I can check the warranty."}))
    result = await run_turn(monkeypatch, "product", "warranty_claim", "Can you check my warranty?")
    assert_outcome(result, turn, action="update_existing_ticket", ticket=42, updates=1, text="Ticket #42")
    assert turn[1].call_args.kwargs["ticket_id"] == 42
    assert all(filters["customer_id"] == 7 for _, filters in queries)
    assert all("created_at" not in filters for is_count, filters in queries if not is_count)


@pytest.mark.asyncio
async def test_guardian_counts_two_recent_closed_tickets(monkeypatch, turn):
    queries = fake_ticket_db(monkeypatch, [
        case(42, age=1, status="closed"), case(43, age=47, status="closed"),
        case(44, age=49, status="closed"), case(45, age=1, status="closed", owner=8),
    ])
    monkeypatch.setattr(domain_graph, "product_node", AsyncMock(return_value={"response": "I can check the warranty."}))
    result = await run_turn(monkeypatch, "product", "warranty_claim", "Can you check my warranty?")
    assert_outcome(result, turn, action="escalate", ticket=None, text="escalating this to a human")
    assert "multiple recent" in result["guardian_reason"].lower()
    count_filters = [filters for is_count, filters in queries if is_count]
    assert len(count_filters) == 1
    assert set(count_filters[0]) == {"customer_id", "created_at"}


@pytest.mark.asyncio
async def test_payment_method_uses_approved_document_without_ticket_write(monkeypatch, turn):
    fake_ticket_db(monkeypatch, [{**case(42, age=72, subject="Legacy ticket"), "type": "billing"}])
    monkeypatch.setattr(domain_graph, "handle_payment_request", AsyncMock(side_effect=AssertionError("payment flow called")))
    monkeypatch.setattr(rag_agent, "retrieve_as_context", Mock(side_effect=AssertionError("generic retrieval called")))

    async def answer(messages):
        system = messages[0]["content"]
        assert "Source: payment_methods.md" in system
        assert "**RuPay** (India's own payment network)" in system
        return SimpleNamespace(content="Yes, we accept RuPay cards.")

    monkeypatch.setattr(rag_agent, "get_rag_llm", lambda: SimpleNamespace(ainvoke=answer))
    result = await run_turn(monkeypatch, "payment", "payment_method", "Do you accept RuPay cards?")
    assert_outcome(result, turn, action="resolve", ticket=None, text="Yes, we accept RuPay cards.")
    assert result["tool_results"] == {"sources": ["payment_methods.md"], "context_used": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(("domain", "ticket_type"), [("order", "cancellation"), ("payment", "billing")])
async def test_full_v2_clarification_crosses_guardian_without_ticket_write(monkeypatch, turn, domain, ticket_type):
    fake_ticket_db(monkeypatch, [{**case(42, age=72, subject="Legacy ticket"), "type": ticket_type}])
    decide = Mock(wraps=domain_graph.Guardian.decide)
    monkeypatch.setattr(domain_graph.Guardian, "decide", decide)
    result = await run_turn(monkeypatch, domain, "unsupported_intent", "Can you change this for me?")
    assert_outcome(result, turn, action="resolve", ticket=None, text="clarify your request")
    decide.assert_called_once()
    assert decide.call_args.args[0].active_cases[0].id == 42
    assert result["workflow_state"]["turn_count"] == 1


@pytest.mark.asyncio
async def test_full_v2_router_initialization_failure_still_crosses_guardian(monkeypatch, turn):
    fake_ticket_db(monkeypatch, [])
    get_llm = Mock(side_effect=RuntimeError("router unavailable"))
    monkeypatch.setattr(domain_graph.semantic_router, "get_semantic_router_llm", get_llm)
    decide = Mock(wraps=domain_graph.Guardian.decide)
    monkeypatch.setattr(domain_graph.Guardian, "decide", decide)

    result = await domain_graph.domain_support_graph.ainvoke({
        "message": "Could you help me?", "session_id": "router-offline", "customer_id": 7,
    })
    assert_outcome(result, turn, action="resolve", ticket=None, text="clarify your request")
    decide.assert_called_once()
    get_llm.assert_called_once()
