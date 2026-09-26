# Responsibility Matrix (Revised)

This document explicitly defines architectural responsibilities for Phase F Workflow Orchestration, maintaining the invariant that Boardroom is strictly an offline review metaphor, not a runtime component.

| Responsibility | Current Location | Proposed Location (Phase F) | Rationale |
|----------------|------------------|-----------------------------|-----------|
| **Session Message Concurrency** | None (Rate limit only) | Transport/Session Boundary | A session-level lock/queue must enforce exactly one workflow transition per ordered message. |
| **Transport Layer Management** | WebSocket Handler (`chat_handler.py`) | WebSocket Handler | Keep concerns decoupled. Transport must never own workflow policy. |
| **PII Guardrails / Sanitization** | WebSocket Handler | WebSocket Handler / Middleware | Guardrails run deterministically before semantic routing or logs. |
| **Intent & Semantic Facts** | Legacy `intent_router` + shadow `semantic_router` | `semantic_router` exclusively | The Semantic Router provides facts. The LLM must not select workflow transitions. |
| **Deterministic Workflow Transitions** | LangGraph `route_after_classification` | Supervisor Node | Pure Python, deterministic application of semantic facts against `WorkflowState`. Evaluates CONTINUE, SUSPEND, SWITCH. |
| **Pending Action Confirmation** | WebSocket Handler (`chat_handler.py`) | Supervisor Node | Supervisor manages pending state transitioning; SkillRuntime manages authorization safety locally. |
| **Domain Logic / Policies** | `ProductAgent` / `db_agent` | Specific Domain Agents (`ProductAgent`, `OrderAgent`, `PaymentAgent`) | LLM agents perform typed reasoning constrained to their boundaries. |
| **Ticket Identity & Context** | `TicketLifecycleService` | `TicketLifecycleService` / Domain Agents | `active_ticket_id` is workflow-scoped historical context, not a rigid orchestration lock overriding semantic shifts. Ticket isolation is preserved. |
| **Tool Choice Authorization** | Directly invoked by agents | Skill Runtime / Python Authorization | LLMs cannot directly choose/execute tools without passing through deterministic Python authorization gates. |

## Explicit Invariants Enforced
- Exactly one workflow transition per ordered message.
- LLM cannot directly choose tools without Python authorization.
- LLM cannot directly change workflow status.
- Workflow state is strictly bounded (no unbounded arbitrary dictionaries).
- Customer isolation is preserved.
- Ticket isolation is preserved.
- Domain switches are purely deterministic.
- Supervisor MAY mutate workflow status/metadata, but MUST NOT mutate semantic/domain facts (entities, tickets, etc.).
- WebSocket transport does not own workflow policy.
- Boardroom is never a runtime component.
