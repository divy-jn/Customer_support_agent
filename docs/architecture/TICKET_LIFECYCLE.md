# Ticket Lifecycle Architecture

## Purpose
The Ticket Lifecycle Service (`app.tickets.lifecycle.TicketLifecycleService`) provides a centralized, deterministic policy boundary for ticket creation and mutation. 

It ensures that:
1. Every distinct customer issue is tracked via a ticket.
2. Identical/ongoing issues for the same customer are deduplicated into the same ticket (no duplicate ticket spam).
3. The LLM is **never** responsible for arbitrarily deciding whether an issue deserves a ticket.
4. Ticket operations are always securely isolated by the authenticated `customer_id`.

## Core Policies

### 1. Issue Identity & Creation (CREATE)
When a customer raises a new issue (e.g., `technical_support`, `warranty_claim`, `billing_issue`, `escalation`), the lifecycle service checks for an existing open ticket that matches the issue footprint.

If no matching ticket is found, a **new ticket** is deterministically created.

### 2. Issue Continuation (UPDATE)
If an existing open ticket is found that matches the issue footprint, the service will **update** that ticket rather than creating a new one.

The matching footprint policy requires:
- The **same `customer_id`** (Strict authorization).
- And one of the following:
  - The same `active_ticket_id` exists in the current session's `WorkflowState`, and the target `order_id` has not diverged.
  - The category (e.g., `TicketType`) matches AND the target `order_id` or `product_name` explicitly matches.

### 3. Non-Issue Intents (IGNORED)
General inquiries (e.g., `product_inquiry`, `product_features`) do **not** generate tickets. The service immediately ignores these intents.

## Boundary Implementation
Agents (e.g., `ProductAgent`) do **not** directly invoke `create_ticket` or `update_ticket` tools. 

Instead, agents process the customer message, extract verified state (like `order_id` or `product_name`), and hand an `IssueContext` over to the `TicketLifecycleService`. The service enforces the policy and returns a `TicketLifecycleResult`. 

The Agent then persists the returned `ticket_id` into its `WorkflowState` so that subsequent turns in the same workflow cleanly map to the same ticket.

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

## Idempotency and Session Reliability
The ticket lifecycle provides session-level idempotency by utilizing the `WorkflowState` (the session's memory). 
Because `active_ticket_id` is tracked in state and evaluated alongside `order_id` / `product_name` dependency checking, the system naturally responds correctly to user pivoting (e.g., switching to a new order midway through a conversation will invalidate the old ticket reference and generate a new ticket).
