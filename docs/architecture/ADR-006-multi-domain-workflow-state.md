# ADR-006: Multi-Domain Workflow State (F.2)

## Status
Proposed (Phase F.2 Discovery)

## Context
As the agent transitions from a single Product domain to multiple domains (Product, Order, Payment), the current monolithic `WorkflowState` breaks down. A single ticket ID cannot simultaneously represent an active Product troubleshooting session and an active Order return session.

To support deterministic orchestration by the Supervisor (ADR-005), we need a strictly typed state structure that natively handles isolated domain facts, supports bounded workflow suspension, explicitly versions the schema, and prepares for optimistic concurrency.

## Decision
We will restructure `WorkflowState` into a strongly typed architecture representing exactly **one live workflow instance per domain per session**:

1. **Global Orchestration State**: 
   - Managed strictly by the `Supervisor`.
   - Tracks `schema_version` and `state_revision` for atomic persistence.
   - Contains `active_domain` and `suspended_domains` (bounded LIFO stack of max 3).
   - *Note*: Pending Approval state schema is deferred to Phase F.8.

2. **Isolated Domain State Models**:
   - `product_state: ProductState`, `order_state: OrderState`, `payment_state: PaymentState`.
   - Each tracks its own `active_ticket_id` (managed by TicketLifecycleService) and `domain_status` (IN_PROGRESS, AWAITING_INPUT, SUSPENDED, COMPLETED, FAILED, ESCALATED).
   - Domain states are strictly isolated from one another.

3. **Explicit Migration & Compatibility**:
   - We explicitly reject implicit `@property` compatibility adapters.
   - We will implement explicit serialization functions: `WorkflowState.from_legacy()` and `WorkflowState.to_legacy_projection()` to ensure transparent and testable boundaries during the migration phase.

4. **Validation & Boundaries (Fail-Closed Matrix)**:
   - Impossible states (e.g. `active_domain == PRODUCT` but `product_state == None`) will be rejected by rigorous Pydantic validators, favoring fail-closed over silent normalization.
   - Completed workflows are inherently historical; re-triggering a domain after completion issues a `START_NEW` transition that creates a clean state.

## Consequences

### Positive
- Strict isolation of domain facts prevents cross-domain leakage (e.g., Product tickets won't pollute Order data).
- Deterministic Pydantic validation guarantees predictability (no arbitrary `dict` injections).
- Schema versioning and monotonic revisions lay the groundwork for optimistic concurrency locking.
- Explicit migration boundaries (`from_legacy`) avoid mysterious database-to-memory deserialization errors.

### Negative
- `TicketLifecycleService` must be refactored to consume/update domain-specific states rather than a global root state.
- Increased verbosity during the migration slices (F.2.1 through F.2.7).
