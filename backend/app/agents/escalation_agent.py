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

# Low temperature (0.1) ensures empathetic, predictable handover messaging
def get_escalation_llm():
    """Get the LLM for fast escalation processing."""
    return get_llm(model=settings.llm_model, temperature=0.1)

async def generate_escalation_response(message: str) -> dict:
    """
    Generate an empathetic handover message.
    """
    llm = get_escalation_llm()
    
    # #review: conversation_history and customer identity are not passed here;
    # prompt only sees the single triggering message.
    user_prompt = f"Customer's message: \"{message}\""

    try:
        # #review: track_llm_call is imported above but not used here for telemetry
        with track_llm_call(settings.llm_model, "escalation_node", user_prompt) as tracker:
            response = await llm.ainvoke([
                {"role": "system", "content": ESCALATION_PROMPT},
                {"role": "user", "content": user_prompt},
            ])
            tracker["output_text"] = response.content
        return {
            "response": response.content,
            "escalated": True
        }
    except Exception as e:
        # Safe fallback handoff message in case LLM is unreachable
        return {
            "response": "I understand you need more help. I am transferring you to a human agent right now. Please hold on.",
            "escalated": True,
            "error": str(e)
        }
