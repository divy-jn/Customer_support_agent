# ADR 005: Supervisor Workflow Orchestration (Revised)

## Status
Proposed (Phase F Discovery Phase)

## Context
As we transition to a multi-domain architecture (Phase F), the system requires a mechanism to manage deterministic conversations spanning multiple domains.
1. Current routing logic is hardcoded into LangGraph edges (`route_after_classification`).
2. Continuation logic aggressively hijacks workflows based on `active_ticket_id` rather than strict workflow state status.
3. Transport layers (WebSockets) are improperly managing orchestrational approvals for database tools.
4. Concurrency is currently handled via message rate-limiting, but lacks explicit message serialization guarantees for state transitions.

## Decision
We will introduce a **Supervisor Layer** representing deterministic orchestration logic.
The architecture enforces the following invariants and boundaries:

1. **Semantic Router**: Produces analytical semantic facts (Domain + Intent).
2. **Supervisor**: Applies deterministic Python workflow policies to the semantic facts and the `WorkflowState`. LLMs must **not** directly select workflow transitions, tools, or authorizations. The Supervisor's decisions are limited to: CONTINUE, SUSPEND_AND_SWITCH, START_NEW, RESUME, ESCALATE, REQUEST_CLARIFICATION, PROCEED_APPROVAL.
3. **Domain Agents (Order, Payment, Product)**: Self-contained units that consume a `DomainContext` and return a typed `DomainResponse` (containing statuses like `IN_PROGRESS`, `COMPLETED`, `AWAITING_INPUT`).
4. **Ticket Context**: `active_ticket_id` is workflow-scoped historical context used by domain agents. It is no longer an orchestration lock for the Supervisor.
5. **State Machine**: Workflows must abide by rigid transitions: `IDLE` -> `IN_PROGRESS` -> `AWAITING_INPUT` -> `SUSPENDED` -> `RESUMED` -> `COMPLETED` -> `ESCALATED`.
    - **`COMPLETED`**: Terminal success. Does not automatically resume. Next message evaluates semantically against context.
    - **`ESCALATED`**: Terminal for AI. Locks session. Next messages persist to human agents but AI MUST NOT autonomously resume.
6. **Concurrency**: A session-level serialization invariant will be strictly enforced (e.g., via actor model or locks) ensuring exactly one workflow transition occurs per ordered message.
7. **Mutation Authority**: Supervisor MAY mutate workflow status, active domain, suspension metadata. Supervisor MUST NOT mutate semantic/domain facts (entities, ticket data, product facts). Domain agents own domain facts.

## Implementation Sequence
1. F.1 Supervisor decision contract + deterministic transition policy (Over existing WorkflowState. Does not redesign state.)
2. F.2 Bounded multi-domain WorkflowState (Introduces the typed state schemas.)
3. F.3 Supervisor shadow mode (compute decisions without affecting routing)
4. F.4 Compare Supervisor decisions against legacy
5. F.5 Promote Semantic Router + Supervisor
6. F.6 Remove legacy routing responsibilities
7. F.7 Migrate OrderAgent
8. F.8 Move confirmation/pending-action orchestration out of Transport into Supervisor

## Consequences

**Positive:**
- Fully deterministic workflow orchestration guarantees.
- Elimination of leaky state and transport abstractions.
- Zero LLM-hallucination risk for state transitions, since the Supervisor is pure code.

**Negative / Complexity:**
- Managing suspended and concurrent domain workflows increases serialization complexity.
- Requires strict domain boundaries in `WorkflowState`.
