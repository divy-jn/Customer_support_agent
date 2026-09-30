# PHASE F.2 DISCOVERY: Multi-Domain Workflow State

## 1. Global vs. Domain Workflow State
The architecture cleanly separates Global Orchestration from Domain Fact tracking:
- **Global Workflow State**: Owned by the system/Supervisor. Tracks session identity, global orchestration (active_domain, suspended_domains), versioning (schema_version, state_revision), and concurrency control.
- **Domain Workflow State**: Owned by specific domain agents (ProductAgent, OrderAgent, etc.). Tracks domain-specific facts, local execution status, and active tickets.

## 2. Workflow Instance Model
**Invariant**: Each session may have at most **one live workflow instance for each supported domain**. 
There is no `workflow_instance_id` array; a domain state is either active, suspended, or cleanly replaced by a `START_NEW` orchestration.

## 3. Bounded Typed Models & Boundedness
- **ProductState, OrderState, PaymentState**: Strictly typed Pydantic models.
- **PendingAction/Approval**: Deferred to Phase F.8. No arbitrary `dict` payloads are allowed.
- **Limits**:
  - `suspended_domains`: Maximum 3 domains.
  - String fields (`product_name`, `manufacturer`, `tracking_number`): Maximum 255 characters.
  - Tool context (`last_tool_result`): Bounded or excluded in favor of session message history.

## 4. Active & Suspended Workflows
- `active_domain` strictly points to the currently executing context.
- **Suspension**: A strictly bounded LIFO stack (`suspended_domains`). 
- When suspended, the respective `DomainWorkflowStatus` transitions to `SUSPENDED`.
- **Resumption**: `SUSPENDED` -> `RESUME` action -> `IN_PROGRESS` (with `active_domain` restored). 
- Isolation: Each suspended workflow retains its isolated facts and ticket context.

## 5. Escalation Scope
- **Domain Scope**: Escalation applies to the active **Domain Workflow** (`DomainWorkflowStatus.ESCALATED`).
- **Session Scope separation**: The overall session `mode` (human vs AI) remains a transport/session-level concern. The Supervisor evaluates whether a workflow is escalated but does not directly own WebSocket/session mode handoff.

## 6. Ticket Relationship
- **One active ticket per domain**: A domain workflow may have at most one active ticket at a time (unless explicit multi-ticket policy is added later).
- **Ownership**: Ticket identity is owned by `TicketLifecycleService`.
- **Isolation**: Supervisor does not mutate ticket IDs. Switching domains never transfers a ticket to another domain. Resuming a suspended domain restores its specific ticket context.
- Adheres strictly to Phase E.2.3 semantics (distinct issue -> new ticket, same issue -> update).

## 7. Completed Workflows
- `COMPLETED` domain state is purely historical.
- A new issue in the same domain creates a **fresh** DomainState instance via `START_NEW`.
- We do not silently reuse completed domain facts. Historical ticket IDs remain accessible via external ticket persistence/audit trails, not by resurrecting the closed workflow.

## 8. Cross-Domain Data
- **Isolation**: Domain states are strictly isolated (`ProductState` cannot directly read `OrderState` properties).
- If cross-domain context is required, it must be provided through an explicitly authorized shared read model or capability, never via direct mutation or structural coupling.

## 9. Persistence & Concurrency
- `WorkflowState` must be deterministically serializable, versionable, and support future optimistic concurrency.
- **Fields**: `schema_version: int` and `state_revision: int` (for atomic compare-and-swap conflict detection).
- Concurrency locking is not implemented in F.2, but the schema provides the foundation.

## 10. Migration Strategy
We use explicit boundary adapters, explicitly avoiding "magic" `@property` getters:
- `WorkflowState.from_legacy(...)`: Inflates flat legacy state into typed DomainStates.

## 11. Implementation Slices
- **F.2.1**: Schema models only (Pydantic definitions, enums, limits).
- **F.2.2**: Validation + versioning (`schema_version`, `state_revision`, invalid state matrix).
- **F.2.3**: Explicit legacy adapter (`from_legacy`).
- **F.2.4**: Product state migration (migrate existing Product workflow to the adapter).
- **F.2.5**: Ticket context migration (align `TicketLifecycleService`).
- **F.2.6**: Persistence compatibility verification (JSONB serialization, optimistic locking prep).
- **F.2.7**: Remove legacy fields (hard boundary).

## 12. Global vs Domain State Field Ownership

| Field | Type | Scope | Owner | Source of Truth | Mutation Auth | Persistence | Lifecycle | Boundedness |
|-------|------|-------|-------|-----------------|---------------|-------------|-----------|-------------|
| `session_id` | `str` | Global | Transport | Transport | Immutable | Required | Session | Fixed string |
| `customer_id` | `int` | Global | Auth | Auth | Immutable | Required | Session | Integer |
| `schema_version` | `int` | Global | System | Schema Definition | System/Migrations | Required | Versioned | Strict Int |
| `state_revision` | `int` | Global | Persistence | DB/Store | Optimistic Lock | Required | Monotonic | Monotonic Int|
| `active_domain` | `OrchestrationDomain` | Global | Supervisor | Supervisor | Supervisor | Required | Workflow | Enum |
| `suspended_domains`| `list[OrchestrationDomain]`| Global| Supervisor| Supervisor| Supervisor| Required| Workflow| Max 3 |
| `product_state` | `ProductState` | Domain | ProductAgent | Domain Agent | ProductAgent/TicketSvc| Required| Domain | 1 per domain |
| `order_state` | `OrderState` | Domain | OrderAgent | Domain Agent | OrderAgent/TicketSvc | Required| Domain | 1 per domain |
| `payment_state` | `PaymentState` | Domain | PaymentAgent | Domain Agent | PaymentAgent/TicketSvc | Required| Domain | 1 per domain |
