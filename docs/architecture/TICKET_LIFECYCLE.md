# Ticket Lifecycle Architecture

## Purpose
The Ticket Lifecycle Service (`app.tickets.lifecycle.TicketLifecycleService`) provides a centralized, deterministic policy boundary for ticket creation and mutation. 

It ensures that:
1. Every distinct customer issue is tracked via a ticket.
2. Identical/ongoing issues for the same customer are deduplicated into the same ticket.
3. The LLM is **never** responsible for arbitrarily deciding whether an issue deserves a ticket.
4. Ticket operations are securely isolated by the authenticated `customer_id`.

## Core Policies

### 1. Issue Identity (Bounded Deterministic Compatibility)
Candidate matching is not based on fuzzy semantic text, but on an `IssueIdentity` struct providing bounded deterministic compatibility. Since the current schema lacks strict relational tracking for products inside tickets, we define identity compatibility defensively:
- `domain` (e.g. product)
- `intent` (e.g. technical_support)
- `ticket_type` (e.g. technical_issue)
- `order_id` (optional, explicit dependency)
- `product_name` (optional, explicit dependency via subject text)

Two issues are considered "compatible" if and only if:
1. Ticket types match exactly.
2. If `order_id` is supplied, it does not conflict with the ticket's `order_id`. A null ticket `order_id` does NOT automatically absorb a newly supplied `order_id` unless it is explicitly continuing the active workflow (via `active_ticket_id`).
3. If `product_name` is supplied, it does not conflict with the ticket's product. A generic ticket without reliable product identity does NOT automatically absorb a new product issue unless explicitly continuing the active workflow.

### 2. Issue Identity & Creation (CREATE)
When a customer raises an issue, the lifecycle service checks for an existing open ticket that is compatible with the `IssueIdentity`.
If no compatible ticket is found, or if candidates are ambiguous (see below), a **new ticket** is deterministically created.

### 3. Issue Continuation (UPDATE)
If an existing open ticket is found that aligns with the `IssueIdentity`, the service will **mutate** the existing ticket. 
Mutation means actual data change: the new message is appended to the ticket's history (`description_append`) and the `priority` may be bumped. 
This queries the authoritative persistence layer for the latest `description`, appends the new text, and updates the `updated_at` timestamp.
We NEVER "fake" an update by simply returning `UPDATED` without modifying the ticket.

### 4. Candidate Matching Hierarchy & Ambiguity
If multiple open tickets exist, we prioritize candidates securely and deterministically:
1. Exact `active_ticket_id` matching an open ticket with a fully compatible `IssueIdentity`.
2. Exact `ticket_type` + exact `order_id` match.
3. Exact `ticket_type` + exact `product_name` match.

**Ambiguity Policy**: If multiple candidates match equally, or if there are multiple same-type open tickets with no strong anchor (no explicit order/product, no active ticket ID), the system safely defaults to **CREATE**. We do NOT use "most recent" as a semantic tie-breaker for unrelated issues to prevent unrelated generic tickets from incorrectly merging.

### 5. Stale Tickets
We actively defend against stale tickets absorbing unrelated work by strictly enforcing `status != closed`. If a ticket is closed, it is completely ignored. Additionally, mismatched identifiers (`order_id`, `product_name`) or lack thereof will conservatively reject candidates unless there is an active session link.

### 6. Customer Isolation
Authenticated `customer_id` is the absolute perimeter. The service forcefully filters all lookups (`eq("customer_id", ...)`) and refuses to merge or attach to any ticket ID that crosses customer boundaries.

### 7. Failure Semantics
- **Lookup Failure**: If the database lookup for existing tickets fails, the service returns `FAILED`. It **NEVER** falls through to `CREATE`. 
- **Create/Update Failure**: If insertion or mutation fails, the service returns `FAILED`.
- **Sanitization**: Raw exception details and DB internals are never leaked in the `TicketLifecycleResult.reason`. 

### 8. Idempotency Limitation
This implementation achieves **workflow-state continuity** and **best-effort duplicate prevention**.
It does **NOT** provide a mathematically pure "exactly-once" idempotency guarantee, because there is no robust client-provided message idempotency key (e.g., `Idempotency-Key` header) currently plumbed through the WebSocket stack. A rapid concurrent retry of the exact same message could technically race the `find->create` check.

## Boundary Implementation
Agents (e.g., `ProductAgent`) do **not** directly invoke `create_ticket` or `update_ticket` tools. 

Instead, agents extract verified state, and hand an `IssueContext` over to the `TicketLifecycleService`. The service enforces the policy, creates or updates the ticket, and returns a `TicketLifecycleResult`. 

The Agent then persists the returned `ticket_id` into its `WorkflowState` so that subsequent turns in the same workflow safely map to the same ticket.
