# F.2.8.3 Slice 2B Validation & Hardening Report

## 1. Final Exact Git File Set
**Production Code Changed**:
- `backend/app/agents/graph.py` (propagate `client_request_id` from state to tools)
- `backend/app/tools.py` (update 4 target tools to remove UUID fallback, pass explicitly to RPC, validate closed)
- `backend/app/websocket/chat_handler.py` (extract `client_request_id` in WS flow)

**Production Schema**:
- `backend/scripts/schema.sql` (appended `idempotency_records` table and `execute_idempotent_operation` RPC with strict security invoker grants)

**Reusable Regression Tests**:
- `backend/tests/test_idempotency_real.py` (concurrent real DB validations)
- `backend/tests/test_idempotency_additional.py` (negative isolation, crash equivalency, and missing ID validations)

**Deleted Temporary Artifacts**:
- `backend/scripts/schema_updates_scratch.sql`
- `backend/apply_schema.py`
- `backend/setup_test_db.py`
- `backend/tests/test_idempotency_rpc.py` (deleted obsolete mock test)

## 2. RPC Fingerprint Integrity
The authoritative fingerprinting boundary is placed in the **Python Tool Layer**. 
Python canonicalizes the JSON payload via `json.dumps(payload, sort_keys=True)` and hashes it using SHA-256. 
*Reasoning*: Postgres cannot natively compute a SHA-256 hash without the `pgcrypto` extension, and casting a `jsonb` to `text` in Postgres may yield different whitespace/ordering semantics than Python's `sort_keys=True`. Thus, the RPC explicitly trusts `p_payload_hash` supplied by the trusted backend layer. The caller is a trusted backend service authenticated via `service_role`.

## 3. RPC Operation Allowlist
The RPC executes logic using explicit, static `IF p_operation_type = 'create_ticket' ELSIF ...` blocks. 
Only the four intended operations are reachable:
1. `create_ticket`
2. `update_ticket`
3. `cancel_order`
4. `process_refund`

Any other `p_operation_type` defaults to the `ELSE` block, which returns:
`{"error": "Unknown operation type: <type>"}`.
There is **no dynamic SQL** (`EXECUTE`), and the LLM cannot dispatch arbitrary functions or table insertions.

## 4. Security / Authentication Verification
- **Durable Identity**: `(authenticated_customer_id, client_request_id)` is strictly enforced in the table's composite primary key.
- **Backend Derivation**: The LLM tools receive `customer_id` strictly from the Graph state, which is populated by `chat_handler.py` deriving it from `Depends(verify_customer_ws_token)`. The caller-supplied JSON cannot override this identity.
- **RPC Permissions**: The RPC is constrained via `REVOKE EXECUTE ON FUNCTION ... FROM public, anon, authenticated;` and explicitly `GRANT EXECUTE ... TO service_role`.
- **Target Ownership**: Target resources (like `order_id` or `ticket_id`) are independently queried and verified (`v_order_customer_id != p_customer_id`) before mutation inside the RPC.

## 5. All Four Mutation Test Results
Tested explicitly against the local Postgres database (`test_idempotency_real.py`, `test_idempotency_additional.py`):

**CREATE (create_ticket)**:
- First request: PASSED (ticket created, terminal success cached)
- Exact retry: PASSED (returns cached success, no duplicate ticket)
- Concurrent exact retry: PASSED (3 threads race, exactly 1 ticket created)
- Conflicting retry: PASSED (different payload returns `IdempotencyConflict`)
- New request with identical business payload: PASSED (creates a new, second ticket)

**UPDATE/APPEND (update_ticket)**:
- First append: PASSED (text appended)
- Exact retry: PASSED (returns cached success, does NOT duplicate the append text)
- Concurrent retry: PASSED (exactly 1 append executes)
- New request with identical text: PASSED (legitimately appends the same string a second time)
- Conflicting payload: PASSED (`IdempotencyConflict` returned)

**CANCEL (cancel_order)**:
- First request: PASSED (status changed to cancelled)
- Exact retry: PASSED (cached success)
- Concurrent retry: PASSED (exactly 1 update occurs)
- Conflicting target/action/payload: PASSED (rejected natively or via conflict)

**REFUND (process_refund)**:
- First request: PASSED (status changed to refunded)
- Exact retry: PASSED (cached success)
- Concurrent retry: PASSED (exactly 1 update occurs)
- Conflicting operation: PASSED (`IdempotencyConflict`)
- Genuine failure: PASSED (attempting to refund an `active` order correctly returns business error)

## 6. Crash-Equivalent Semantics
**Test Validated**: `test_real_crash_equivalent`
**Sequence Proved**:
1. DB transaction commits (mutation + idempotency record established).
2. Python application "crashes" before the WebSocket response or CAS session save occurs.
3. User reconnects, agent resends the *same* `client_request_id`.
4. RPC natively hits `IF FOUND` on the `idempotency_records` table, blocking execution and immediately returning the cached `terminal_result`.
5. **Business mutation count remains exactly one**.
*(Note: This proves DB side-effect recovery/idempotency. It does NOT claim to prove durable LangGraph checkpointing)*.

## 7. Distinct-Request Ticket Race
**Status: OPEN**
This issue remains explicitly unresolved in this request-level idempotency slice.
If R1 and R2 are *distinct* requests (different UUIDs), and both concurrently evaluate `find_matching_ticket()`, they may both see no ticket and both proceed to call `create_ticket`.
The RPC boundary isolates *single requests*, but it does not own the cross-operation search context to deduplicate business-level semantic intent. This is classified as a remaining business-level deduplication/concurrency issue to be addressed in a future phase.

## 8. Frontend Release Gate
**Status: PENDING**
The current frontend does not supply `client_request_id`. 
The backend explicitly enforces a fail-closed behavior:
```python
if not client_request_id:
    return json.dumps({"error": "client_request_id is required for mutating operations"})
```
No UUID fallback is implemented. Frontend integration is a hard prerequisite. **End-to-end production readiness is NOT claimed.**

## 9. Remote Supabase Release Gate
**Status: PENDING**
Local Postgres validation is fully complete.
Remote Supabase migration is blocked/pending (as local Python could not connect to Supabase DB).
- Schema deployment pending.
- RPC deployment pending.
- Remote permission/security validation pending.
**Remote production deployment is NOT complete.**

## 10. Exact Test Totals
- **Local Integration Tests** (Real Postgres DB - `test_idempotency_real.py`, `test_idempotency_additional.py`): 10 passed.
- **Targeted Repository Tests** (`test_database_tools.py`): 19 passed.
*(Note: We did not run the full repository suite since UI/Frontend tests were out of scope, but the entire backend DB/tool suite was successfully executed)*.

## 11. Graphify Result
```
agy graphify update .
agy : The term 'agy' is not recognized as the name of a cmdlet...
```
Graphify validation failed as the executable is unavailable in this environment.

## 12. Final Git Status/Diff Summary
- **No secrets or `.env` changes.**
- **No temporary/scratch files remaining.**
- **Exact final file set**: 
  - `backend/app/agents/graph.py` (Modified)
  - `backend/app/tools.py` (Modified)
  - `backend/app/websocket/chat_handler.py` (Modified)
  - `backend/scripts/schema.sql` (Modified)
  - `backend/tests/test_idempotency_real.py` (New)
  - `backend/tests/test_idempotency_additional.py` (New)
