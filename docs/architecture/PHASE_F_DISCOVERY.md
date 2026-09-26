# Phase F Discovery: Orchestration & Supervisor Layer (Revised)

This document outlines the discovery findings for Phase F (Workflow Orchestration), answering the architectural questions required for the final Boardroom acceptance and future implementation.

## 1. Current Responsibilities
- **Semantic Router**: Evaluates natural language into structured `Intent` and `Domain` (currently running in shadow mode).
- **`route_after_classification`**: LangGraph conditional edge. Evaluates router output and `WorkflowState` to determine the next graph node. Contains hardcoded rules for `product_node` continuation.
- **LangGraph Graph (`graph.py`)**: Wires nodes together, manages execution context (`AgentState`), and dictates global control flow.
- **`ProductAgent`**: Executes domain-specific logic for "product" category using the Skill Registry/Runtime. Updates `WorkflowState` and interacts with `TicketLifecycleService`.
- **`WorkflowState`**: Strongly-typed memory object storing extracted entities, tool execution history, and `active_ticket_id`.
- **`TicketLifecycleService`**: Manages the creation, retrieval, and updating of support tickets based on `IssueIdentity` matching.
- **WebSocket/Chat Handler**: Manages connections, input validation (PII guardrails), session history, and explicitly intercepts user approvals (e.g., "Yes") to manually inject state into the `db_plan_node`.

## 2. Leaking and Duplicated Responsibilities
- **Approval Orchestration**: The WebSocket handler manually intercepts approval messages and overrides the graph routing to trigger `db_execute_node`. Orchestration logic is leaking into the transport layer.
- **Continuation Rules**: `route_after_classification` hardcodes semantic domain exceptions, blurring lines between routing and deterministic workflow orchestration.
- **State Pollution**: `AgentState` mixes transport concerns (router failure flags), approval state, and domain context (`workflow_state`).

## 3. Supervisor / Workflow Orchestrator Future Ownership
The Supervisor must own deterministic workflow orchestration. It applies rigid, python-coded transition policies to the semantic facts produced by the Semantic Router.
- **Global Workflow State Lifecycle**: Managing state machine transitions (IDLE, IN_PROGRESS, SUSPENDED, etc.) across all domains.
- **Continuation vs. Interruption**: Evaluating if a message continues an active workflow, switches domains, or resumes a suspended one based on deterministic rules.
- **Approval Orchestration**: Managing `pending_approval` transitions gracefully, decoupling them from WebSocket transport. Policy/SkillRuntime handles actual authorization safety.
- **Cross-Domain Handoffs**: Transitioning control between distinct Domain Agents deterministically.

## 4. What the Supervisor Must Explicitly NOT Own
- **Semantic Interpretation**: Must rely entirely on the Semantic Router.
- **Domain/Business Policy**: Must not contain domain-specific business rules.
- **Tool Execution**: Must not execute external database or API calls directly.
- **Transport / Concurrency**: Must not handle WebSockets.
- **LLM Decisions**: LLM must not directly select workflow transitions, tools, or authorization outcomes. The Supervisor is purely deterministic.

## 5. Architectural Boundaries
- **Semantic Understanding**: Translates text to Intent + Entities (Semantic Router). Purely analytical.
- **Workflow Orchestration**: Maps intents and current state to subsequent states deterministically (Supervisor).
- **Domain-Agent Reasoning**: Executes domain workflows (e.g., Product, Order) using specific skills.
- **Policy Enforcement**: Rules enforced natively within Skills or external Services (e.g., `TicketLifecycleService`).
- **Tool Execution**: Isolated, side-effect producing boundaries.
- **Persistence**: Long-term storage of session history and states.

## 6. Workflow State Machine
The Supervisor enforces a strict state machine on workflows:
- **`IDLE`**: No active workflow. Next state: `IN_PROGRESS` or `ESCALATED`.
- **`IN_PROGRESS`**: Domain agent is actively processing. Next state: `AWAITING_INPUT`, `COMPLETED`, `SUSPENDED`, or `ESCALATED`.
- **`AWAITING_INPUT`**: Domain agent or confirmation flow is waiting for user response. Next state: `IN_PROGRESS`, `SUSPENDED`, or `ESCALATED`.
- **`SUSPENDED`**: Workflow paused due to domain switch. Next state: `RESUMED` or `ESCALATED`.
- **`RESUMED`**: Restored from `SUSPENDED`. Next state: `IN_PROGRESS`.
- **`COMPLETED`**: Terminal success state. Workflow is done. A completed workflow does not automatically resume. The next message must be evaluated against semantic domain/intent and current workflow context. Only an explicit valid continuation/resume condition may reactivate the prior workflow.
- **`ESCALATED`**: Terminal handoff state. Locks session. ESCALATED is terminal for autonomous AI handling of that workflow. New messages may still be persisted and surfaced to the human support flow, but the AI must not silently resume autonomous handling unless an explicit future policy allows it.

*Invalid Transitions*: Cannot transition from `COMPLETED` to `IN_PROGRESS`. Cannot transition from `ESCALATED` to anything else (without human intervention).

## 7. Deterministic Domain-Switch Rules
Switches are evaluated deterministically using Semantic Intent vs Active Domain Workflow:
- **Product → Order**: User asks order question mid-product workflow. Product transitions to `SUSPENDED`. Order transitions `IDLE` → `IN_PROGRESS`.
- **Order → Product**: Opposite of above. Order `SUSPENDED`, Product `IN_PROGRESS`.
- **Related-but-ambiguous**: If intent falls within the umbrella of the active domain, continuation rules apply.
- **Resuming**: If a user switches back to a previously `SUSPENDED` domain, its state transitions `SUSPENDED` → `RESUMED` → `IN_PROGRESS`.
- **Completed Workflow + Related Issue**: A new workflow instance of the same domain starts. Historical ticket/context may be injected, but the workflow is fresh.
- **Completed Workflow + Unrelated Issue**: Starts a new workflow in the required domain.

## 8. Semantic Ambiguity Handling
When the Semantic Router returns uncertain classifications or conflicts with the strict workflow state:
- If `confidence < threshold` and `workflow_status == AWAITING_INPUT`, the Supervisor prioritizes treating the message as a response to the active workflow.
- If completely unclassifiable, the Supervisor requests clarification (routing to a generalized fallback or retaining the current active agent to handle the ambiguity) instead of hallucinating a domain switch.

## 9. Bounded Multi-Domain State
State must be bounded and strongly typed. We replace arbitrary `dict`s with explicit objects:
```python
class WorkflowState(BaseModel):
    session_id: str
    active_domain: str | None
    status: WorkflowStatus
    product_state: ProductState | None
    order_state: OrderState | None
    payment_state: PaymentState | None
    pending_approval_state: ApprovalState | None
```

## 10. Role of active_ticket_id
`active_ticket_id` is **workflow-scoped context**, not a rigid orchestration lock.
- **Workflow Relationship**: A ticket binds to a specific domain workflow instance (e.g., a Product technical issue).
- **Domain-Scoped vs Workflow-Scoped**: If an Order workflow spins up, it does not inherit the Product ticket. It may create an Order ticket or reference it, but ticket mutation remains strictly bound to the active Domain Agent interacting with `TicketLifecycleService`. The Supervisor does not use `active_ticket_id` to override semantic domain switches.

## 11. Concurrency and Session Serialization
Rate limiting is insufficient for concurrency.
- **Serialization Invariant**: Messages within a single `session_id` MUST be processed sequentially. 
- A lock or queue at the transport/session boundary must guarantee that `Message(N+1)` is not routed until the Supervisor has committed the `WorkflowState` output of `Message(N)`. Exactly one workflow transition per ordered message.

## 12. Supervisor Decision Contract
The Supervisor relies on a deterministic policy execution.
- **Input**: `(Semantic_Domain, Semantic_Intent, Current_WorkflowState, Message)`
- **Output**: `SupervisorDecision(Action, TargetDomain, Mutated_WorkflowState)`
- **Allowed Decisions**: CONTINUE, SUSPEND_AND_SWITCH, START_NEW, RESUME, ESCALATE, REQUEST_CLARIFICATION, PROCEED_APPROVAL.
- **Forbidden Decisions**: Directly calling tools, generating conversational responses, mutating Domain context, bypassing authorization policies.
- **Mutation Authority**: Supervisor MAY mutate: workflow status, active domain, suspension/resume metadata, pending approval metadata, and workflow transition metadata. Supervisor MUST NOT mutate semantic/domain facts (product facts, order facts, payment facts, customer facts, extracted entity values, or ticket facts). Domain agents/tools own domain facts.
- **Observability**: Emits a `SupervisorLog` containing the decision logic path, input semantic facts, and state diff.

## 13. Supervisor Shadow-Mode Design
The Supervisor must be implemented iteratively and tested in production without altering behavior.
- **Shadow Implementation**: The Supervisor computes `SupervisorDecision` and logs it asynchronously.
- `route_after_classification` remains authoritative.
- Dashboards/logs compare the Supervisor's output to the legacy routing outcome to build confidence before the switch.

## 14. Explicit Invariants
- Exactly one workflow transition per ordered message.
- LLM cannot directly choose tools without Python authorization.
- LLM cannot directly change workflow status (this is deterministic).
- Workflow state is bounded.
- Customer isolation is preserved.
- Ticket isolation is preserved.
- Domain switches are deterministic.
- WebSocket transport does not own workflow policy.
- Boardroom is never a runtime component.

## 15. Implementation Sequence (Revised)
1. **F.1 Supervisor Decision Contract + Deterministic Workflow Transition Policy**: Define the Pydantic models, State Machine, and pure-Python evaluation logic over the existing WorkflowState structure. Do not duplicate the F.2 state redesign here.
2. **F.2 Bounded Multi-Domain WorkflowState**: Implement the typed multi-domain schema (`ProductState`, `OrderState`, etc.).
3. **F.3 Supervisor Shadow Mode**: Wire the Supervisor into LangGraph alongside `route_after_classification`, logging decisions silently.
4. **F.4 Compare Supervisor Decisions**: Validate shadow mode accuracy against legacy routing.
5. **F.5 Promote Semantic Router + Supervisor**: Flip the flag. Supervisor becomes authoritative.
6. **F.6 Remove Legacy Routing**: Delete `intent_router` and `route_after_classification` exceptions.
7. **F.7 Migrate OrderAgent**: Wrap existing DB planner logic into a formalized Domain Agent.
8. **F.8 Move Confirmation Orchestration**: Move "pending_approval" management from WebSocket to Supervisor + SkillRuntime authorization.

**Recommendation for F.1:** Focus entirely on creating the pure-Python, deterministic Supervisor logic. This allows robust unit-testing of every transition rule before touching the runtime pipeline. Do not hook it into the live server until it is exhaustively validated against the defined deterministic transition matrix and edge cases.
