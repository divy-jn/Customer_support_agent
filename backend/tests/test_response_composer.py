from app.agents.guardian import GuardianAction, GuardianDecision
from app.agents.response_composer import compose_response


def test_existing_ticket_message_keeps_domain_answer_and_ticket_reference():
    response = compose_response(
        "Your warranty is active.",
        GuardianDecision(action=GuardianAction.UPDATE_EXISTING_TICKET, ticket_id=786, reason="duplicate"),
    )
    assert response.startswith("Your warranty is active.")
    assert "Ticket #786" in response
    assert "instead of creating a duplicate" in response


def test_resolved_response_is_not_padded_with_policy_copy():
    assert compose_response(
        "Your warranty is active.", GuardianDecision(action=GuardianAction.RESOLVE, reason="resolved")
    ) == "Your warranty is active."


def test_create_ticket_message_keeps_domain_answer_and_handoff_text():
    response = compose_response(
        "I will help you register this warranty issue.",
        GuardianDecision(action=GuardianAction.CREATE_TICKET, reason="needs human"),
    )
    assert response.startswith("I will help you register this warranty issue.")
    assert "passing this to a support specialist" in response


def test_escalate_message_keeps_domain_answer_and_escalation_text():
    response = compose_response(
        "I understand this is critical.",
        GuardianDecision(action=GuardianAction.ESCALATE, reason="safety concern"),
    )
    assert response.startswith("I understand this is critical.")
    assert "escalating this to a human support specialist" in response


def test_continue_message_keeps_domain_answer_and_continuation_text():
    response = compose_response(
        "Your package is scheduled for delivery tomorrow.",
        GuardianDecision(action=GuardianAction.CONTINUE, reason="authorized to proceed"),
    )
    assert response.startswith("Your package is scheduled for delivery tomorrow.")
    assert "complete this request here" in response
