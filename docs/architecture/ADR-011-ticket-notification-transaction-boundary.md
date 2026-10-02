# ADR-011: Ticket Notification Transaction Boundary

## Status
Proposed (Discovery Phase F.2.8.3 Slice 3.4)

## Context
With the introduction of the durable outbox for email notifications (Slice 3.2, 3.3) and the prior work on customer-scoped idempotency for ticket creation and updates (Slice 2B), we need a reliable way to orchestrate both operations in tandem. 

Currently, the system is exposed to the following failure vectors:
1. **Unsafe Dual-Write**: A ticket is created in PostgreSQL via a Supabase RPC, and the backend attempts to send an email synchronously via SMTP. If the server crashes after the DB commit but before the SMTP transmission completes, the email side-effect is lost forever.
2. **Idempotency Replay Danger**: If a client safely retries a ticket creation request, the idempotency layer replays the `success` result. In the current design, this triggers the synchronous email dispatch *again*, resulting in duplicate emails for a single logically deduplicated business event.
3. **No Interactive Transactions**: The backend leverages Supabase's PostgREST API for its database interactions. PostgREST does not support interactive transactions (`BEGIN ... COMMIT`). An application-level multi-step sequence (`execute_idempotent_operation` followed by `enqueue_outbox_event`) cannot be made atomically durable without introducing `psycopg2` direct connections to the API layer, which fractures the architecture.

## Decision
*(This document represents Discovery only; no immediate implementation decisions are made, but the following architecture path is proposed.)*

1. **The Atomic Unit**: Business Mutation + Idempotency Result + Outbox Enqueue + Authorization must all occur within a **single PostgreSQL transaction**.
2. **Mechanism**: We will use exactly this conceptual model:
   - ONE Supabase/PostgREST RPC request
   - one PostgreSQL transaction
   - top-level wrapper function executes inside that transaction
   - nested PostgreSQL function calls participate in the same transaction
   - transaction commits or rolls back at the RPC boundary

   The wrapper function is the orchestration boundary inside the transaction; it does not itself perform transaction control.
3. **Authorization Boundary**: The wrapper RPC must perform customer ticket authorization (`SELECT FOR UPDATE`) strictly inside the PostgreSQL transaction, immediately aborting the mutation and outbox enqueue if it fails.
4. **Execution Semantics**: 
    - **FIRST_EXECUTION**: perform business mutation, persist terminal idempotency result, enqueue notification, and commit atomically.
    - **EXACT_REPLAY**: return the stored terminal idempotency result, do NOT perform business mutation, and do NOT enqueue an outbox notification.
    - **CONFLICT**: reject request, do NOT perform business mutation, and do NOT enqueue outbox notification.
5. **Outbox Payload Resolution**: The Python layer will proactively compute the `logical_identity_hash`, `recipient_identity`, and `notification_type` using `client_request_id`. The database wrapper RPC will dynamically inject generated values (e.g., `ticket_id`) into the final Outbox JSON payload before enqueueing.
6. **Retention Guarantee**: To guarantee that exact replays never mistakenly re-enqueue notifications, the `logical_identity_hash` uniqueness must outlive the replay window. Therefore, `SENT` outbox rows will NOT be purged until their corresponding idempotency records expire.

## Consequences
- **Positive**: Zero data loss for email notifications on server crash.
- **Positive**: Strict prevention of duplicate emails upon safe idempotent retries, backed by strict retention synchronization.
- **Positive**: Zero possibility of unauthorized outbox generation.
- **Negative**: Adds a layer of indirection in the database schema (a wrapper RPC) specific to operations requiring outbox side-effects.
- **Negative**: Requires Python routes to accept `client_request_id` natively, which necessitates future frontend changes.
