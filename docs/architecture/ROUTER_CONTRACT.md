# Semantic Router Contract

## Philosophy
The semantic router answers the question: "What does the customer need semantically?" 
It does NOT answer: "Which agent or tool should handle this?"

## Contract: `SemanticRouteResult`
A strongly typed, deterministic schema.

```python
class RouterFailureType(str, Enum):
    SUCCESS = "success"
    TRANSPORT_ERROR = "transport_error"
    AUTHENTICATION_ERROR = "authentication_error"
    RATE_LIMIT_ERROR = "rate_limit_error"
    TIMEOUT_ERROR = "timeout_error"
    PARSER_ERROR = "parser_error"
    SCHEMA_VALIDATION_ERROR = "schema_validation_error"
    INTERNAL_ERROR = "internal_error"

class RouterDiagnostics(BaseModel):
    failure_type: RouterFailureType
    error_message: str | None = None
    latency_ms: int = 0
    raw_response: str | None = None # Only populated on parse failure, sanitized

class SemanticRouteResult(BaseModel):
    # Semantic Intent
    domain: str        # e.g., "order", "product", "payment", "general", "escalation", "unknown"
    intent: str        # e.g., "track_order", "warranty_claim", "billing_question"
    sentiment: str     # "positive", "neutral", "negative"
    urgency: str       # "low", "medium", "high", "critical"
    
    # Observability & Metadata
    confidence: float  # 0.0 to 1.0, purely observational for Phase B
    is_continuation: bool = False
    entities: dict = {}
    
    # System Status
    diagnostics: RouterDiagnostics
```

## Failure Classification
- A `RouterFailureType` other than `SUCCESS` means the system failed to semantically route the user.
- These failures are explicitly caught and categorized.
- Semantic failures (e.g. LLM hallucinates an unknown domain) will result in a `SCHEMA_VALIDATION_ERROR` or `PARSER_ERROR`.
