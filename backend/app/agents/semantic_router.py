"""
Semantic Router Agent (Phase B)
Classifies the semantic domain, intent, sentiment, and urgency of a customer message.
Outputs a strictly typed SemanticRouteResult.
"""

import json
import time
from app.agents.routing_policy import SEMANTIC_INTENTS
from app.llm_factory import get_llm, model_for_telemetry
from app.middleware.tracking import track_llm_call
from app.models import SemanticRouteResult, RouterDiagnostics, RouterFailureType


SEMANTIC_ROUTER_SYSTEM_PROMPT = """You are the Semantic Router for a Customer Support System.
Your job is to understand WHAT the customer needs semantically.

DO NOT answer the user's question. DO NOT try to solve their issue.
DO NOT decide which internal agent, database, or tool should handle the request.

Extract and output a JSON object exactly matching this schema:
{
  "domain": "general" | "product" | "order" | "payment" | "escalation" | "unknown",
  "intent": "<one allowed value for the chosen domain>",
  "sentiment": "positive" | "neutral" | "negative",
  "urgency": "low" | "medium" | "high" | "critical",
  "confidence": <float_between_0_and_1>,
  "is_continuation": <boolean>,
  "entities": {
     // Extracted entities like order_id, product_name, etc. if explicitly mentioned
  }
}

Definitions:
- "general": Policies, FAQs, and account queries including ticket status and active tickets.
  Use "ticket_status" for the customer's own support cases, even if no ticket number is given.
- "product": Product features, tech support, specifications, warranty queries.
- "order": Order tracking, cancellations, delivery issues, and the customer's order history.
  Use "view_order_history" to list past orders, even if no order number is given.
- "payment": Billing, deductions, refunds, transaction failures, and accepted payment methods.
  Use "payment_method" for questions about which payment methods are accepted.
- "escalation": Severe complaints, explicit human handoff requests.

Choose exactly one intent from the list for the selected domain. If uncertain
about the specific intent, use the closest listed value for that domain.
If uncertain about the domain, use "unknown" for both domain and intent.

Allowed intents by domain:
""" + "\n".join(f"- {domain}: {', '.join(intents)}" for domain, intents in SEMANTIC_INTENTS.items())

def get_semantic_router_llm():
    return get_llm(temperature=0.1)

def parse_and_validate_semantic_output(raw_text: str, latency_ms: int) -> SemanticRouteResult:
    """Parses LLM output, enforcing the SemanticRouteResult schema and capturing failures."""
    text = raw_text.strip()
    # Strip markdown fences if present at boundary
    # #review: If LLM produces conversational pre-text before ```json, startswith check will fail.
    # A regex search for the JSON block is more resilient against conversational LLM output.
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return SemanticRouteResult(
            domain="unknown",
            intent="unknown",
            sentiment="neutral",
            urgency="medium",
            confidence=0.0,
            diagnostics=RouterDiagnostics(
                failure_type=RouterFailureType.PARSER_ERROR,
                error_message=f"JSONDecodeError: {str(e)}",
                latency_ms=latency_ms,
                raw_response=text
            )
        )
        
    valid_domains = {"general", "product", "order", "payment", "escalation", "unknown"}
    valid_sentiments = {"positive", "neutral", "negative"}
    valid_urgencies = {"low", "medium", "high", "critical"}
    
    missing = [f for f in ["domain", "intent", "sentiment", "urgency", "confidence", "is_continuation"] if f not in data]
    if missing:
        return SemanticRouteResult(
            domain="unknown",
            intent="unknown",
            sentiment="neutral",
            urgency="medium",
            confidence=0.0,
            diagnostics=RouterDiagnostics(
                failure_type=RouterFailureType.SCHEMA_VALIDATION_ERROR,
                error_message=f"Missing required fields: {missing}",
                latency_ms=latency_ms,
                raw_response=text
            )
        )
        
    invalid_fields = []
    if data["domain"] not in valid_domains:
        invalid_fields.append(f"domain={data['domain']}")
    if data["sentiment"] not in valid_sentiments:
        invalid_fields.append(f"sentiment={data['sentiment']}")
    if data["urgency"] not in valid_urgencies:
        invalid_fields.append(f"urgency={data['urgency']}")
        
    if invalid_fields:
        return SemanticRouteResult(
            domain="unknown",
            intent="unknown",
            sentiment="neutral",
            urgency="medium",
            confidence=0.0,
            diagnostics=RouterDiagnostics(
                failure_type=RouterFailureType.SCHEMA_VALIDATION_ERROR,
                error_message=f"Invalid enum values: {invalid_fields}",
                latency_ms=latency_ms,
                raw_response=text
            )
        )
        
    return SemanticRouteResult(
        domain=data["domain"],
        intent=data["intent"],
        sentiment=data["sentiment"],
        urgency=data["urgency"],
        confidence=float(data["confidence"]),
        is_continuation=bool(data["is_continuation"]),
        entities=data.get("entities", {}),
        diagnostics=RouterDiagnostics(
            failure_type=RouterFailureType.SUCCESS,
            latency_ms=latency_ms
        )
    )


async def classify_semantic_intent(message: str, conversation_history: list[dict] = None) -> SemanticRouteResult:
    """
    Classify the message and output a strongly typed SemanticRouteResult.
    Catches and safely categorizes system exceptions (transport, auth, rate limit, etc.).
    """
    # Pass recent 6 turns for conversational disambiguation
    context = ""
    if conversation_history:
        recent = conversation_history[-6:]
        context = "Recent conversation context:\n"
        for msg in recent:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            context += f"  [{role}]: {content}\n"
        context += "\n"

    prompt = f"""{context}Customer's latest message: "{message}"

Classify this message and respond with a JSON object."""

    start_time = time.time()
    try:
        llm = get_semantic_router_llm()
        with track_llm_call(model_for_telemetry(llm), "semantic_router", prompt) as tracker:
            response = await llm.ainvoke([
                {"role": "system", "content": SEMANTIC_ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ])
            tracker["output_text"] = response.content
            
        latency_ms = int((time.time() - start_time) * 1000)
        return parse_and_validate_semantic_output(response.content, latency_ms)

    except Exception as e:
        latency_ms = int((time.time() - start_time) * 1000)
        error_type = type(e).__name__
        
        # Categorize the exception based on common error names
        # #review: Vertex AI / Google GenAI exceptions (e.g. ResourceExhausted, GoogleAPICallError)
        # default to INTERNAL_ERROR instead of RATE_LIMIT_ERROR / TRANSPORT_ERROR.
        failure_type = RouterFailureType.INTERNAL_ERROR
        if error_type in ("OpenAIAuthenticationError", "AuthenticationError", "PermissionDeniedError", "PermissionDenied"):
            failure_type = RouterFailureType.AUTHENTICATION_ERROR
        elif error_type in ("RateLimitError", "ResourceExhausted"):
            failure_type = RouterFailureType.RATE_LIMIT_ERROR
        elif error_type in ("APITimeoutError", "ReadTimeout", "Timeout", "TimeoutError"):
            failure_type = RouterFailureType.TIMEOUT_ERROR
        elif error_type in ("APIConnectionError", "ConnectError", "ConnectionError"):
            failure_type = RouterFailureType.TRANSPORT_ERROR
            
        return SemanticRouteResult(
            domain="unknown",
            intent="unknown",
            sentiment="neutral",
            urgency="medium",
            confidence=0.0,
            diagnostics=RouterDiagnostics(
                failure_type=failure_type,
                error_message=f"{error_type}: {str(e)[:200]}", # sanitize by truncating and dropping stacktrace
                latency_ms=latency_ms
            )
        )
