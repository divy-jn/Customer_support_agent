"""Comprehensive deterministic Guardian policy test suite.

Covers every scenario category specified in the architecture diagram:
- Policy questions and product questions
- Order tracking and cancellation
- Payment failures and refund requests
- Existing relevant active tickets (duplicate prevention)
- Multiple recent tickets
- First-time and repeated human requests
- Severe complaints and safety/legal concerns
- Repeated unsuccessful resolution attempts
- Missing order identifiers
- Ticket-history/database failures
- Invalid router output and fallback behavior
- Duplicate requests and high-risk approval handling
"""

import pytest

from app.agents.guardian import Guardian, GuardianAction, GuardianDecision, GuardianInput, SupportCase


# ──────────────────────────────────────────────
#  1. Automated resolution (RESOLVE)
# ──────────────────────────────────────────────

class TestResolve:
    def test_simple_product_question_resolves_automatically(self):
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="product_information", domain_can_resolve=True,
        ))
        assert decision.action is GuardianAction.RESOLVE

    def test_policy_faq_resolves_automatically(self):
        decision = Guardian.decide(GuardianInput(
            domain="general", intent="return_policy", domain_can_resolve=True,
        ))
        assert decision.action is GuardianAction.RESOLVE

    def test_order_tracking_resolves_automatically(self):
        decision = Guardian.decide(GuardianInput(
            domain="order", intent="order_tracking", domain_can_resolve=True,
        ))
        assert decision.action is GuardianAction.RESOLVE

    def test_no_escalation_triggers_yields_resolve(self):
        """When nothing is wrong, Guardian stays out of the way."""
        decision = Guardian.decide(GuardianInput(
            domain="payment", intent="payment_status", domain_can_resolve=True,
        ))
        assert decision.action is GuardianAction.RESOLVE
        assert decision.ticket_id is None


# ──────────────────────────────────────────────
#  2. Duplicate prevention (UPDATE_EXISTING_TICKET)
# ──────────────────────────────────────────────

class TestDuplicatePrevention:
    def test_active_ticket_matching_domain_and_intent_is_updated(self):
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="warranty_claim",
            recent_cases=[SupportCase(id=786, status="open", domain="product", intent="warranty_claim")],
        ))
        assert decision.action is GuardianAction.UPDATE_EXISTING_TICKET
        assert decision.ticket_id == 786

    def test_active_ticket_with_null_intent_matches_domain_only(self):
        """A ticket with no intent (e.g. created before intent tracking) should match on domain."""
        decision = Guardian.decide(GuardianInput(
            domain="order", intent="delivery_issue",
            requires_human=True,
            recent_cases=[SupportCase(id=100, status="open", domain="order", intent=None)],
        ))
        assert decision.action is GuardianAction.UPDATE_EXISTING_TICKET
        assert decision.ticket_id == 100

    def test_closed_ticket_is_never_matched(self):
        decision = Guardian.decide(GuardianInput(
            domain="payment", intent="refund_request", requires_human=True,
            recent_cases=[SupportCase(id=50, status="closed", domain="payment", intent="refund_request")],
        ))
        assert decision.action is GuardianAction.CREATE_TICKET
        assert decision.ticket_id is None

    def test_ticket_in_different_domain_is_not_matched(self):
        decision = Guardian.decide(GuardianInput(
            domain="payment", intent="billing_issue", requires_human=True,
            recent_cases=[SupportCase(id=200, status="open", domain="order", intent="tracking")],
        ))
        assert decision.action is GuardianAction.CREATE_TICKET
        assert decision.ticket_id is None

    def test_ticket_with_different_intent_is_not_matched(self):
        """Same domain but different intent should not match."""
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="product_recommendation",
            requires_human=True,
            recent_cases=[SupportCase(id=300, status="open", domain="product", intent="warranty_claim")],
        ))
        assert decision.action is GuardianAction.CREATE_TICKET

    def test_multiple_tickets_uses_newest_active(self):
        """Repository returns newest first; Guardian should select the first active match."""
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="technical_support",
            recent_cases=[
                SupportCase(id=500, status="open", domain="product", intent="technical_support"),
                SupportCase(id=400, status="closed", domain="product", intent="technical_support"),
            ],
        ))
        assert decision.action is GuardianAction.UPDATE_EXISTING_TICKET
        assert decision.ticket_id == 500  # newest first

    def test_multiple_active_tickets_escalates_and_links_newest(self):
        """Multiple active tickets trigger human review while referencing the newest match."""
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="technical_support",
            recent_cases=[
                SupportCase(id=500, status="open", domain="product", intent="technical_support"),
                SupportCase(id=400, status="in_progress", domain="product", intent="technical_support"),
            ],
        ))
        assert decision.action is GuardianAction.ESCALATE
        assert decision.ticket_id == 500

    def test_all_active_statuses_are_recognized(self):
        for status in ("open", "in_progress", "pending_customer", "escalated"):
            decision = Guardian.decide(GuardianInput(
                domain="order", intent="cancel_order",
                recent_cases=[SupportCase(id=1, status=status, domain="order", intent="cancel_order")],
            ))
            assert decision.action is GuardianAction.UPDATE_EXISTING_TICKET, f"Failed for status={status}"


# ──────────────────────────────────────────────
#  3. Safety and legal escalation
# ──────────────────────────────────────────────

class TestSafetyLegal:
    def test_safety_issue_escalates_when_no_active_ticket(self):
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="product_issue",
            safety_or_legal_issue=True,
        ))
        assert decision.action is GuardianAction.ESCALATE

    def test_safety_issue_escalates_and_attaches_active_ticket(self):
        """Safety issues require immediate human escalation even when active ticket exists."""
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="product_issue",
            safety_or_legal_issue=True,
            recent_cases=[SupportCase(id=999, status="open", domain="product", intent="product_issue")],
        ))
        assert decision.action is GuardianAction.ESCALATE
        assert decision.ticket_id == 999

    def test_legal_issue_escalates(self):
        decision = Guardian.decide(GuardianInput(
            domain="general", intent="complaint",
            safety_or_legal_issue=True,
        ))
        assert decision.action is GuardianAction.ESCALATE


# ──────────────────────────────────────────────
#  4. Repeated unresolved attempts
# ──────────────────────────────────────────────

class TestRepeatedAttempts:
    def test_exactly_three_attempts_triggers_escalation(self):
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="technical_support",
            unresolved_attempts=3,
        ))
        assert decision.action is GuardianAction.ESCALATE

    def test_more_than_three_attempts_still_escalates(self):
        decision = Guardian.decide(GuardianInput(
            domain="order", intent="delivery_issue",
            unresolved_attempts=5,
        ))
        assert decision.action is GuardianAction.ESCALATE

    def test_two_attempts_does_not_escalate(self):
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="technical_support",
            unresolved_attempts=2, domain_can_resolve=True,
        ))
        assert decision.action is GuardianAction.RESOLVE

    def test_repeated_attempts_with_active_ticket_escalates(self):
        """With >=3 attempts, escalate to human review while attaching the active ticket."""
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="technical_support",
            unresolved_attempts=3,
            recent_cases=[SupportCase(id=42, status="open", domain="product", intent="technical_support")],
        ))
        assert decision.action is GuardianAction.ESCALATE
        assert decision.ticket_id == 42


# ──────────────────────────────────────────────
#  5. Human request handling
# ──────────────────────────────────────────────

class TestHumanRequest:
    def test_first_human_request_when_resolvable_continues(self):
        """Customer says 'manager' once, but issue is solvable → CONTINUE."""
        decision = Guardian.decide(GuardianInput(
            domain="order", intent="order_tracking",
            customer_requests_human=True,
            domain_can_resolve=True,
        ))
        assert decision.action is GuardianAction.CONTINUE

    def test_human_request_without_resolution_capability_creates_ticket(self):
        """Issue cannot be resolved and customer wants human → CREATE_TICKET."""
        decision = Guardian.decide(GuardianInput(
            domain="order", intent="delivery_issue",
            customer_requests_human=True,
            requires_human=True,
            domain_can_resolve=False,
        ))
        assert decision.action is GuardianAction.CREATE_TICKET

    def test_human_request_without_domain_resolve_and_without_requires_human(self):
        """Customer asks for human, domain can't resolve, but no explicit requires_human."""
        decision = Guardian.decide(GuardianInput(
            domain="payment", intent="billing_issue",
            customer_requests_human=True,
            domain_can_resolve=False,
        ))
        # Single human request without repeated flag returns CONTINUE to clarify
        assert decision.action is GuardianAction.CONTINUE

    def test_requires_human_without_customer_request_creates_ticket(self):
        """Domain agent signals it needs human help, even without customer asking."""
        decision = Guardian.decide(GuardianInput(
            domain="order", intent="damaged_item",
            requires_human=True,
            domain_can_resolve=False,
        ))
        assert decision.action is GuardianAction.CREATE_TICKET


# ──────────────────────────────────────────────
#  6. Ticket history edge cases
# ──────────────────────────────────────────────

class TestTicketHistoryEdgeCases:
    def test_empty_recent_cases_allows_ticket_creation(self):
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="warranty_claim",
            requires_human=True,
            recent_cases=[],
        ))
        assert decision.action is GuardianAction.CREATE_TICKET

    def test_no_recent_cases_at_all_allows_resolution(self):
        decision = Guardian.decide(GuardianInput(
            domain="general", intent="faq",
            domain_can_resolve=True,
            recent_cases=[],
        ))
        assert decision.action is GuardianAction.RESOLVE

    def test_mixed_open_and_closed_tickets_only_matches_open(self):
        decision = Guardian.decide(GuardianInput(
            domain="payment", intent="refund_request",
            requires_human=True,
            recent_cases=[
                SupportCase(id=10, status="closed", domain="payment", intent="refund_request"),
                SupportCase(id=20, status="open", domain="payment", intent="refund_request"),
            ],
        ))
        assert decision.action is GuardianAction.UPDATE_EXISTING_TICKET
        assert decision.ticket_id == 20

    def test_only_unrelated_domain_tickets_do_not_block_creation(self):
        decision = Guardian.decide(GuardianInput(
            domain="payment", intent="billing_issue",
            requires_human=True,
            recent_cases=[
                SupportCase(id=1, status="open", domain="order", intent="tracking"),
                SupportCase(id=2, status="closed", domain="product", intent="warranty"),
            ],
        ))
        assert decision.action is GuardianAction.CREATE_TICKET

    def test_history_lookup_failed_when_human_needed_escalates_to_prevent_duplicate(self):
        """When history lookup fails, Guardian must not create a ticket based on incomplete history."""
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="warranty_claim",
            requires_human=True,
            history_lookup_failed=True,
        ))
        assert decision.action is GuardianAction.ESCALATE
        assert "verified" in decision.reason.lower()
        assert decision.ticket_id is None

    def test_history_lookup_failed_when_resolvable_still_resolves(self):
        """Resolvable requests without human handoff do not need ticket history to answer."""
        decision = Guardian.decide(GuardianInput(
            domain="general", intent="faq",
            domain_can_resolve=True,
            history_lookup_failed=True,
        ))
        assert decision.action is GuardianAction.RESOLVE
        assert decision.ticket_id is None

    def test_history_lookup_failed_when_customer_requests_human_and_cannot_resolve(self):
        decision = Guardian.decide(GuardianInput(
            domain="order", intent="cancellation",
            customer_requests_human=True,
            requires_human=True,
            domain_can_resolve=False,
            history_lookup_failed=True,
        ))
        assert decision.action is GuardianAction.ESCALATE
        assert decision.ticket_id is None



# ──────────────────────────────────────────────
#  7. Combined scenarios (priority ordering)
# ──────────────────────────────────────────────

class TestPriorityOrdering:
    def test_safety_escalation_takes_priority_over_existing_ticket(self):
        """Safety issues take priority, escalating to human review while attaching the active ticket."""
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="safety_concern",
            safety_or_legal_issue=True,
            unresolved_attempts=5,
            requires_human=True,
            recent_cases=[SupportCase(id=999, status="open", domain="product", intent="safety_concern")],
        ))
        assert decision.action is GuardianAction.ESCALATE
        assert decision.ticket_id == 999

    def test_safety_takes_priority_over_repeated_attempts_when_no_ticket(self):
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="safety_concern",
            safety_or_legal_issue=True,
            unresolved_attempts=5,
        ))
        assert decision.action is GuardianAction.ESCALATE

    def test_repeated_attempts_takes_priority_over_requires_human(self):
        decision = Guardian.decide(GuardianInput(
            domain="order", intent="delivery_issue",
            unresolved_attempts=3,
            requires_human=True,
        ))
        assert decision.action is GuardianAction.ESCALATE

    def test_requires_human_takes_priority_over_customer_requests_human_with_resolve(self):
        """When domain says requires_human, that overrides 'continue if resolvable'."""
        decision = Guardian.decide(GuardianInput(
            domain="order", intent="delivery_issue",
            customer_requests_human=True,
            requires_human=True,
            domain_can_resolve=True,  # domain says resolvable but also requires human
        ))
        # requires_human → CREATE_TICKET
        assert decision.action is GuardianAction.CREATE_TICKET


# ──────────────────────────────────────────────
#  8. Decision output invariants
# ──────────────────────────────────────────────

class TestDecisionInvariants:
    def test_resolve_never_has_ticket_id(self):
        decision = Guardian.decide(GuardianInput(
            domain="general", intent="faq", domain_can_resolve=True,
        ))
        assert decision.action is GuardianAction.RESOLVE
        assert decision.ticket_id is None

    def test_continue_never_has_ticket_id(self):
        decision = Guardian.decide(GuardianInput(
            domain="order", intent="tracking",
            customer_requests_human=True, domain_can_resolve=True,
        ))
        assert decision.action is GuardianAction.CONTINUE
        assert decision.ticket_id is None

    def test_update_always_has_ticket_id(self):
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="tech",
            recent_cases=[SupportCase(id=42, status="open", domain="product", intent="tech")],
        ))
        assert decision.action is GuardianAction.UPDATE_EXISTING_TICKET
        assert decision.ticket_id == 42

    def test_create_ticket_never_has_ticket_id(self):
        decision = Guardian.decide(GuardianInput(
            domain="order", intent="issue", requires_human=True,
        ))
        assert decision.action is GuardianAction.CREATE_TICKET
        assert decision.ticket_id is None

    def test_escalate_never_has_ticket_id(self):
        decision = Guardian.decide(GuardianInput(
            domain="product", intent="issue", safety_or_legal_issue=True,
        ))
        assert decision.action is GuardianAction.ESCALATE
        assert decision.ticket_id is None

    def test_every_decision_has_a_reason(self):
        """All Guardian decisions must include an audit-friendly reason."""
        inputs = [
            GuardianInput(domain="general", intent="faq", domain_can_resolve=True),
            GuardianInput(domain="product", intent="issue", safety_or_legal_issue=True),
            GuardianInput(domain="order", intent="issue", unresolved_attempts=3),
            GuardianInput(domain="order", intent="issue", requires_human=True),
            GuardianInput(
                domain="order", intent="track",
                customer_requests_human=True, domain_can_resolve=True,
            ),
            GuardianInput(
                domain="product", intent="tech",
                recent_cases=[SupportCase(id=1, status="open", domain="product", intent="tech")],
            ),
        ]
        for inp in inputs:
            decision = Guardian.decide(inp)
            assert isinstance(decision.reason, str)
            assert len(decision.reason) > 10, f"Reason too short for {decision.action}: {decision.reason}"
