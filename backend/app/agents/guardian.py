"""Deterministic Guardian policy for ticket and escalation decisions.

This module is deliberately free of database and LLM calls.  Callers provide
customer-scoped ticket facts, which keeps the policy auditable and makes a
decision reproducible for a given input.
"""

from enum import Enum
from typing import Iterable

from pydantic import BaseModel, Field


class GuardianAction(str, Enum):
    RESOLVE = "resolve"
    CONTINUE = "continue"
    UPDATE_EXISTING_TICKET = "update_existing_ticket"
    CREATE_TICKET = "create_ticket"
    ESCALATE = "escalate"


class SupportCase(BaseModel):
    """The minimal, customer-scoped ticket projection used by Guardian."""

    id: int = Field(gt=0)
    status: str
    domain: str | None = None
    intent: str | None = None
    priority: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status.lower() in {"open", "in_progress", "pending_customer", "escalated"}


class GuardianInput(BaseModel):
    domain: str
    intent: str
    customer_requests_human: bool = False
    requires_human: bool = False
    safety_or_legal_issue: bool = False
    domain_can_resolve: bool = False
    unresolved_attempts: int = Field(default=0, ge=0)
    recent_cases: list[SupportCase] = Field(default_factory=list)
    history_lookup_failed: bool = False


class GuardianDecision(BaseModel):
    action: GuardianAction
    reason: str
    ticket_id: int | None = None


class Guardian:
    """Single authority for duplicate prevention and escalation eligibility."""

    @staticmethod
    def _matching_active_case(facts: GuardianInput) -> SupportCase | None:
        matches: Iterable[SupportCase] = (
            case
            for case in facts.recent_cases
            if case.is_active
            and case.domain == facts.domain
            and (case.intent is None or case.intent == facts.intent)
        )
        # The repository returns newest first.  Keeping that ordering makes the
        # selected ticket deterministic even if historical data is imperfect.
        return next(iter(matches), None)

    @classmethod
    def decide(cls, facts: GuardianInput) -> GuardianDecision:
        existing = cls._matching_active_case(facts)
        if existing:
            return GuardianDecision(
                action=GuardianAction.UPDATE_EXISTING_TICKET,
                ticket_id=existing.id,
                reason="An active ticket already covers this domain and intent; do not create a duplicate.",
            )

        if facts.safety_or_legal_issue:
            return GuardianDecision(
                action=GuardianAction.ESCALATE,
                reason="Safety or legal issues require immediate human escalation.",
            )

        if facts.unresolved_attempts >= 3:
            return GuardianDecision(
                action=GuardianAction.ESCALATE,
                reason="Three or more unresolved attempts require human escalation.",
            )

        if facts.requires_human:
            if facts.history_lookup_failed:
                return GuardianDecision(
                    action=GuardianAction.ESCALATE,
                    reason="Ticket history could not be verified; escalating to human review to prevent duplicate ticket operations.",
                )
            return GuardianDecision(
                action=GuardianAction.CREATE_TICKET,
                reason="The request needs human intervention and no matching active ticket exists.",
            )

        if facts.customer_requests_human and facts.domain_can_resolve:
            return GuardianDecision(
                action=GuardianAction.CONTINUE,
                reason="The domain agent can complete this authorized request before a handoff is needed.",
            )

        if facts.customer_requests_human and facts.history_lookup_failed:
            return GuardianDecision(
                action=GuardianAction.ESCALATE,
                reason="Ticket history could not be verified; escalating to human review to prevent duplicate ticket operations.",
            )

        return GuardianDecision(
            action=GuardianAction.RESOLVE,
            reason="No escalation trigger applies and the request remains eligible for automated resolution.",
        )
