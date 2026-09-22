"""
Routing Policy (Phase B)
Provides the deterministic mapping from semantic domain to execution route.
Does NOT execute the routes or contain LLM prompts.
"""

def map_domain_to_route(domain: str) -> str:
    """
    Deterministic mapping of semantic domain to target agent.
    """
    mapping = {
        "general": "GeneralAgent",
        "product": "ProductAgent",
        "order": "OrderAgent",
        "payment": "PaymentAgent",
        "escalation": "EscalationAgent",
        "unknown": "GeneralAgent" # Fallback
    }
    return mapping.get(domain, "GeneralAgent")
