# PHASE F.2 DISCOVERY: Multi-Domain Workflow State

## 1. Global vs. Domain Workflow State
The architecture cleanly separates Global Orchestration from Domain Fact tracking:
- **Global Workflow State**: Owned by the system/Supervisor. Tracks session identity (customer_id, session_id), global workflow status (IDLE, IN_PROGRESS, ESCALATED), the current `active_domain`, and the bounded stack of `suspended_domains`. It also holds the global cross-cutting `pending_approval` state.
- **Domain Workflow State**: Owned by specific domain agents (ProductAgent, OrderAgent, etc.). Tracks domain-specific facts (e.g., `product_id`, `tracking_number`), domain-level execution status (IN_PROGRESS, AWAITING_INPUT, COMPLETED), and the specific `active_ticket_id` associated with that domain's isolated task.

## 2. Bounded Typed Models
Instead of a generic `domain_states: dict`, we explicitly type every domain:
- **ProductState**: `product_id`, `product_name`, `manufacturer`, `active_ticket_id`, `status`
- **OrderState**: `order_id`, `tracking_number`, `active_ticket_id`, `status`
- **PaymentState**: `transaction_id`, `payment_method`, `active_ticket_id`, `status`
- *GeneralState/EscalationState*: Intentionally omitted. "General" routing does not require business fact persistence; "Escalation" is a global terminal status.

## 3. Active Domain Representation
- An explicit `active_domain: OrchestrationDomain | None` at the top level points to the current execution context.
- Domain state objects are nested Pydantic models (e.g., `product_state: ProductState | None`), instantiated lazily when a domain workflow starts.

## 4. Suspended Workflows
- **Mechanism**: A strictly bounded LIFO stack: `suspended_domains: list[OrchestrationDomain] = Field(max_length=3)`.
- **Suspend (Supervisor)**: Pushes `active_domain` to `suspended_domains` and updates `active_domain` to the new target.
- **Resume (Supervisor)**: Pops the requested domain from `suspended_domains` and sets it as `active_domain`.
- **Isolation**: Each domain retains its isolated `DomainState` (including ticket ID and facts) while suspended.

## 5. Ticket Relationship
- Tickets are domain-bound context (`ProductState.active_ticket_id`, `OrderState.active_ticket_id`).
- This naturally allows a single session to handle an active Product issue (Ticket A) and an active Order issue (Ticket B) in parallel, switching between them.
- `TicketLifecycleService` will be updated to operate on the `active_domain`'s specific state object.

## 6. Completed Workflows
- When a domain agent resolves a request, it sets its internal `domain_status = COMPLETED`.
- If the customer re-engages in the same domain (e.g., semantic intent triggers the completed domain again), the Supervisor's `START_NEW` rule applies.
- **START_NEW**: Replaces the completed `DomainState` with a fresh instance, effectively wiping stale facts and creating a clean slate for the new interaction.

## 7. Escalation
- `ESCALATED` is a terminal **Global Status**.
- When triggered, autonomous AI execution halts entirely.
- Domain states remain frozen exactly as they were, allowing human agents to read the precise context of the failure/escalation via the persisted schema.

## 8. Two-Level State Machine
- **Global**: `IDLE` -> `IN_PROGRESS` -> `ESCALATED`
- **Domain**: `IN_PROGRESS` <-> `AWAITING_INPUT` -> `COMPLETED` | `FAILED`
The Supervisor manages the Global layer; Domain Agents manage their respective Domain layer.

## 9. Transition Ownership & Mutation Boundaries
- **Supervisor**: Mutates `global_status`, `active_domain`, `suspended_domains`. Never mutates domain facts.
- **Domain Agents**: Mutates their respective `DomainState` facts and `domain_status`.
- **TicketLifecycleService**: Mutates the `active_ticket_id` within the currently active `DomainState`.

## 10. Stale Workflows & Boundedness Limits
- **Limits**: Maximum 3 suspended workflows.
- **Staleness**: Configuration-driven TTL (e.g., 30 minutes). Enforced by the persistence layer (e.g., Postgres/Redis TTL or a cron job pruning stale session state), not hardcoded in business logic.

## 11. Concurrency Implications
- The F.2 Pydantic schema is deterministically serializable to JSONB.
- Preserves the "one ordered message = one workflow transition" rule by providing a flat, transactional data structure suited for external optimistic locking.

## 12. Migration Strategy
1. **F.2.1**: Define new multi-domain schema (`ProductState`, etc.) alongside legacy fields in `WorkflowState`.
2. **F.2.2**: Implement `@property` adapters in `WorkflowState` to sync legacy fields (e.g., `state.product_id`) with `state.product_state.product_id`.
3. **F.2.3**: Update `ProductAgent` and `TicketLifecycleService` to use `state.product_state` explicitly.
4. **F.2.4**: Remove legacy flattened fields from `WorkflowState` and finalize migration.

## 13. Invalid State Validation
Impossible states (e.g., `active_domain = PRODUCT` but `product_state = None`, or `ESCALATED` but receiving new mutations) will be enforced via Pydantic `@model_validator` methods to fail closed at deserialization/mutation time.
