from app.agents.guardian import Guardian, GuardianAction, GuardianInput, SupportCase


def test_matching_active_case_is_updated_before_any_escalation():
    decision = Guardian.decide(GuardianInput(
        domain="product", intent="warranty_claim", safety_or_legal_issue=True,
        recent_cases=[SupportCase(id=786, status="open", domain="product", intent="warranty_claim")],
    ))

    assert decision.action is GuardianAction.UPDATE_EXISTING_TICKET
    assert decision.ticket_id == 786


def test_unrelated_or_closed_cases_never_block_a_new_escalation():
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


def test_human_request_does_not_bypass_an_authorized_resolution():
    decision = Guardian.decide(GuardianInput(
        domain="order", intent="order_tracking", customer_requests_human=True, domain_can_resolve=True,
    ))

    assert decision.action is GuardianAction.CONTINUE


def test_non_escalation_requests_remain_automated():
    decision = Guardian.decide(GuardianInput(domain="product", intent="product_information", domain_can_resolve=True))

    assert decision.action is GuardianAction.RESOLVE
