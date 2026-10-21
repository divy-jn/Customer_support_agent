from app.agents.guardian import GuardianAction, GuardianDecision
from app.agents.response_composer import compose_response


def test_existing_ticket_message_keeps_domain_answer_and_ticket_reference():
    response = compose_response(
        "Your warranty is active.",
        GuardianDecision(action=GuardianAction.UPDATE_EXISTING_TICKET, ticket_id=786, reason="duplicate"),
    )
    assert response.startswith("Your warranty is active.")
    assert "Ticket #786" in response
    assert "won’t create a duplicate" in response


def test_resolved_response_is_not_padded_with_policy_copy():
    assert compose_response(
        "Your warranty is active.", GuardianDecision(action=GuardianAction.RESOLVE, reason="resolved")
    ) == "Your warranty is active."
