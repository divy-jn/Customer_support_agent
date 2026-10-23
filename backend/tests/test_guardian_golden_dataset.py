"""Deterministic test runner for the versioned Guardian policy golden dataset.

Executes all scenarios in `tests/evals/guardian_policy_golden_v1.json` against
`Guardian.decide()` and the integration boundary.  Ensures 100% deterministic,
offline policy verification without requiring LLMs, databases, or paid APIs.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel, Field

from app.agents.domain_graph import (
    _apply_guardian_ticket_decision,
    guardian_response_node,
    route_after_supervisor,
)
from app.agents.guardian import (
    Guardian,
    GuardianAction,
    GuardianDecision,
    GuardianInput,
    SupportCase,
)
from app.agents.supervisor import SupervisorAction
from app.config import settings
from app.models import WorkflowState


DATASET_PATH = Path(__file__).parent / "evals" / "guardian_policy_golden_v1.json"


class ExpectedContract(BaseModel):
    action: str
    ticket_id: int | None = None
    escalated: bool
    mutation_allowed: bool


class ScenarioModel(BaseModel):
    id: str
    category: str
    description: str
    input_policy_facts: dict
    ticket_fixtures: dict
    expected: ExpectedContract


def load_scenarios() -> list[ScenarioModel]:
    with open(DATASET_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [ScenarioModel(**s) for s in data["scenarios"]]


SCENARIOS = load_scenarios()


# ──────────────────────────────────────────────
#  Dataset Schema & Integrity Validation
# ──────────────────────────────────────────────

def test_golden_dataset_schema_and_id_uniqueness():
    """Verify that every scenario in the dataset has a valid schema and unique ID."""
    assert len(SCENARIOS) >= 15
    seen_ids = set()
    for scen in SCENARIOS:
        assert scen.id not in seen_ids, f"Duplicate scenario ID: {scen.id}"
        seen_ids.add(scen.id)
        assert scen.category
        assert scen.description
        assert scen.expected.action in {a.value for a in GuardianAction}


# ──────────────────────────────────────────────
#  Deterministic Pure Policy Verification
# ──────────────────────────────────────────────

@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_guardian_policy_scenario(scenario: ScenarioModel):
    facts = scenario.input_policy_facts
    fixtures = scenario.ticket_fixtures

    lookup_failed = fixtures.get("lookup_outcome") in {"db_error", "malformed_json", "exception"}
    cases = [
        SupportCase(
            id=c["id"],
            status=c["status"],
            domain=c.get("domain"),
            intent=c.get("intent"),
            priority=c.get("priority"),
        )
        for c in fixtures.get("cases", [])
    ]

    inp = GuardianInput(
        domain=facts["domain"],
        intent=facts["intent"],
        customer_requests_human=facts.get("customer_requests_human", False),
        requires_human=facts.get("requires_human", False),
        safety_or_legal_issue=facts.get("safety_or_legal_issue", False),
        domain_can_resolve=facts.get("domain_can_resolve", False),
        unresolved_attempts=facts.get("unresolved_attempts", 0),
        recent_cases=cases,
        history_lookup_failed=lookup_failed,
    )

    decision = Guardian.decide(inp)

    # 1. Action matches
    assert decision.action.value == scenario.expected.action, (
        f"Scenario {scenario.id} failed: expected action {scenario.expected.action}, got {decision.action.value}"
    )

    # 2. Ticket ID matches
    assert decision.ticket_id == scenario.expected.ticket_id, (
        f"Scenario {scenario.id} failed: expected ticket_id {scenario.expected.ticket_id}, got {decision.ticket_id}"
    )

    # 3. Decision reason is present and meaningful
    assert isinstance(decision.reason, str) and len(decision.reason) > 5


# ──────────────────────────────────────────────
#  Deterministic Mutation Boundary Verification
# ──────────────────────────────────────────────

@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
@pytest.mark.asyncio
async def test_guardian_mutation_boundary_contract(scenario: ScenarioModel):
    """Verify that ticket mutations only occur when explicitly allowed."""
    facts = scenario.input_policy_facts
    fixtures = scenario.ticket_fixtures
    expected = scenario.expected

    decision = GuardianDecision(
        action=GuardianAction(expected.action),
        ticket_id=expected.ticket_id,
        reason=f"Test scenario {scenario.id}",
    )

    lookup_failed = fixtures.get("lookup_outcome") in {"db_error", "malformed_json", "exception"}
    state = {
        "message": f"Test message for {scenario.id}",
        "semantic_domain": facts["domain"],
        "intent": facts["intent"],
        "customer_id": facts.get("customer_id"),
        "client_request_id": facts.get("client_request_id"),
        "history_lookup_failed": lookup_failed,
    }

    with patch("app.agents.domain_graph.create_ticket") as mock_create, \
         patch("app.agents.domain_graph.update_ticket") as mock_update:

        mock_create.return_value = '{"ticket_id": 999}'
        mock_update.return_value = '{"status": "success"}'

        ticket_id, mutation_error = await _apply_guardian_ticket_decision(state, decision)

        if expected.mutation_allowed:
            assert not mutation_error, f"Expected successful mutation for {scenario.id}"
            if decision.action is GuardianAction.CREATE_TICKET:
                mock_create.assert_called_once()
                mock_update.assert_not_called()
            elif decision.action is GuardianAction.UPDATE_EXISTING_TICKET:
                mock_update.assert_called_once()
                mock_create.assert_not_called()
        else:
            # When mutation is not allowed, no ticket write should have executed successfully
            if decision.action in {GuardianAction.RESOLVE, GuardianAction.CONTINUE, GuardianAction.ESCALATE}:
                mock_create.assert_not_called()
                mock_update.assert_not_called()
            elif not facts.get("customer_id") or not facts.get("client_request_id") or lookup_failed:
                # If mutation action was attempted but missing ID or lookup failed, mutation must be flagged as error/blocked
                assert mutation_error is True
                mock_create.assert_not_called()
                mock_update.assert_not_called()


# ──────────────────────────────────────────────
#  Routing & State Transition Golden Verification
# ──────────────────────────────────────────────

@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.id for s in SCENARIOS])
def test_golden_scenario_routing_and_state_consistency(scenario: ScenarioModel, monkeypatch):
    """Prove that golden scenarios respect supervisor routing and state transitions."""
    monkeypatch.setattr(settings, "agent_architecture_mode", "v2_all")
    facts = scenario.input_policy_facts
    domain = facts["domain"]
    intent = facts["intent"]

    # 1. Verify supervisor routing matches the domain and policy intent
    route = route_after_supervisor({
        "semantic_domain": domain,
        "intent": intent,
        "supervisor_action": SupervisorAction.CONTINUE.value,
    })

    if domain == "product":
        assert route == "product"
    elif domain == "order":
        assert route in {"order", "legacy"}
    elif domain == "payment":
        assert route in {"payment", "legacy"}
    elif domain == "general":
        assert route == "general"
    elif domain == "escalation":
        assert route == "guardian"

    # 2. Verify state contract with unresolved_attempts
    workflow = WorkflowState.from_legacy({
        "session_id": f"sess-{scenario.id}",
        "customer_id": facts.get("customer_id") or 1,
        "active_domain": domain if domain != "escalation" else "general",
        "unresolved_attempts": facts.get("unresolved_attempts", 0),
    })
    assert workflow.unresolved_attempts == facts.get("unresolved_attempts", 0)
    dumped = workflow.model_dump(mode="json")
    assert dumped["unresolved_attempts"] == facts.get("unresolved_attempts", 0)
