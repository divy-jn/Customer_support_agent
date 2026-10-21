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
