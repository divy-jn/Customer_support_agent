"""
Routing Policy (Phase B)
Provides the deterministic mapping from semantic domain to execution route.
Does NOT execute the routes or contain LLM prompts.
"""

import re


def map_domain_to_route(domain: str) -> str:
    """
    Deterministic mapping of semantic domain to target agent.
    """
    # #review: In graph.py, the LangGraph nodes are named "db_plan_node", "product_node", "rag_node", etc.
    # The return values here ("GeneralAgent", "OrderAgent", "PaymentAgent") need adapter translation
    # to match the actual LangGraph orchestration node names.
    mapping = {
        "general": "GeneralAgent",
        "product": "ProductAgent",
        "order": "OrderAgent",
        "payment": "PaymentAgent",
        "escalation": "EscalationAgent",
        "unknown": "GeneralAgent" # Fallback for unclassified domains
    }
    return mapping.get(domain, "GeneralAgent")



# Shared vocabulary for classifier prompts and safe telemetry labels.
SEMANTIC_INTENTS = {
    "general": ("faq", "greeting", "small_talk", "clarification", "continuation",
                "affirmation", "negation", "acknowledgment", "conversational",
                "account_name", "ticket_status"),
    "product": ("product_inquiry", "product_information", "product_features", "product_specs",
                "product_details", "product_availability", "product_comparison", "technical_support",
                "product_warranty", "warranty_status", "warranty_claim", "check_inventory"),
    "order": ("track_order", "cancel_order", "view_order_history", "order_status", "order_details",
              "delivery_issue", "delivery_delay", "missing_item", "wrong_item", "damaged_item",
              "follow_up_inquiry", "general_order_inquiry"),
    "payment": ("payment_method", "refund_request", "billing_issue", "payment_failure",
                "transaction_issue", "duplicate_charge"),
    "escalation": ("escalation",),
    "unknown": ("unknown",),
}

ORDER_HISTORY_ALIASES = frozenset({
    "order_history_request", "order_history", "list_orders", "past_orders", "my_orders",
})

_TELEMETRY_INTENTS = frozenset(
    intent for intents in SEMANTIC_INTENTS.values() for intent in intents
) | ORDER_HISTORY_ALIASES | {
    "tracking", "order_tracking", "order_cancellation", "cancellation", "order_issue", "shipping_issue",
    "follow_up", "check_order", "refund", "process_refund", "payment_failed", "charged_twice",
    "product_question", "inventory", "stock_check", "product_stock", "warranty", "warranty_inquiry",
    "inquire_user_name", "inquire_account_name", "ticket_status_request", "active_ticket_status",
    "list_active_tickets", "ticket_inquiry", "active_tickets", "check_ticket_status",
}

_TELEMETRY_ENTITY_KEYS = frozenset({
    "order_id", "ticket_id", "product_id", "product_name", "product_model", "tracking_number",
    "payment_id", "transaction_reference", "customer_id", "email", "name", "serial_number",
})


_TICKET_STATUS_ALIASES = frozenset({
    "ticket_status", "ticket_status_request", "active_ticket_status", "list_active_tickets",
    "ticket_inquiry", "active_tickets", "check_ticket_status",
})


def normalize_intent(domain: str, intent: str) -> str:
    normalized = re.sub(r"[\s-]+", "_", intent.strip().lower())
    if domain == "order" and (
        normalized in ORDER_HISTORY_ALIASES
        or any(keyword in normalized for keyword in ("history", "past_orders", "list_orders"))
    ):
        return "view_order_history"
    if domain == "general" and normalized in _TICKET_STATUS_ALIASES:
        return "ticket_status"
    return normalized


_TICKET_REFERENCE = re.compile(r"\b(?:ticket|case)\s+(?:#\s*)?([0-9]+)\b", re.IGNORECASE)
_HASH_REFERENCE = re.compile(r"(?<!\w)#\s*([0-9]+)\b")
_TICKET_MUTATION = re.compile(
    r"^\s*(?:please\s+)?(?:(?:can|could|would) you\s+)?"
    r"(?:close|reopen|change|create|cancel|resolve|update)\s+(?:(?:my|the|a)\s+)?(?:ticket|case)\b",
    re.IGNORECASE,
)
_TICKET_LIST = re.compile(r"\b(?:tickets|support cases)\b", re.IGNORECASE)


def extract_ticket_id(message: str, entities: dict, *, ticket_context: bool) -> int | None:
    """Resolve numbered ticket queries without interpreting an order number as a ticket."""
    if _TICKET_MUTATION.search(message):
        return None
    if explicit := _TICKET_REFERENCE.search(message):
        candidate = explicit.group(1)
    elif ticket_context and not _TICKET_LIST.search(message):
        bare = _HASH_REFERENCE.search(message)
        candidate = bare.group(1) if bare else entities.get("ticket_id")
    else:
        return None

    if isinstance(candidate, str) and re.fullmatch(r"[0-9]{1,19}", candidate.strip()):
        candidate = int(candidate)
    return candidate if type(candidate) is int and 0 < candidate <= 2**63 - 1 else None


def is_ticket_status_query(message: str) -> bool:
    return bool(
        re.search(r"\b(?:tickets?|support cases?)\b", message, re.IGNORECASE)
        and re.search(r"\b(?:my|for my account|i have)\b", message, re.IGNORECASE)
        and re.search(r"\b(?:status|active|open|pending)\b", message, re.IGNORECASE)
        and not re.search(r"\b(?:close|reopen|update|change|create|cancel|resolve)\b", message, re.IGNORECASE)
    )


_LOOKUP_FOLLOWUP = re.compile(
    r"^(?:and\s+)?(?:(?:why|when|what)\b.*\b(?:it|this|that|they|these|those)\b"
    r"|which (?:one|ones|of (?:these|those|them))\b|(?:is|are|was|were|has|have|did)\s+(?:it|they|this|that)\b"
    r"|(?:any|an) (?:update|news)\b|(?:please )?try again\b)",
    re.IGNORECASE,
)
_LOOKUP_HANDOFF = re.compile(
    r"(?:please\s+)?(?:(?:can|could|would) you\s+)?(?:please\s+)?"
    r"escalate\s+(?:it|this|that)(?:\s+(?:ticket|case|order))?"
    r"(?:\s+to\s+(?:a\s+)?(?:human|agent)(?:\s+agent)?)?"
    r"(?:\s+please)?[.? !]*",
    re.IGNORECASE,
)
_NUMBERED_LOOKUP = re.compile(r"(?<!\w)#\s*[0-9]+|\b(?:ticket|case|order)\s+[0-9]+\b", re.IGNORECASE)


def is_lookup_followup(message: str) -> bool:
    return not _NUMBERED_LOOKUP.search(message) and bool(_LOOKUP_FOLLOWUP.match(message.strip()))


def asks_to_escalate_lookup(message: str) -> bool:
    return bool(_LOOKUP_HANDOFF.fullmatch(message.strip()))


_ORDER_DESCRIPTION = re.compile(
    r"\b(?:latest|most recent)\s+(?:order\b|(?:delivered|shipped)\b)"
    r"|\b(?:this|that)\s+(?:[\w-]+\s+){0,4}order\b",
    re.IGNORECASE,
)
_DELIVERY_DATE_QUESTION = re.compile(
    r"\bwhen\b.*\bdeliver(?:ed|y)?\b|\bdeliver(?:ed|y)?\b.*\b(?:date|when)\b"
    r"|\b(?:what|which)\s+date\b.*\bdeliver(?:ed|y)?\b",
    re.IGNORECASE,
)
_ORDER_ACTION_REQUEST = re.compile(
    r"\b(?:cancel|refund|return|replace|reschedule|change|escalate|damaged|missing|wrong item)\b", re.IGNORECASE,
)


def is_descriptive_order_lookup(message: str) -> bool:
    return bool(_ORDER_DESCRIPTION.search(message)) and not _ORDER_ACTION_REQUEST.search(message)


def is_order_delivery_date_question(message: str) -> bool:
    return bool(_DELIVERY_DATE_QUESTION.search(message)) and not _ORDER_ACTION_REQUEST.search(message)


def telemetry_intent(value: str) -> str:
    # A model can put customer data in an unconstrained intent field.
    if len(value) <= 64 and value.strip().lower() in _TELEMETRY_INTENTS:
        return value
    return "[redacted]"


def telemetry_entity_keys(entities: dict) -> list[str]:
    return sorted(_TELEMETRY_ENTITY_KEYS.intersection(entities))
