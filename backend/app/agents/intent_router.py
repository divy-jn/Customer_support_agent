"""
Intent Router Agent — classifies customer intent and sentiment,
then routes to the appropriate specialist agent.

Uses the small/fast LLM model for quick classification.
"""

import json
from app.config import settings
from app.llm_factory import get_llm
from app.middleware.tracking import track_llm_call


ROUTER_SYSTEM_PROMPT = """You are an intent classification and sentiment analysis system for a customer support platform.

Given a customer message, you MUST respond with a valid JSON object containing exactly these fields:
- "intent": one of [faq, technical_support, billing, refund, order_tracking, order_cancellation, account_management, product_inquiry, complaint, general]
- "sentiment": one of [positive, neutral, negative]
- "urgency": one of [low, medium, high, critical]
- "route_to": one of [rag_agent, db_agent, web_agent, escalation]
- "reasoning": a brief explanation of your classification

Routing rules:
- Route to "rag_agent" for: faq, technical_support, product_inquiry, general questions about policies/products
- Route to "db_agent" for: billing, refund, order_tracking, order_cancellation, account_management (anything needing database lookup)
- Route to "web_agent" for: questions about things outside our knowledge base (competitor products, general tech questions)
- Route to "escalation" for: when sentiment is "negative" AND urgency is "high" or "critical", OR when the customer explicitly asks for a human agent

IMPORTANT: Respond ONLY with the JSON object, no other text."""


def get_router_llm():
    """Get the small LLM for fast routing decisions."""
    return get_llm(model=settings.llm_small_model, temperature=0.1)


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

        result = json.loads(response.content)

        # Validate required fields
        valid_intents = ["faq", "technical_support", "billing", "refund", "order_tracking",
                         "order_cancellation", "account_management", "product_inquiry", "complaint", "general"]
        valid_sentiments = ["positive", "neutral", "negative"]
        valid_urgencies = ["low", "medium", "high", "critical"]
        valid_routes = ["rag_agent", "db_agent", "web_agent", "escalation"]

        result["intent"] = result.get("intent", "general") if result.get("intent") in valid_intents else "general"
        result["sentiment"] = result.get("sentiment", "neutral") if result.get("sentiment") in valid_sentiments else "neutral"
        result["urgency"] = result.get("urgency", "medium") if result.get("urgency") in valid_urgencies else "medium"
        result["route_to"] = result.get("route_to", "rag_agent") if result.get("route_to") in valid_routes else "rag_agent"

        # Force escalation for angry/critical customers
        if result["sentiment"] == "negative" and result["urgency"] in ("high", "critical"):
            result["route_to"] = "escalation"

        return result

    except (json.JSONDecodeError, Exception) as e:
        # Fallback classification
        return {
            "intent": "general",
            "sentiment": "neutral",
            "urgency": "medium",
            "route_to": "rag_agent",
            "reasoning": f"Fallback classification due to error: {str(e)}",
        }
