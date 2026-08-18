"""
Escalation Agent — handles angry customers or explicit human handoff requests.
Prepares the session for live agent takeover.
"""

from app.llm_factory import get_llm
from app.config import settings
from app.middleware.tracking import track_llm_call

ESCALATION_PROMPT = """You are a highly empathetic customer support AI.

The customer is frustrated, angry, or has explicitly asked to speak to a human.
Your job is to:
1. De-escalate the situation by acknowledging their frustration empathetically.
2. Assure them that a human agent is being notified immediately.
3. Keep the message relatively short. Do not try to solve their problem yourself.

Draft a polite and empathetic response."""

def get_escalation_llm():
    """Get the small LLM for fast escalation processing."""
    return get_llm(model=settings.llm_small_model, temperature=0.1)

async def generate_escalation_response(message: str) -> dict:
    """
    Generate an empathetic handover message.
    """
    llm = get_escalation_llm()
    
    user_prompt = f"Customer's message: \"{message}\""

    try:
        response = await llm.ainvoke([
            {"role": "system", "content": ESCALATION_PROMPT},
            {"role": "user", "content": user_prompt},
        ])
        return {
            "response": response.content,
            "escalated": True
        }
    except Exception as e:
        return {
            "response": "I understand you need more help. I am transferring you to a human agent right now. Please hold on.",
            "escalated": True,
            "error": str(e)
        }
