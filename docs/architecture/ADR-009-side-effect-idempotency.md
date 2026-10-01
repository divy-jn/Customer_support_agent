# ADR 009: Side-Effect Idempotency

## Status
Proposed

## Context
In Phase F.2.8.2, we established a strict Compare-And-Swap (CAS) boundary around internal `WorkflowState`. While this guarantees safe orchestration state concurrency, it does not natively provide idempotency for external mutations. 

Our agents execute tools that mutate external systems (Supabase DB, SMTP servers). Re-executions, reconnects, or retries (due to CAS conflicts or crashes) can resubmit the same logical side effect. The current architecture does not have durable LangGraph checkpoints; thus, side-effect safety must not depend on LangGraph execution state. 

Without explicit operation-level idempotency, re-execution leads to duplicate ticket creation, double description appends, duplicate email deliveries, or confused LLM routing when terminal states (like `cancelled`) throw errors upon retry.

## Decision

1. **Separation of Architectural Boundaries**: 
   - **Session CAS**: Handles orchestration-state concurrency.
   - **Operation Identity**: Handles logical side-effect identity.
   - **Durable Idempotency Mechanism**: Handles duplicate business-operation prevention.
   - **Outbox/Provider Reconciliation**: Handles external side-effect delivery semantics.

2. **Operation Identity Criteria**:
   We distinguish the Client Request from the Logical Operation.
   Because the architecture guarantees at most one business mutation per client request, the durable idempotency identity answers "Is this the same submitted request?", not "What is the operation?".
   The durable identity is:
   `durable_identity = (authenticated_customer_id, client_request_id)`
   The actual operation details (operation_type, canonical_target, payload_hash) are established and stored by the FIRST execution. This securely links the client's explicit intent to the resulting operation.
   *Note: The backend must securely derive the `customer_id` from the authenticated session context (JWT), and MUST NOT blindly trust a caller-supplied `customer_id` payload.*

3. **Storage Boundary for Business Mutations**:
   For DB-backed business mutations (ticket creation, updates, order cancellation, refunds), the durable idempotency boundary must execute in the **same relational transaction** as the business mutation itself (Supabase).
   Because the direct Supabase REST API (`supabase-py`) does not support multi-statement transactions, the chosen DB-backed implementation should eventually use a **Postgres Database Function (RPC)** to atomically commit the `idempotency_records` insert and the business mutation.
   Redis is insufficient as the authoritative business idempotency record due to TTL eviction and lack of atomic commit capabilities with Postgres.

4. **Idempotent Mutator Semantics**:
   - **Exact Match**: A repeated execution of the SAME request ID with the SAME operation fingerprint must return the stored terminal result.
   - **Payload/Target/Action Conflict**: If a retry presents the same `client_request_id` but the LLM produced a *different* operation fingerprint, the system must NOT invent a new independent mutation. It must throw an `IdempotencyConflictException`, aborting the new mutation and forcing the LLM to reconcile.

5. **External Provider / Email Semantics**:
   DB transactions + SMTP/Gmail cannot provide atomic exactly-once delivery. An outbox pattern will be used to provide durable at-least-once dispatch. However, provider-level idempotency or reliable reconciliation is required for stronger effectively-once semantics. Without such provider support, duplicate email delivery remains possible in the crash-after-send window. We accept this limitation while preventing at-least-once dispatch loss.

## Consequences
- **Positive**: Establishes strict guarantees for effectively-once business outcomes for database operations.
- **Positive**: Provides the LLM with safe, predictable responses during retry loops.
- **Negative**: Increases schema complexity in Supabase to track operation identities within transactions.
- **Negative**: Explicitly acknowledges that exactly-once email delivery is impossible without external provider support.
