# ADR-010: Email Side-Effect Reliability and Outbox Pattern

## 1. Context
Following the implementation of Postgres transactional idempotency (F.2.8.3 Slice 2B), the application successfully guarantees at-most-once business mutation semantics. However, email notification side-effects currently operate independently of these guarantees. Because email delivery inherently involves a network boundary (SMTP/Gmail API) and lacks distributed transaction support, a crash window exists between committing database state and dispatching an email.

This document discovers the current state of email side-effects, clearly separates the current (failed-closed) runtime from the intended future flow, and proposes an outbox-based architecture to establish reliable, durable, at-least-once delivery.

## 2. Formal Identity Contracts
To guarantee at-least-once dispatch without generating unnecessary duplicates on retries, every email notification must possess a deterministic, durable identity. We strictly separate four concepts:

1. **Client Request Identity:** Scope defined by `(customer_id, client_request_id)`.
2. **Business Event Identity:** The durable database record of a state mutation.
3. **Notification Identity:** The logical uniqueness of a single notification dispatch intent.
4. **Outbox Row Identity:** The database primary key (`event_id = uuid_generate_v4()`) for a specific row.

**Logical Notification Identity Definition:**
`logical_identity_hash = SHA256(serialize(namespace, source_event_id, notification_type, recipient_identity))`
The serialization uses a deterministic JSON array of strings (e.g. `["v1", "customer:1|req:A", "TICKET_CREATED", "customer:1"]`) to prevent ambiguous concatenations.

**Recipient Identity vs Address:**
- `recipient_identity`: The stable logical principal (e.g., `customer:123`). Used in the deduplication hash.
- `recipient_address`: The snapshotted email address used for delivery, stored in the outbox payload.

### Source Event ID Mapping
- **Ticket Creation / Resolution / Order Update:** The source event ID is the canonical tuple `(customer_id, client_request_id)`.
- **Escalation:** The current schema (`conversations.escalated = true`) is merely boolean state and lacks a durable event identity capable of distinguishing repeated transitions (E1 vs E2). The future contract requires creating an explicit durable escalation event identifier alongside the session state change.
- **Custom Ticket Email:** Will use the `(customer_id, client_request_id)` canonical tuple (must be introduced to the LLM tool).

## 3. Transaction Ownership & Durability Strategies

### A. Business Mutations (Tickets/Orders)
The primary business mutation transaction explicitly owns the outbox insert, guaranteeing atomicity alongside the core entity changes.

### B. Escalation Durability & Crash Analysis
With a durable escalation event identity in place, the crash windows are:
- **Scenario A:** Durable escalation event committed → Process crashes before outbox enqueue.
  *Reconciliation:* A background sweep discovers the exact missing notification by matching the durable escalation event against the outbox records. (This is impossible with only `escalated = true`).
- **Scenario B:** Outbox committed → Process crashes.
  *Reconciliation:* The sweep finds the existing logical notification and does not duplicate it.
- **Scenario C:** SMTP accepted → Process crashes before `SENT` update.
  *Reconciliation:* The row times out and reverts to `RETRYABLE`. A retry duplicates the delivery because SMTP has no application-level exactly-once guarantee.

### C. Custom Email Identity Strategy
The `send_custom_ticket_email` LLM tool operates inline without a business transaction.
**Strategy:**
- The tool requires `client_request_id`.
- It invokes a standalone idempotent RPC to record the intent and enqueue the outbox row.
- **Replay:** Same request ID + same payload skips enqueue.
- **Conflict:** Same request ID + changed payload raises `IdempotencyConflict`.
- **New Notification:** New request ID + same payload enqueues a new outbox row.

## 4. Current vs. Future Runtime Distinction
This architecture describes the post-integration architecture.
- **Current Runtime:** The HTTP ticket and order mutation routes currently **fail closed** because `client_request_id` is missing in their implementations. Therefore, their email blocks are unreachable. Escalation and custom email paths remain active but execute inline without durability guarantees.
- **Future Runtime:** Once `client_request_id` is integrated, these paths will route through the outbox layer.

## 5. Exact State Machine & Payload Contract

**States:** `PENDING -> PROCESSING`, `RETRYABLE -> PROCESSING`, `PROCESSING -> SENT`, `PROCESSING -> RETRYABLE`, `PROCESSING -> FAILED`.

**Worker Attributes:** `attempt_count`, `claimed_at`, `next_attempt_at`.

**Atomic Claim SQL:**
```sql
UPDATE outbox_events SET status = 'PROCESSING', claimed_at = NOW(), attempt_count = attempt_count + 1 WHERE id IN (SELECT id FROM outbox_events WHERE status IN ('PENDING', 'RETRYABLE') AND next_attempt_at <= NOW() FOR UPDATE SKIP LOCKED) RETURNING *;
```
**Stale Recovery:** A row in `PROCESSING` whose `claimed_at` is older than a lease threshold is reverted to `RETRYABLE`.

**Payload Contract:**
Contains snapshotted `recipient_address`, template identifier, and referenced business IDs. Fully rendered HTML is omitted to minimize PII. If referenced DB records are deleted prior to dispatch, the worker fails gracefully.

## 6. Worker Model Decision (Provisional)
A FastAPI in-process background worker utilizing `SELECT ... FOR UPDATE SKIP LOCKED` is the provisional choice.
**Tradeoffs:**
- **Lifecycle:** Ties email dispatch to the API lifecycle. Abrupt `SIGTERM` kills rely entirely on stale-processing recovery.
- **Multi-Replica:** Safely supported by `SKIP LOCKED`.
- **Failure Isolation:** Worker memory leaks or blocking operations directly degrade the API server.

## 7. Migration Slices
- **3.1:** Notification/event identity contract.
- **3.2:** Outbox relational schema & atomic enqueue.
- **3.3:** Worker/claim/retry lifecycle.
- **3.4:** Migrate ticket create/resolution HTTP flows.
- **3.5:** Migrate WebSocket escalation flows.
- **3.6:** Migrate custom email LLM tool flow.
- **3.7:** Observability/reconciliation.
