# PHASE F.2.8.3 SLICE 3.5 DISCOVERY

## 1. Durable Escalation Lifecycle State Machine & Serialization

To ensure consistent escalation transitions during high-concurrency websocket floods or race conditions, we must introduce a durable, per-session escalation lifecycle state machine. This is tracked strictly in Postgres on a single per-session row.

**Durable State Row Existence Invariant:**
*   Exactly one durable current-escalation-state record MUST exist per session.
*   The `session_id` must be uniquely identified by that durable state record.
*   The initial creation of that record must itself be concurrency-safe (e.g., via safe upsert/initialization logic).
*   This durable row MUST exist before normal escalation/release transitions rely on row-locking serialization.

**State Machine:**
*   `NONE` → `ACTIVE` = **E1** (First escalation).
*   `ACTIVE` → `RELEASED` = Explicit human release.
*   `RELEASED` → `ACTIVE` = **E2** (Subsequent escalation).
*   `ACTIVE` + new escalation = Bypass/Reject; no event is ever created.

**Serialization Mechanism:**
Escalation creation and release MUST serialize on this same durable current-escalation-state row lock in Postgres (e.g., `SELECT ... FOR UPDATE`). Redis must NEVER participate in deciding the validity of the transition. The Postgres row lock and the state machine strictly dictate the sequence of transitions.

## 2. Immutable Escalation History vs Current Escalation State

The architecture distinctly separates three layers:

*   **A. Immutable Escalation History (`escalation_events` table):** An append-only log of escalation occurrences. E1 and E2 represent distinct rows.
*   **B. Durable Current Escalation Lifecycle/State:** The serialized Postgres state machine tracking `NONE`, `ACTIVE`, or `RELEASED`.
*   **C. Redis Live Projection:** The volatile cache of the durable current state (`mode = "human" | "ai"`).

## 3. Same-Request Redis Reconciliation

Redis is strictly a projection. To ensure that a stale Redis cache NEVER routes the current request incorrectly, the durable escalation state is consulted BEFORE any routing decision. 

**Required Sequence:**
1. Request arrives.
2. Read authoritative durable escalation state.
3. Reconcile/repair stale Redis projection (if out of sync).
4. Make routing decision based on the repaired mode.

## 4. Concurrent Escalation & Release Analysis

Because Redis and request arrival order do not govern transitions, the database serialization order strictly determines the valid result.

*   **Concurrent Escalation Requests:** Two distinct `client_request_id` values arriving concurrently for a non-active session. The Postgres row lock forces serialization. The first acquires the lock, observes the `NONE`/`RELEASED` state, transitions to `ACTIVE`, and creates **E1**. The second request blocks, acquires the lock after, observes the `ACTIVE` state, and bypasses/rejects the request. It MUST NOT create E2. E2 is impossible without a committed `RELEASED` state.
*   **Concurrent Release vs Escalation Semantics:**
    *   If release serializes first: The state transitions `ACTIVE` → `RELEASED`. The blocked escalation request then acquires the lock and transitions it back `RELEASED` → `ACTIVE`, creating **E2**.
    *   If escalation serializes first: The state is already `ACTIVE`, so the escalation is bypassed (no event). The blocked release request then acquires the lock and transitions it `ACTIVE` → `RELEASED`.

## 5. Durable Payload Fingerprint (Idempotency Contract)

To distinguish between a legitimate replay and an idempotency conflict, the database uniqueness constraint `UNIQUE(customer_id, client_request_id)` is augmented with a durable payload fingerprint.

*   **Canonical Input:** Deterministic JSON serialization of the user's triggering message payload.
*   **Deterministic Hash:** `payload_hash = SHA256(canonical_input)`.
*   **Stored Fingerprint:** The `payload_hash` is stored alongside the `escalation_events` record (or idempotency key table).
*   **Replay Comparison:** Same `customer_id` + same `client_request_id` + same `payload_hash` → Exact Replay (safely skips without duplication).
*   **Conflict Comparison:** Same `customer_id` + same `client_request_id` + *different* `payload_hash` → Raises `IdempotencyConflict`.

## 6. Escalation Notification Association

Each notification belongs to a specific escalation event UUID (`escalation_event_id`). 
For E1:
*   `ESCALATION_TEAM` (tied to E1)
*   `ESCALATION_CUSTOMER` (tied to E1)

For E2:
*   `ESCALATION_TEAM` (tied to E2)
*   `ESCALATION_CUSTOMER` (tied to E2)

Therefore, E1 notifications ≠ E2 notifications, even when the customer, support recipient, and notification type are identical. The `source_event_id` in the outbox strictly binds to the unique UUID of the event.

## 7. Reconciliation Rules

Reconciliation must precisely use the specific `escalation_event_id` AND the specific notification identity. It cannot infer missing notifications merely from `session_id` or current escalated state.

For each record in `escalation_events`:
1.  Determine if Support Notification is required (Always `True`).
2.  Determine if Customer Notification is required (`customer_notification_required` boolean on the event).

Then, querying the outbox by the full logical hash:
*   **Required + missing:** Repair by enqueueing the missing outbox intent.
*   **Not required:** No repair (skip intentionally).
*   **Existing:** No duplicate action.

## 8. Transaction Ownership — Precise Boundaries

One single Postgres transaction strictly owns:
1.  **Transition validation** (acquiring the session row lock and enforcing the state machine).
2.  **Durable escalation event creation** (`INSERT INTO escalation_events`).
3.  **Current durable escalation state mutation** (e.g., `UPDATE session_states SET current_escalation_status = 'ACTIVE'`).
4.  **Required outbox notification intents** (`INSERT INTO outbox_events`).

Redis state is a projection updated *after* the Postgres commit. Postgres and Redis are NOT one distributed transaction.

## 9. Failure & Concurrency Matrix

| Scenario | Expected Durable State |
| :--- | :--- |
| **Concurrent E1 requests** (distinct request IDs) | First locks & succeeds. Second locks, sees ACTIVE, bypasses. Only E1 created. |
| **Concurrent escalation while ACTIVE** | Request acquires lock, sees ACTIVE, bypasses. No event created. |
| **Concurrent release + escalation** | Serialized strictly by Postgres database lock order. |
| **Release serializes first, then Escalation** | State becomes RELEASED, then ACTIVE (creating E2). |
| **Escalation serializes first, then Release** | Escalation is bypassed, then state becomes RELEASED. |
| **Concurrent E2 attempts after RELEASED** | First locks & succeeds (creates E2). Second locks, sees ACTIVE, bypasses. |
| **Redis stale during races** | Reconciled *before* routing decisions on the exact same request turn. |
| **Event history committed / current state fails** | Prevented by transaction atomicity (both commit or rollback together). |
| **Current state committed / notification missing** | Prevented by transaction atomicity (outbox commits with state). |
| **E1 released then E2 created** | Valid state. E1 and E2 have distinct histories; current state is `ACTIVE`. |
| **Same request ID different payload** | Raises `IdempotencyConflict`; no new event, no state change. |
| **Same request ID exact replay** | Idempotency hit; no second event, outbox inserts skipped. |
| **Redis loss** | Full reconstruction of mode from authoritative durable current escalation state before routing. |

## 10. Acceptance Criteria

*   **E1 creates durable history + ACTIVE durable state:** Verifies initial escalation.
*   **Release changes durable current state to RELEASED:** Verifies human handoff reversal.
*   **E2 after release creates a new durable event:** Verifies repeated escalation capabilities.
*   **Replay of E1 request does not create E2:** Enforces request identity idempotency.
*   **Same request ID with changed fingerprint conflicts:** Enforces `payload_hash` conflict detection.
*   **Notification identity for E1 differs from E2:** Verifies proper outbox association.
*   **Redis stale state is repaired before routing:** Ensures AI does not inappropriately answer or ignore users during the exact current request.
*   **Reconciliation does not confuse E1/E2:** Verifies accurate background sweeps using `escalation_event_id`.
*   **Customer/support notifications remain independently represented:** Verifies separate outbox rows.
*   **Missing optional customer email does not break support notification:** Verifies resilient enqueueing.
*   **Event/outbox commit atomicity:** Enforces single Postgres transaction ownership.
*   **Concurrent E1 requests serialize safely:** Only one creates E1; the other bypasses.
*   **Concurrent escalation while ACTIVE is bypassed:** Never creates another escalation event.
*   **Concurrent E2 attempts serialize safely:** Only one creates E2; the other bypasses.
*   **Concurrent release + escalation serialize correctly:** Database serialization order determines transition strictly (e.g., Release then Escalation = E2; Escalation then Release = bypassed escalation + RELEASED state).
*   **Durable row existence:** State row initialization is concurrency-safe and exists prior to normal lock utilization.
