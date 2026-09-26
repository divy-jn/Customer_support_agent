# Ticket Lifecycle Architecture

## Purpose
The Ticket Lifecycle Service (`app.tickets.lifecycle.TicketLifecycleService`) provides a centralized, deterministic policy boundary for ticket creation and mutation. 

It ensures that:
1. Every distinct customer issue is tracked via a ticket.
2. Identical/ongoing issues for the same customer are deduplicated into the same ticket.
3. The LLM is **never** responsible for arbitrarily deciding whether an issue deserves a ticket.
4. Ticket operations are securely isolated by the authenticated `customer_id`.

## Core Policies

### 1. Issue Identity (Deterministic Fingerprint)
Candidate matching is not based on fuzzy semantic text, but on a rigid `IssueIdentity` struct:
- `domain` (e.g. product)
- `intent` (e.g. technical_support)
- `ticket_type` (e.g. technical_issue)
- `order_id` (optional, explicit dependency)
- `product_name` (optional, explicit dependency)

Two issues are considered "the same issue" if and only if their identities are strictly compatible (e.g., ticket types match exactly, and explicitly tracked order/product identifiers do not conflict).

### 2. Issue Identity & Creation (CREATE)
When a customer raises an issue, the lifecycle service checks for an existing open ticket that matches the `IssueIdentity`.
If no compatible ticket is found, or if the user pivots to a fundamentally different issue (e.g., changing `order_id` or `intent`), a **new ticket** is deterministically created.

### 3. Issue Continuation (UPDATE)
If an existing open ticket is found that perfectly aligns with the `IssueIdentity`, the service will **mutate** the existing ticket. 
Mutation means actual data change: the new message is appended to the ticket's history (`description_append`) and the `priority` may be bumped.
We NEVER "fake" an update by simply returning `UPDATED` without modifying the ticket.

### 4. Candidate Matching Hierarchy & Ambiguity
If multiple open tickets exist, we prioritize candidates securely and deterministically:
1. Exact `active_ticket_id` matching an open ticket with a fully compatible `IssueIdentity`.
2. Exact `ticket_type` + exact `order_id` match.
3. Exact `ticket_type` + exact `product_name` match.
4. If multiple candidates tie (or are ambiguous), we deterministically sort by `updated_at` (recency) and select the most recently updated compatible ticket.

### 5. Stale Tickets
We actively defend against stale tickets absorbing unrelated work by strictly enforcing `status != closed`. If a ticket is closed, it is completely ignored, forcing a new ticket to be created. Additionally, mismatched identifiers (`order_id`, `product_name`) will aggressively reject stale candidates.

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

Instead, agents extract verified state (like `order_id` or `product_name`), and hand an `IssueContext` over to the `TicketLifecycleService`. The service enforces the policy, creates or updates the ticket, and returns a `TicketLifecycleResult`. 

The Agent then persists the returned `ticket_id` into its `WorkflowState` so that subsequent turns in the same workflow safely map to the same ticket.

```python
# ── Step 0.5: Central Ticket Lifecycle ──
issue_ctx = IssueContext(
    customer_id=context.customer_id,
    domain="product",
    intent=context.semantic_intent,
    message=context.customer_message,
    order_id=merged_state.order_id,
    product_name=merged_state.product_name,
    urgency=context.urgency,
    sentiment=context.sentiment
)

ticket_res = TicketLifecycleService.process_issue(issue_ctx, merged_state.active_ticket_id)
if ticket_res.ticket_id:
    merged_state.active_ticket_id = ticket_res.ticket_id
```
