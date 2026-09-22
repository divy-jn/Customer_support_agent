# Routing Policy

## Purpose
The canonical routing policy maps a semantic domain to the target execution route. It isolates the decision of "What is needed" (Semantic Router) from "Who handles it" (Routing Policy).

## Policy Mapping

```python
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
```

## Restrictions
- The routing policy must NEVER be implemented via LLM prompts.
- The Semantic Router must NEVER output `ProductAgent`, `db_agent`, etc. It only outputs `product` or `order`.
- During Phase B Shadow Mode, this policy will be tested but its output will not mutate the actual graph transitions.
