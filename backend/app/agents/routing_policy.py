"""
Routing Policy (Phase B)
Provides the deterministic mapping from semantic domain to execution route.
Does NOT execute the routes or contain LLM prompts.
"""

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


def telemetry_intent(value: str) -> str:
    # A model can put customer data in an unconstrained intent field.
    if len(value) <= 64 and value.strip().lower() in _TELEMETRY_INTENTS:
        return value
    return "[redacted]"


def telemetry_entity_keys(entities: dict) -> list[str]:
    return sorted(_TELEMETRY_ENTITY_KEYS.intersection(entities))
