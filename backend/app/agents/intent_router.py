"""
Intent Router Agent — classifies customer intent and sentiment,
then routes to the appropriate specialist agent.

Uses the small/fast LLM model for quick classification.
"""

import json
from app.config import settings
from app.llm_factory import get_llm
from app.middleware.tracking import track_llm_call


from app.skills.base import render_skill_prompt
from app.skills.policy import GLOBAL_SYSTEM_POLICY
from app.skills.registry import IntentRoutingSkill

ROUTER_SYSTEM_PROMPT = GLOBAL_SYSTEM_POLICY + "\n\n" + render_skill_prompt(IntentRoutingSkill)


def get_router_llm():
    """Get the small LLM for fast routing decisions."""
    return get_llm(model=settings.llm_small_model, temperature=0.1)


def parse_and_validate_router_output(raw_text: str) -> dict:
    """
    Deterministic parsing boundary to handle markdown-wrapped JSON from the provider.
    Limitation: The current LLM provider wraps structured JSON in markdown fences, breaking 
    LangChain's native with_structured_output. This function strips the fences before parsing.
    """
    text = raw_text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    
    result = json.loads(text)
    
    valid_intents = ["faq", "technical_support", "billing", "refund", "order_tracking",
                     "order_cancellation", "account_management", "product_inquiry", "complaint", "general"]
    valid_sentiments = ["positive", "neutral", "negative"]
    valid_urgencies = ["low", "medium", "high", "critical"]
    valid_routes = ["rag_agent", "db_agent", "web_agent", "escalation"]

    # Check missing fields
    missing = [f for f in ["intent", "sentiment", "urgency", "route_to"] if f not in result]
    if missing:
        raise ValueError(f"Missing required fields: {missing}")

    invalid_fields = []
    if result["intent"] not in valid_intents:
        invalid_fields.append(f"intent={result['intent']}")
    if result["sentiment"] not in valid_sentiments:
        invalid_fields.append(f"sentiment={result['sentiment']}")
    if result["urgency"] not in valid_urgencies:
        invalid_fields.append(f"urgency={result['urgency']}")
    if result["route_to"] not in valid_routes:
        invalid_fields.append(f"route_to={result['route_to']}")
        
    if invalid_fields:
        raise ValueError(f"Invalid enum values: {invalid_fields}")
        
    CANONICAL_INTENT_ROUTE = {
        "faq": "rag_agent",
        "technical_support": "rag_agent",
        "product_inquiry": "rag_agent",
        "general": "rag_agent",
        "billing": "db_agent",
        "refund": "db_agent",
        "order_tracking": "db_agent",
        "order_cancellation": "db_agent",
        "account_management": "db_agent",
        "complaint": "escalation",
    }
        
    return {
        "intent": result["intent"],
        "sentiment": result["sentiment"],
        "urgency": result["urgency"],
        "route_to": CANONICAL_INTENT_ROUTE[result["intent"]],
        "llm_proposed_route": result["route_to"],
        "reasoning": result.get("reasoning", "")
    }


async def classify_intent(message: str, conversation_history: list[dict] = None) -> dict:
    """
    Classify the intent, sentiment, and urgency of a customer message.

    Args:
        message: The customer's latest message.
        conversation_history: Optional list of previous messages for context.

    Returns:
        Dict with intent, sentiment, urgency, route_to, and reasoning.
    """
    llm = get_router_llm()

    # Build context from conversation history
    context = ""
    if conversation_history:
        recent = conversation_history[-6:]  # Last 3 exchanges
        context = "Recent conversation context:\n"
        for msg in recent:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            context += f"  [{role}]: {content}\n"
        context += "\n"

    prompt = f"""{context}Customer's latest message: "{message}"

Classify this message and respond with a JSON object."""

    try:
        with track_llm_call(settings.llm_small_model, "intent_router", prompt) as tracker:
            response = await llm.ainvoke([
                {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ])
            tracker["output_text"] = response.content

        result = parse_and_validate_router_output(response.content)

        # Force escalation for angry/critical customers
        if result["sentiment"] == "negative" and result["urgency"] in ("high", "critical"):
            result["route_to"] = "escalation"

        return result

    except Exception as e:
        error_type = type(e).__name__
        router_error_message = str(e)
        
        # Categorize the exception
        router_transport_failure = False
        router_parse_failure = False
        router_internal_failure = False
        
        # Checking against OpenAI exceptions or common transport/connection error types
        if error_type in (
            "OpenAIAuthenticationError", 
            "AuthenticationError",
            "RateLimitError", 
            "APITimeoutError", 
            "APIConnectionError", 
            "ConnectError",
            "ReadTimeout",
            "Timeout",
            "ConnectionError"
        ):
            router_transport_failure = True
        elif error_type in ("JSONDecodeError", "ValidationError", "ValueError"):
            router_parse_failure = True
        else:
            router_internal_failure = True

        print(f"ROUTER EXCEPTION [{error_type}]: {router_error_message[:200]}")
        
        # Fallback classification
        return {
            "intent": "general",
            "sentiment": "neutral",
            "urgency": "medium",
            "route_to": "rag_agent",
            "reasoning": "Fallback classification due to error.",
            "router_error_type": error_type,
            "router_transport_failure": router_transport_failure,
            "router_parse_failure": router_parse_failure,
            "router_internal_failure": router_internal_failure
        }
