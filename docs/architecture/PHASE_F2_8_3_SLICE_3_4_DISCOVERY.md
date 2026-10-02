# Phase F.2.8.3 Slice 3.4: Ticket Notification Integration (Discovery)

This document details the architectural design for integrating ticket creation and resolution with the durable outbox and customer-scoped idempotency, ensuring absolute atomicity and isolation.

## 1. Existing Call Paths & Vulnerabilities

**Exact Current Ticket Call Paths:**
- **Create Ticket:** Client `POST /tickets` → `routes.py` → `tools.py` hashes payload → `supabase.rpc("execute_idempotent_operation")` commits transaction → Python receives success → `routes.py` calls `asyncio.to_thread(send_ticket_created_email)` (synchronous SMTP).
- **Resolve Ticket:** Client `PATCH /tickets/{id}` → `routes.py` → `tools.py` pre-checks ownership via HTTP `SELECT` → hashes payload → `supabase.rpc("execute_idempotent_operation")` commits transaction → Python receives success → `routes.py` calls `asyncio.to_thread(send_resolution_email)` (synchronous SMTP).

**Current Crash/Commit Windows:**
- **Crash before commit:** Safe (no ticket, no email).
- **Crash after commit but before SMTP:** Unsafe (ticket is durable, email is permanently lost).
- **SMTP Timeout:** Unsafe (ticket is durable, email exception swallowed, email permanently lost).
- **Idempotency Replay Danger:** A client retrying a request receives a cached success result, causing `routes.py` to erroneously dispatch a *second* duplicate synchronous email.

## 2. Real Transaction Boundary & Wrapper RPC Responsibilities

The backend leverages PostgREST (Supabase REST API), meaning interactive transactions (`BEGIN ... COMMIT` over HTTP) are impossible. Two separate `supabase.rpc()` calls are **never** atomic. 

**The Atomic Unit:**
Use exactly this conceptual model:

ONE Supabase/PostgREST RPC request
    ↓
one PostgreSQL transaction
    ↓
top-level wrapper function executes inside that transaction
    ↓
nested PostgreSQL function calls participate in the same transaction
    ↓
transaction commits or rolls back at the RPC boundary

The wrapper function is the orchestration boundary inside the transaction; it does not itself perform transaction control.

**Wrapper RPC Responsibilities:**
We will introduce a top-level Wrapper RPC that exclusively orchestrates the following sequence:
1. **Idempotency Lookup/Claim:** Safely lock the `(customer_id, client_request_id)` idempotency record.
2. **Authorization:** Validate customer ownership of the target resource inside the transaction.
3. **Business Mutation:** Execute the ticket INSERT or UPDATE.
4. **Notification Source-Event Identity:** Generate the deterministic logical notification hash.
5. **Outbox Enqueue:** Insert the outbox payload.
6. **Terminal Idempotency Result:** Save the final result.

**Strict Prohibition:** No synchronous SMTP or external HTTP calls may occur inside this transaction. `execute_idempotent_operation` should ideally remain generic, delegating specific notification logic to the wrapper RPC.

## 3. Explicit Execution Semantics (First vs Replay vs Conflict)

The Wrapper RPC must enforce three distinct execution paths:

- **FIRST_EXECUTION:**
  - perform business mutation
  - persist terminal idempotency result
  - enqueue notification
  - commit atomically
  
- **EXACT_REPLAY:**
  - return the stored terminal idempotency result
  - do NOT perform business mutation
  - do NOT enqueue an outbox notification

- **CONFLICT:**
  - reject request
  - do NOT perform business mutation
  - do NOT enqueue outbox notification

The UNIQUE(logical_identity_hash) constraint is defense-in-depth only. It is NOT the normal replay mechanism.

## 4. Outbox Retention vs Idempotent Replay Guarantee

If an outbox row is `SENT` and immediately purged, an `EXACT_REPLAY` could mistakenly enqueue a duplicate email if the wrapper isn't strictly aware of the replay state.

**Architectural Guarantee:**
Logical notification identity remains durable for at least the full idempotency replay horizon. 
- The deduplication mechanism (`ON CONFLICT (logical_identity_hash) DO NOTHING`) requires the historical logical identity to exist.
- Therefore, `SENT` outbox rows MUST NOT be purged until their corresponding idempotency records expire. Their retention horizons are strictly synchronized (e.g., both retained for 30 days).

## 5. Transactional Authorization

Customer authorization must be executed *inside* the single PostgreSQL transaction, completely replacing any prior non-atomic Python `SELECT` checks. 

**Authorization Flow:**
- A customer attempting to mutate another customer's ticket will fail the transactional `SELECT ... FOR UPDATE WHERE customer_id = p_customer_id`.
- Concurrent mutation races are safely serialized by the row lock.
- **Authorization Failure:** If authorization fails, the transaction generates an error, aborts the business mutation, and strictly **creates no outbox event**.

## 6. Ticket Creation Data Flow

Data flows linearly within the transaction:
1. **Business Mutation:** `INSERT INTO tickets ... RETURNING id INTO v_ticket_id`.
2. **Data Dependency:** The generated `v_ticket_id` is captured.
3. **Outbox Payload:** The wrapper constructs the JSON payload, injecting the dynamically generated `v_ticket_id`.
4. **Notification Identity:** `logical_identity_hash` is computed deterministically.
5. **Terminal Result:** The result is saved, cementing the transaction.

## 7. Ticket Resolution Semantics

- **Open/Resolvable Proof:** The wrapper performs `SELECT status FROM tickets WHERE id = p_ticket_id FOR UPDATE`. It proves the ticket exists, belongs to the customer, and `status != 'closed'`.
- **State Transition:** `UPDATE tickets SET status = 'closed', resolution = p_resolution`.
- **Source Event:** `customer:{customer_id}|req:{client_request_id}`.
- **Differentiation:** Resolution utilizes `NotificationType.TICKET_RESOLVED`. This generates a strictly different `logical_identity_hash` from `TICKET_CREATED`, ensuring resolution retries are deduplicated against resolutions, not creations.
- **Already Resolved:** If the ticket is already closed, the transaction returns a failure or no-op, and **no resolution notification is enqueued**.

## 8. HTTP Route & `client_request_id` Integration

Currently, HTTP ticket routes do not supply `client_request_id` and therefore intentionally fail closed. 
- **Future Integration:** `client_request_id` will be introduced as a required field in the `TicketCreate` and `TicketUpdate` Pydantic models (e.g., `client_request_id: UUID = Field(...)`).
- **Validation:** Must be a valid UUIDv4.
- **Flow:** Extracted by FastAPI and passed directly into the Python `tools.py` invocation, which forwards it to the Supabase Wrapper RPC.
- *(Note: Frontend changes to supply this UUID are deferred to the frontend owner).*

## 9. Failure Matrix

| Failure Point | Expected Durable Result |
|---|---|
| Authorization failure | No business mutation, no notification enqueue |
| Idempotency conflict | Rejected; no business mutation, no notification |
| Exact replay | Original success returned; outbox deduplicates/skips |
| Outbox uniqueness conflict | Handled safely by `ON CONFLICT DO NOTHING` |
| Mutation failure | Transaction aborts; no ticket, no notification |
| Outbox insertion failure | Transaction aborts; business mutation rolls back |
| PostgreSQL transaction rollback | Clean state; nothing made durable |
| Crash before commit | Clean state; nothing made durable |
| Crash after commit | Business + outbox durable; worker will deliver email |
| Worker / SMTP failure | Ticket remains committed; outbox retries via exponential backoff |
| Outbox retention purge followed by request replay | Impossible; outbox retention outlives or matches idempotency horizon |

## 10. Acceptance Criteria

- [ ] Exactly one ticket + one logical notification created on a successful request.
- [ ] Duplicate retry returns the original success result.
- [ ] Duplicate retry creates no second ticket.
- [ ] **Replaying an idempotent ticket request within the supported replay horizon after the notification was SENT or otherwise finalized MUST NOT generate a second logical notification.**
- [ ] Conflict is rejected.
- [ ] **Authorization failure MUST create neither business mutation nor notification.**
- [ ] Transaction rollback removes both the business mutation and outbox record.
- [ ] Crash before commit leaves neither durable.
- [ ] Concurrent identical requests create exactly one ticket and one notification.
- [ ] Customer isolation holds within the strict transactional boundary.
- [ ] Worker failure does not roll back the committed ticket.
- [ ] **No synchronous SMTP occurs inside the business transaction.**

## 11. Migration Sequence

Implementation will follow a conservative, incremental path:
1. **3.4.1:** Transactional wrapper mechanism (PostgreSQL RPC).
2. **3.4.2:** Ticket creation integration.
3. **3.4.3:** Ticket resolution integration.
4. **3.4.4:** HTTP `client_request_id` route integration.
5. **3.4.5:** Failure and concurrency tests.
6. **3.4.6:** Controlled email-path migration (Worker HTML payload validation).
