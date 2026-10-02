# PHASE F.2.8.3 SLICE 3 — EMAIL SIDE-EFFECT RELIABILITY / OUTBOX DISCOVERY

## 1. Formal Identity Contracts

To ensure precise deduplication and reliable dispatch, we must strictly distinguish four identities:

1. **Client Request Identity:** The caller's intent identifier, scoped by `(customer_id, client_request_id)`.
2. **Business Event Identity:** The durable record of a state mutation (e.g., Ticket ID 123, or a specific Escalation Event record).
3. **Notification Identity:** The logical uniqueness of a single notification dispatch intent.
4. **Outbox Row Identity:** The database primary key (`event_id = uuid_generate_v4()`) for a specific row in the `outbox_events` table.

### 1.1 Source Event ID
The `source_event_id` ties the notification to its origin.

- **Ticket Creation / Resolution / Order Update:** The source event ID is the canonical tuple `(customer_id, client_request_id)`.
- **Custom Ticket Email:** The LLM tool does not mutate business state, but its invocation must be made durable. The source event ID will be the canonical tuple `(customer_id, client_request_id)`, requiring the tool to accept `client_request_id` and use the idempotency layer to enqueue the outbox row.
- **Escalation:** **(Current Gap)** The current schema records escalation merely as a boolean state (`conversations.escalated = true`). This is **not** a durable event history and cannot distinguish repeated escalation transitions (E1 vs. E2) or serve as a reliable source for reconciliation. The future contract requires creating an explicit durable escalation event identifier (e.g., an `escalation_events` append-only table or a monotonic sequence) inserted transactionally alongside the session state change.

### 1.2 Recipient Identity vs. Delivery Address
- **`recipient_identity`:** The stable logical principal receiving the email (e.g., `customer:456` or `team:support`).
- **`recipient_address`:** The actual email address (e.g., `alice@example.com`).

The outbox payload stores the snapshotted `recipient_address` for delivery, but the **logical deduplication uses the stable `recipient_identity`**. If a customer's email address changes after enqueue but before a retry, the retry uses the original snapshot, maintaining the stable identity without creating a duplicate notification.

### 1.3 Canonicalization and Notification Identity
`Notification Identity = hash(serialize(namespace, source_event_id, notification_type, recipient_identity))`

**Canonicalization Contract:**
To prevent ambiguous concatenations (e.g., "A" + "BC" vs "AB" + "C"), the tuple is deterministically serialized as a JSON array of strings (e.g., `["v1", "customer:123|req:abc", "TICKET_CREATED", "customer:123"]`). The `namespace` ("v1") ensures future schema contract changes do not silently collide with historical identities.

## 2. Escalation Crash Analysis & Reconciliation

With a durable escalation event identity in place (future contract), the crash windows are:

- **Scenario A (Loss):** Durable escalation event is committed to the database, but the process crashes before the outbox row is enqueued. 
  *Recovery:* A reconciliation sweep discovers the exact missing notification by matching the durable escalation event ID against the outbox's `source_event_id`. (This is impossible relying solely on the boolean `escalated = true`).
- **Scenario B (Safe):** Outbox row is committed, but the process crashes before sending. 
  *Recovery:* Reconciliation finds the existing logical notification in the outbox (`PENDING` or `PROCESSING` timeout) and does not duplicate it. The worker resumes dispatch.
- **Scenario C (Duplicate):** SMTP accepts the payload, but the process crashes before updating the outbox row to `SENT`. 
  *Recovery:* The row times out and reverts to `RETRYABLE`. The worker retries, resulting in duplicate email delivery because standard SMTP provides no application-level exactly-once guarantee.

## 3. Custom Email Identity Strategy

The `send_custom_ticket_email` LLM tool path currently operates inline without a business transaction.
- **Future Contract:** The tool schema will require `client_request_id`.
- **Identity Relationship:**
  - `client_request_id` + `customer_id` scopes the idempotency record.
  - The `source_event_id` is exactly this `(customer_id, client_request_id)` tuple.
- **Semantics:**
  - Same request ID + same payload = **Replay** (Idempotency cache hit; no new outbox row enqueued).
  - Same request ID + changed payload = **Conflict** (`IdempotencyConflict`).
  - New request ID + same payload = **Legitimate new notification**.

## 4. Exact Outbox State Machine & Payload

### 4.1 State Machine
- **PENDING:** Initial state. Transitions to `PROCESSING` upon worker claim.
- **PROCESSING:** Claimed. Transitions to `SENT` (success), `RETRYABLE` (transient failure), or `FAILED` (terminal failure).
- **SENT:** Terminal success.
- **RETRYABLE:** Transient failure; awaiting next attempt. Transitions to `PROCESSING`.
- **FAILED:** Terminal error (auth failure, 550 invalid recipient, max retries exceeded).

### 4.2 Claim and Lease
An atomic SQL operation claims rows and acquires leases:
```sql
UPDATE outbox_events 
SET status = 'PROCESSING', claimed_at = NOW(), attempt_count = attempt_count + 1 
WHERE id IN (
    SELECT id FROM outbox_events 
    WHERE status IN ('PENDING', 'RETRYABLE') AND next_attempt_at <= NOW() 
    FOR UPDATE SKIP LOCKED LIMIT 10
) RETURNING *;
```
**Stale Recovery:** Rows in `PROCESSING` with a `claimed_at` timestamp older than the lease threshold are reverted to `RETRYABLE` by a recovery sweep.

### 4.3 Payload Contract
The payload contains the minimum durable data for deterministic execution.
- Includes the snapshotted `recipient_address`.
- Includes the template identifier.
- Includes referenced business IDs (e.g., `ticket_id`).
- Minimizes PII by avoiding fully rendered HTML.
- **Missing Records:** If a referenced ticket is deleted from the DB before the worker runs, the worker fails gracefully.

## 5. Worker Decision (Provisional)

A FastAPI in-process background execution is provisionally selected but subject to operational constraints:
- **Lifecycle:** The worker is tied to the API process. `SIGTERM` kills the worker abruptly, relying entirely on the stale-processing recovery mechanism to unlock rows.
- **Multi-Replica:** `SKIP LOCKED` safely supports concurrent API pods.
- **Failure Isolation:** An OOM or event loop block in the email worker degrades the entire web server.
- **Complexity:** Simple to deploy, but couples concerns.

## 6. Current vs Future Runtime Distinction

- **Current Runtime:** The HTTP ticket and order mutation routes currently **fail closed** because `client_request_id` is not supplied by the callers. Therefore, their subsequent email dispatch blocks are entirely **unreachable**. Escalation and custom email paths remain active but execute inline without durability guarantees.
- **Future Architecture:** This discovery represents the post-integration architecture, applicable only once the callers provide the required request identities and the outbox layer is implemented.

## 7. Recommended Migration Order

1. **Slice 3.1:** Notification/event identity contract (Canonicalization, Tuples).
2. **Slice 3.2:** Outbox schema (`outbox_events`) + atomic enqueue operations.
3. **Slice 3.3:** Worker/claim/retry lifecycle and state machine.
4. **Slice 3.4:** Migrate ticket create/resolution HTTP flows.
5. **Slice 3.5:** Migrate escalation flows (Requires durable escalation event schema).
6. **Slice 3.6:** Migrate custom email LLM tool flow.
7. **Slice 3.7:** Observability/reconciliation.
