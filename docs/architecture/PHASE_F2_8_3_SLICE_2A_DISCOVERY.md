# F.2.8.3 SLICE 2A DISCOVERY: STABLE REQUEST & OPERATION IDENTITY

## A. Request Semantics & Multi-Operation Constraints
**What constitutes a request?** One WebSocket message is exactly one client request. 
**Multi-operation constraints:** In the current architecture (via `db_plan_node` returning a single `DBPlannerOutput` action, or `TicketLifecycleService.process_issue()` being called at most once), one client request legitimately produces at most **one** business mutation. 
It is currently impossible for a single request to legitimately produce multiple side effects. Supporting multiple valid mutations in one request is a FUTURE capability requiring a different operation sequencing/identity model and is explicitly out of scope for this architecture.

## B. Operation Identity vs Request Identity
We define a strict conceptual separation:
- **Request Identity**: "Is this the same submitted request?"
- **Operation Identity**: "What operation did that request establish?"

Given the invariant of *one mutation per request*, the durable idempotency identity must be the request identity bound to the customer:
```
durable_idempotency_identity = (customer_id, client_request_id)
```

The underlying durable record will store the exact fingerprint of the operation established by the FIRST execution:
- `customer_id`
- `client_request_id`
- `operation_type` (e.g., `cancel_order`)
- `canonical_target` (e.g., `123`)
- `payload_hash`
- `terminal_result`

## C. Same Request / Different LLM Plan Behavior
If the LLM plan changes upon retry of the same `client_request_id` (e.g., Attempt 1: `cancel_order(123)`, Attempt 2: `cancel_order(456)`):
The system detects that `client_request_id` already established an operation. It verifies the stored fingerprint against the new LLM plan. Because the operation type, target, or payload differs, this is a **conflicting retry**.
- **Behavior**: The system must NOT silently invent a new independent business operation for `456`. It must throw an `IdempotencyConflictException`, abort the new mutation, and return the previous terminal result to force the LLM to reconcile.

## D. Decision Table

| Scenario | Request Identity (`client_request_id`) | Operation Fingerprint | Expected Behavior |
| --- | --- | --- | --- |
| **1. Exact retry (auto-reconnect or "Retry" button)** | Same as Attempt 1 | Same | Return stored terminal result. No second mutation. |
| **2. New identical user message (manual re-type)** | New UUID | Same | Execute as a brand new independent mutation. |
| **3. Concurrent exact retry (race condition)** | Same as Attempt 1 | Same | DB locks/blocks. Loser waits and returns winner's terminal result. |
| **4. Same request + different target** | Same | Different | `IdempotencyConflict`. DO NOT execute new mutation. |
| **5. Same request + different action** | Same | Different | `IdempotencyConflict`. DO NOT execute new mutation. |
| **6. Same request + different payload** | Same | Different | `IdempotencyConflict`. DO NOT execute new mutation. |
| **7. Different customer reusing request ID** | Same | N/A | Fails cross-customer security check. Reject immediately. |

## E. Database Concurrency & RPC Behavior
Relying merely on a PK constraint is insufficient. The future Postgres RPC must guarantee:
1. Only one concurrent caller establishes the operation.
2. The losing caller CANNOT execute the business mutation.
3. The losing caller safely waits and obtains the winner's committed terminal result.
4. A transaction rollback (e.g., business logic failure) rolls back the idempotency record, preventing a false completed result.
5. The same request ID with a different fingerprint cannot mutate anything.

## F. Frontend Ownership & Contract
The frontend (Madan's domain) must be updated to implement this contract. No backend logic can safely simulate this.
**WebSocket Contract:**
```json
{
  "client_request_id": "uuid-v4",
  "message": "User text here",
  "customer_id": 123
}
```
**Semantics:**
- UUID generated exactly once when the user submits a message.
- Reused ONLY if the frontend retries that exact request.
- A manual user submission gets a new UUID.

## G. Propagation Path
1. **Frontend**: Sends `client_request_id` via WebSocket.
2. **chat_handler.py**: Extracts and places `client_request_id` into the execution context / request context. (It must NOT be added to the durable `WorkflowState` unless future graph contracts explicitly require durable request tracing).
3. **_execute_tool (graph.py)**: Extracts `client_request_id` from the context and passes it alongside the generated operation fingerprint to `tools.py`.
4. **tools.py**: Passes `client_request_id` and fingerprint to a **Postgres Database Function (RPC)**.

## H. Security Binding
The durable identity is strictly **scoped by customer**:
`durable_identity = (authenticated_customer_id, client_request_id)`

**Semantics:**
- **Same customer + same request ID:** Same submitted request (returns cached result).
- **Same customer + different request ID:** New distinct request.
- **Different customer + same request ID:** Different scoped identity, with complete isolation. Global `client_request_id` uniqueness is NOT part of the contract. The system allows `(customer 101, R1)` and `(customer 202, R1)` to coexist as separate valid records. No customer can read, replay, or mutate another customer's idempotency record.

**Authorization & Context Requirements:**
- The system must derive the `customer_id` strictly from the authenticated JWT session context. It MUST NOT trust a caller-supplied `customer_id` payload.
- `session_id` is explicitly excluded from the request identity. This ensures an authenticated customer can drop their connection, reconnect under a new session context, and still safely retry their in-flight request.
