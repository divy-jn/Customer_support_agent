# ADR-012: Durable Escalation Events

## 1. Context

In Phase F.2.8.3 Slice 3, we are implementing a reliable transactional outbox pattern for side-effect dispatch (email notifications).

Currently, escalations trigger an inline email send (`send_escalation_email`) and mutate a volatile Redis state (`mode = "human"`). The lack of transaction atomicity between the Redis session state and notification dispatch creates severe crash windows. Furthermore, the absence of a durable serialization mechanism allows concurrent escalation requests to race, potentially creating duplicate alerts or corrupting session modes. 

This ADR mandates a durable escalation event contract, a formal idempotency model, a distinct Postgres vs. Redis authority hierarchy, and a serialized durable state machine to govern concurrent transitions.

## 2. Decision

We will introduce a distinct, immutable `escalation_events` table in Postgres as the history log, accompanied by a durable active state tracker to serialize all escalation lifecycle operations.

### 2.1 The Durable Escalation Lifecycle State Machine
All escalations and releases MUST serialize on a per-session durable current-escalation-state record/lock in Postgres. Exactly one durable current-escalation-state record must exist per session, uniquely identifying the `session_id`. Its initial creation must be concurrency-safe, ensuring the row exists *before* normal escalation/release transitions rely on serialization via row locking.

The allowed state machine is:
*   `NONE` → `ACTIVE` = E1 (First valid escalation).
*   `ACTIVE` → `RELEASED` = Explicit human release.
*   `RELEASED` → `ACTIVE` = E2 (Subsequent valid escalation).
*   `ACTIVE` + new escalation = Bypass/Reject (no event created).

### 2.2 Concurrent Release vs Escalation Semantics
Database serialization order determines the valid result, regardless of Redis state or request arrival order.
- **If release serializes first:** The state goes `ACTIVE` → `RELEASED`. The waiting escalation request then transitions the state `RELEASED` → `ACTIVE` (becoming E2).
- **If escalation serializes first while ACTIVE:** The escalation is bypassed (no event is created). The waiting release request then transitions the state `ACTIVE` → `RELEASED`.

### 2.3 Separation of History vs Current State
- **Immutable Escalation History (`escalation_events`):** An append-only log of every escalation occurrence.
- **Durable Current Escalation State:** The Postgres record tracking the `NONE`, `ACTIVE`, or `RELEASED` lifecycle.
- **Redis Live Projection (`mode`):** A volatile cache representing the durable current state.

### 2.4 Same-Request Redis Reconciliation
To ensure stale Redis caches never incorrectly route a request, the authoritative durable escalation state MUST be consulted before making routing decisions.
- **Required Sequence:** Request arrives → read authoritative durable escalation state → reconcile/repair stale Redis projection → make routing decision.

### 2.5 The Durable Event Contract & Request Fingerprint
- **Idempotency Key:** `UNIQUE(customer_id, client_request_id)`.
- **Payload Fingerprint:** A `payload_hash = SHA256(canonical_input)` will be stored alongside the request ID.
- **Idempotency Guarantee:** 
  - Same customer + same request ID + same fingerprint → Exact Replay (safely skipped).
  - Same customer + same request ID + different fingerprint → `IdempotencyConflict`.
  - Same customer + new request ID → New event (only if state allows, e.g., E2).

### 2.6 Transaction Ownership
A **single Postgres transaction** will strictly own:
1. **Transition validation** (via acquiring the session row lock).
2. **Durable escalation event creation** (`INSERT INTO escalation_events`).
3. **Current durable escalation state mutation** (`UPDATE` to `ACTIVE` or `RELEASED`).
4. **Required outbox notification intents** (`INSERT INTO outbox_events`).

Redis state is a projection updated *after* the Postgres commit. They are not a distributed transaction.

### 2.7 Notification Association & Optional Semantics
- Every notification intent is strictly bound to the specific `escalation_event_id`. Notifications for E1 are distinct from E2.
- If a customer lacks a usable email address, the customer notification is intentionally skipped.
- The `escalation_events` table includes a `customer_notification_required` boolean. Reconciliation queries this flag to distinguish between an intentionally skipped email and a genuinely lost outbox row, repairing only `required + missing` intents.

## 3. Consequences

- **Positive:** Concurrent requests are safely serialized, eliminating duplicate alerts or invalid state transitions regardless of arrival order.
- **Positive:** Escalations become strictly idempotent, driven by `client_request_id` and `payload_hash`.
- **Positive:** We establish a clear authority model (Postgres > Redis), explicitly repairing Redis stale states before AI routing decisions on the *current* request.
- **Negative:** Increased database latency during the websocket loop (mitigated by async DB drivers) due to the strict transactional row locking and initial state record UPSERT requirements.
