"""Small deterministic boundary for turning workflow outcomes into customer text."""

from app.agents.guardian import GuardianAction, GuardianDecision


def compose_response(agent_response: str, decision: GuardianDecision) -> str:
    """Attach only policy-mandated status; never replace a domain-agent answer."""
    suffixes = {
        GuardianAction.UPDATE_EXISTING_TICKET: (
            f" There is already an open support request for this issue (Ticket #{decision.ticket_id}), "
            "so I added your latest details there and won’t create a duplicate support request."
        ),
        GuardianAction.CREATE_TICKET: (
            " I’m passing this to a support specialist so they can continue helping you."
        ),
        GuardianAction.ESCALATE: (
            " I’m escalating this to a human support specialist now."
        ),
        GuardianAction.CONTINUE: (
            " I can complete this request here, and will keep you updated."
        ),
        GuardianAction.RESOLVE: "",
    }
    return f"{agent_response.rstrip()}{suffixes[decision.action]}"
