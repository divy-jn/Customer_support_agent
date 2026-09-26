# ADR-006: Multi-Domain Workflow State (F.2)

## Status
Proposed (Phase F.2 Discovery)

## Context
As the agent transitions from a single Product domain to multiple domains (Product, Order, Payment), the current `WorkflowState` (which flattens product_id, order_id, active_ticket_id into a single monolithic model) breaks down. A single ticket ID cannot represent both an active Product troubleshooting session and an active Order return session simultaneously.

To support deterministic orchestration by the Supervisor (ADR-005), we need a state structure that can cleanly isolate domain facts, support bounded workflow suspension, and avoid generic dictionaries (`domain_states: dict`) that evade static typing.

## Decision
We will restructure `WorkflowState` into a two-tiered, strongly typed architecture:

1. **Global Orchestration State**: 
   - Managed strictly by the `Supervisor`.
   - Contains `global_status` (IDLE, IN_PROGRESS, ESCALATED), `active_domain`, and `suspended_domains` (a bounded LIFO stack of max length 3).
   - Contains cross-cutting context (e.g., `pending_approval`).

2. **Isolated Domain State Models**:
   - `product_state: ProductState`, `order_state: OrderState`, `payment_state: PaymentState`.
   - Managed strictly by their respective Domain Agents.
   - Each state explicitly tracks its own `active_ticket_id` and `domain_status` (IN_PROGRESS, AWAITING_INPUT, COMPLETED).

3. **Validation & Boundaries**:
   - Impossible states (e.g. `active_domain == PRODUCT` but `product_state == None`) will be rejected by Pydantic validators.
   - Completed workflows are not silently resurrected; re-triggering a completed domain generates a `START_NEW` event that replaces the `DomainState` with a clean slate.

## Consequences

### Positive
- Strict isolation of domain facts (Product issues don't accidentally leak into Order tickets).
- Fully deterministic Pydantic validation; no arbitrary generic dictionaries.
- Clear alignment with the F.1 Supervisor decision boundaries.
- Retains transactionality (JSONB serializable) for concurrent environments.

### Negative
- Temporary complexity during the migration phase (F.2.1 - F.2.4) as existing systems are adapted from flat state to nested state.
- `TicketLifecycleService` must be refactored to accept/return specific DomainState tickets rather than the global state ticket.
