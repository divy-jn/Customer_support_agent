"""Small deterministic boundary for turning workflow outcomes into customer text."""

import re

from app.agents.guardian import GuardianAction, GuardianDecision


_HANDOFF_CLAIM = re.compile(
    r"\b(?:(?:let me|i(?:['’](?:m|ll|ve)\b|\s+(?:am|will|have)\b))\s+(?:now\s+)?"
    r"(?:connect(?:ing|ed)?|transfer(?:ring|red)?|escalat(?:e|ing|ed)|"
    r"hand(?:ing|ed)?\s+off|pass(?:ing|ed)?)\s+(?:you|this|your)\b"
    r"|(?:a\s+)?(?:human\s+)?(?:agent|representative|specialist)\s+"
    r"(?:has been notified|is being notified|will be with you)\b)[^.!?\n]*[.!?]?",
    re.IGNORECASE,
)


def remove_unconfirmed_handoff_claims(response: str) -> str:
    """Keep ordinary answers and suppress transfer claims before durable confirmation."""
    return _HANDOFF_CLAIM.sub("You can ask for a human agent if you need more help.", response)


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
