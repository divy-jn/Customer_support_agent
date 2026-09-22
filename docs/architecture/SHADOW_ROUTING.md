# Shadow Routing Integration

## Concept
The Semantic Router runs alongside the legacy `intent_router` but operates in **Shadow Mode**. This means its execution is observable but completely non-authoritative.

## Safe Execution Requirements
- **No side effects**: The shadow router must not execute any actions (DB, API, email, web search).
- **Graceful Failure**: If the semantic router encounters a timeout, 401, parsing error, or internal crash, the exception is caught, logged, and ignored. The legacy path continues.
- **Task Lifecycle**: To prevent delaying the customer response, the shadow router is launched as a fire-and-forget background task using `asyncio.create_task()`. To prevent the task from becoming orphaned or garbage collected prematurely, a strong reference is kept in a global `_shadow_tasks` set, and a callback (`task.add_done_callback`) removes it upon completion.
- **Sanitized Logging**: All metrics and comparisons are logged to the existing telemetry framework under the event name `semantic_router.shadow_comparison`.

## Telemetry Payload (Example)
```json
{
  "event": "semantic_router.shadow_comparison",
  "session_id": "sess_123",
  "legacy_intent": "refund",
  "legacy_route": "db_agent",
  "semantic_domain": "payment",
  "semantic_intent": "refund_request",
  "confidence": 0.85,
  "status": "success",
  "agreement": true,
  "latency_ms": 450
}
```
**Never** log raw customer PII or LLM provider API keys in this payload.
