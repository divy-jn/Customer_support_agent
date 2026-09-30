# ADR-007: Ticket Context Migration (Phase F.2.5)

## Status
Proposed (Discovery Phase)

## Context
In Phase F.2, we introduced explicitly typed multi-domain workflow states (`ProductState`, `OrderState`, `PaymentState`) to replace the monolithic legacy `WorkflowState`. As part of F.2.4, `ProductAgent` was migrated to use `ProductState`.

The handling of ticket identity and lifecycle involves three categories of data and responsibility:
1. **Domain Facts**: `product_name`, `order_id`, etc.
2. **Ticket Identity/Context**: The transient mapping between a customer issue and the persistence layer.
3. **Ticket Lifecycle**: Issue identity resolution, candidate matching, deduplication, and ticket mutation.

Prior proposals suggested moving all ticket logic out of the domains and creating a single, centralized `TicketContext` owned by the `Supervisor`. This violated the F.2 architecture, which specifically designed `DomainState.active_ticket_id` to be the authoritative workflow-to-ticket association for that domain, preventing cross-domain overwrites (e.g., an Order ticket overwriting a Product ticket).

Furthermore, the existing `IssueContext` is too broad, serving as an unstructured bridge rather than a formalized boundary object.

## Decision

1. **TicketContext is a typed boundary object, not the owner of ticket state.**
   Domain Agents (like `ProductAgent`) will communicate with `TicketLifecycleService` by constructing and passing a newly defined, strictly typed `TicketContext`. `TicketContext` exists only as a transient payload to bridge extraction and lifecycle logic.

2. **DomainState.active_ticket_id is the authoritative workflow-to-ticket association for that domain.**
   We will strictly preserve `ProductState.active_ticket_id`, `OrderState.active_ticket_id`, and `PaymentState.active_ticket_id`. The Domain Agent is responsible for receiving the result from `TicketLifecycleService` and persisting the ticket ID into its *own* state.

3. **Legacy Root Compatibility Only**
   `WorkflowState.active_ticket_id` will remain exclusively as a legacy compatibility field. It will not be an alternative authoritative source of truth. Historically, explicit adapters (`to_legacy_projection`) projected domain ticket IDs into it for unmigrated consumers, but this outbound projection was retired in Phase F.2.7.3C.2.

4. **Ticket Timing Preserved**
   Issue Registration (creating/updating the ticket) must occur *before* skill validation and tool execution. This ensures that even if a tool crashes or input validation fails (e.g., AWAITING_INPUT), the customer's issue is safely tracked as a ticket in the database.

5. **Ticket Failure Rule**
   If `TicketLifecycleService` returns `FAILED`, the domain agent must not silently treat the issue as successfully registered, and the workflow must not falsely claim a ticket exists. Exact recovery behavior is deferred to F.2.5.3.

6. **Closed-Ticket Rule**
   Closed tickets are not reusable for new issue matching. They are excluded from candidate lookup, allowing a new compatible issue to CREATE a new ticket.

7. **Supervisor Boundaries Maintained**
   The `Supervisor` will remain an orchestrator of workflow transitions (`active_domain`, status). It will *not* assume ownership of ticket business policies.

## Consequences

### Positive
- **Domain Isolation Maintained**: A switch between the Product workflow and Order workflow correctly maintains independent tickets without overwriting context.
- **Strict Separation of Concerns**: Domain Agents extract facts; `TicketLifecycleService` handles deduplication and DB persistence. `TicketContext` safely transports transient facts (like a ProductAgent extracting an `order_id` for deduplication) without granting the Domain Agent permission to mutate unrelated states (like `OrderState`).
- **Crash Resilience**: Creating tickets before tool execution guarantees an audit trail regardless of execution failures.

### Negative
- **Boilerplate**: Domain Agents must explicitly construct `TicketContext` objects for the lifecycle service.

## Implementation Notes (For Future Phase)
- **Do NOT implement yet.** This is discovery only.
- Define `TicketContext` in `models.py`.
- Update `TicketLifecycleService.process_issue` to accept `TicketContext`.
- Update `ProductAgent` to construct `TicketContext` and map the result to `ProductState.active_ticket_id`.
