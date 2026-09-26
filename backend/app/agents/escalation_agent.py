"""
Escalation Agent — handles angry customers or explicit human handoff requests.
Prepares the session for live agent takeover.
"""

from app.llm_factory import get_llm
from app.config import settings
from app.middleware.tracking import track_llm_call

from app.skills.base import render_skill_prompt
from app.skills.policy import GLOBAL_SYSTEM_POLICY
from app.skills.registry import EscalationSkill

ESCALATION_PROMPT = GLOBAL_SYSTEM_POLICY + "\n\n" + render_skill_prompt(EscalationSkill)

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
