from app.agents.guardian import Guardian, GuardianAction, GuardianInput, SupportCase


_ACTIVE_PRODUCT_CASE = SupportCase(
    id=786, status="open", domain="product", intent="warranty_claim"
)


def test_safety_or_legal_issue_preempts_existing_ticket():
    decision = Guardian.decide(GuardianInput(
        domain="product", intent="warranty_claim", safety_or_legal_issue=True,
        recent_cases=[_ACTIVE_PRODUCT_CASE],
    ))

    assert decision.action is GuardianAction.ESCALATE
    assert decision.ticket_id == 786


def test_matching_active_case_is_updated_without_an_escalation_trigger():
    decision = Guardian.decide(GuardianInput(
        domain="product", intent="warranty_claim", domain_can_resolve=True,
        recent_cases=[_ACTIVE_PRODUCT_CASE],
    ))

    assert decision.action is GuardianAction.UPDATE_EXISTING_TICKET
    assert decision.ticket_id == 786


def test_repeated_human_request_escalates_existing_case():
    decision = Guardian.decide(GuardianInput(
        domain="product", intent="warranty_claim", customer_requests_human=True,
        customer_repeats_human_request=True, domain_can_resolve=True,
        recent_cases=[_ACTIVE_PRODUCT_CASE],
    ))

    assert decision.action is GuardianAction.ESCALATE
    assert decision.ticket_id == 786


def test_single_human_request_does_not_force_escalation_by_itself():
    decision = Guardian.decide(GuardianInput(
        domain="order", intent="order_tracking", customer_requests_human=True,
        domain_can_resolve=True,
    ))

    assert decision.action is GuardianAction.CONTINUE


def test_multiple_active_recent_cases_escalate():
    decision = Guardian.decide(GuardianInput(
        domain="general", intent="faq", domain_can_resolve=True,
        recent_cases=[
            SupportCase(id=10, status="open", domain="order", intent="tracking"),
            SupportCase(id=11, status="in_progress", domain="payment", intent="billing_issue"),
        ],
    ))

    assert decision.action is GuardianAction.ESCALATE
    assert "multiple active recent" in decision.reason.lower()


def test_high_severity_concern_escalates_even_with_existing_case():
    decision = Guardian.decide(GuardianInput(
        domain="payment", intent="billing_issue", sentiment="negative", urgency="high",
        recent_cases=[SupportCase(id=20, status="open", domain="payment", intent="billing_issue")],
    ))

    assert decision.action is GuardianAction.ESCALATE
    assert decision.ticket_id == 20


def test_unrelated_or_closed_cases_never_block_a_new_ticket():
    decision = Guardian.decide(GuardianInput(
        domain="payment", intent="refund_request", requires_human=True,
        recent_cases=[
            SupportCase(id=1, status="closed", domain="payment", intent="refund_request"),
            SupportCase(id=2, status="open", domain="order", intent="cancel_order"),
        ],
    ))

    assert decision.action is GuardianAction.CREATE_TICKET
    assert decision.ticket_id is None


def test_repeated_unresolved_attempts_force_escalation():
    decision = Guardian.decide(GuardianInput(
        domain="product", intent="technical_support", unresolved_attempts=3,
    ))

    assert decision.action is GuardianAction.ESCALATE


def test_non_escalation_requests_remain_automated():
    decision = Guardian.decide(GuardianInput(domain="product", intent="product_information", domain_can_resolve=True))

    assert decision.action is GuardianAction.RESOLVE
