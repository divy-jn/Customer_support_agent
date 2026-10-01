# Phase F.2.8.3 Discovery: Side-Effect Idempotency & Crash/Retry Semantics

## 1. Objective and Scope
The goal of this phase is to establish safe execution guarantees for external side effects within the customer support agent architecture. While F.2.8.2 introduced a robust CAS-based concurrency model for **internal session state** (WorkflowState), it does not natively provide idempotency for **external mutations** (e.g., creating tickets, updating databases, sending emails).

This document analyzes current side effects, deduplication behavior, crash windows, and defines architectural boundaries for external idempotency.

## 2. Current Side-Effect Inventory
Externally visible side effects triggered by the application:

| Operation | Component | Target System | Mutating? | Naturally Idempotent? |
| :--- | :--- | :--- | :--- | :--- |
| `create_ticket` | `TicketLifecycleService`, `db_agent` | Supabase DB | Yes | No |
| `update_ticket` | `TicketLifecycleService`, `db_agent` | Supabase DB | Yes | Partially (status/priority updates are idempotent, `description_append` is NOT) |
| `cancel_order` | `app/tools.py` | Supabase DB | Yes | No (throws error if already cancelled) |
| `process_refund` | `app/tools.py` | Supabase DB | Yes | No (throws error if already refunded) |
| `send_*_email` | `app/email_service.py` | Gmail / SMTP | Yes | No (duplicates will be sent without provider keys) |

## 3. Required Failure Matrix

| Operation | Concurrent Duplicate | Crash Before Commit | Crash After External Success | Lost Response / Timeout | Safe Retry Semantics Required |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `create_ticket` | `_find_matching_ticket` race condition -> Double INSERT | Re-execution attempts INSERT again -> OK | Re-execution attempts INSERT again -> Duplicate ticket | Re-execution attempts INSERT again -> Duplicate ticket | Requires DB-level uniqueness constraint or idempotency key linked to operation identity. |
| `update_ticket` (`description_append`) | Duplicate appends interleave | Re-execution attempts append again -> OK | Re-execution attempts append again -> Duplicate text | Re-execution attempts append again -> Duplicate text | Requires passing idempotency key or structured log appends mapped to request identity. |
| `cancel_order` | Database locking/race on status update | Re-execution attempts update again -> OK | Re-execution sees `status=cancelled`, throws error, fails LLM | Re-execution sees `status=cancelled`, throws error | Repeated execution of the *same logical operation* must return the successful terminal result. |
| `process_refund` | Double refund API / DB updates | Re-execution attempts update again -> OK | Re-execution sees `status=refunded`, throws error, fails LLM | Re-execution sees `status=refunded`, throws error | Repeated execution of the *same logical operation* must return the successful terminal result. |
| `send_*_email` | Multiple concurrent sends triggered | Re-execution triggers send again -> OK | Re-execution triggers send again -> Duplicate email | Re-execution triggers send again -> Duplicate email | Requires outbox for durable dispatch + provider idempotency to prevent duplicate delivery. |

## 4. Crash Window & Retry Semantics
In this architecture, side-effect safety must not depend on LangGraph execution state. We do not have durable LangGraph checkpoints. Re-execution of the agent turn (due to reconnects, crash-recovery, or CAS conflicts) can resubmit the same logical side effect.

- **Crash After External Success**: A tool successfully mutates an external system, but the process crashes before the `save_session_conditional` completes. The CAS never happens. Upon retry, the side-effect executes again.
- **Lost Response**: A network timeout occurs after the external system successfully applies the mutation. The agent assumes failure and may retry.

## 5. Deduplication vs Concurrency Race
- **Ticket Deduplication**: `TicketLifecycleService._find_matching_ticket()` provides *business-level sequential deduplication* by looking up existing tickets based on customer, product, and order.
- **Ticket Concurrency Race**: A severe gap exists under concurrency. If two requests execute `_find_matching_ticket()` simultaneously, both yield "not found" and proceed to `INSERT`. This highlights the lack of *concurrency-safe duplicate prevention* at the database level.

## 6. Exactly-Once Terminology
To establish realistic guarantees, we explicitly distinguish these semantic levels:
- **At-most-once submission**: Ensuring the UI/client prevents duplicate submits (front-end concern).
- **At-least-once retry**: The orchestrator's mechanism of retrying failed or incomplete turns.
- **Exactly-once intent**: The user's goal (e.g., "cancel this order once").
- **Effectively-once business outcome**: The target state is applied idempotently (the database reflects one cancellation regardless of retry count). We can guarantee this for Supabase mutations via relational transactions and idempotency keys.
- **Exactly-once external delivery**: Preventing a duplicate email dispatch. We cannot guarantee this perfectly with just an outbox; it requires provider-level (SMTP/Gmail) idempotency support. Without it, duplicate delivery remains possible in the crash-after-send window.

## 7. Operation Identity Candidates
A naive key like `{session_id}:{turn_count}` or raw argument hashing is insufficient because one turn may legitimately invoke the same action multiple times on different targets. Furthermore, the LLM `tool_call_id` is fundamentally unstable across retries.

**Discovery-Level Identity Candidate**:
An operation identity should comprise:
`{domain} + {operation_type} + {canonical_business_target} + {canonical_semantically_relevant_payload}`

Example for Order Cancellation:
`domain=order, operation=cancel, target=order_123`

Session ID and turn metadata may be retained for traceability but must not act as the universal business idempotency identity.

## 8. Architectural Boundary
The discovery converges on this clear separation of responsibilities:

- **Session CAS**: Orchestration-state concurrency.
- **Operation Identity**: Logical side-effect identity.
- **Durable Idempotency Mechanism**: Duplicate business-operation prevention (within relational DB).
- **Outbox/Provider Reconciliation**: External side-effect delivery semantics.
- **WorkflowState**: NOT the idempotency database.

## 9. Migration Slices (For Future Implementation)
*Do not implement these yet.*
- **Slice 1**: Standardize `cancel_order` and `process_refund` to return previously established terminal results.
- **Slice 2**: Implement relational idempotency boundaries in Supabase to harden ticket creation/updates.
- **Slice 3**: Implement outbox pattern for emails, acknowledging exactly-once delivery limitations without provider keys.
