1.  **Durable Release Transaction (schema.sql)**
    *   Create a new RPC function `execute_release_transition`.
    *   It will lock the `session_escalation_state` row `FOR UPDATE`.
    *   Verify the calling `p_customer_id` against the durable `customer_id` in the row.
    *   Enforce idempotency in `idempotency_records` using `p_customer_id + p_client_request_id` for the `release_session` operation.
    *   Perform transition logic:
        *   `ACTIVE` → Update to `RELEASED`, return success.
        *   `RELEASED` → Return deterministic success without mutating.
        *   `NONE` → Return error (cannot release unescalated).
    *   Commit the terminal result to `idempotency_records`.

2.  **EscalationService Updates (service.py)**
    *   Define `ReleaseRequest` and `ReleaseResult` models.
    *   Implement `EscalationService.release_session(request: ReleaseRequest)` which generates the canonical hash and calls the `execute_release_transition` RPC.

3.  **Chat Handler Integration (chat_handler.py)**
    *   In `handle_agent_ws` (the dashboard websocket), update the `msg_type == "release"` handler.
    *   Require a caller-supplied `client_request_id`. If absent, fail closed.
    *   Retrieve the session's `customer_id`.
    *   Invoke `EscalationService.release_session()`.
    *   Only on success, mutate the Redis session to `mode = "ai"` and notify the customer.
    *   Remove the old auto-abandon logic on `WebSocketDisconnect` because durable escalation requires explicit human release. An agent disconnecting should leave the session durably `ACTIVE`.

4.  **Tests**
    *   Add real PostgreSQL integration tests in `test_escalation_service_real.py` and `test_escalation_integration_slice_353.py`.
    *   Cover all required cases: `ACTIVE` → `RELEASED`, `NONE` fails, `RELEASED` idempotency, exact replay, idempotency conflict, missing request ID fails closed, concurrent release vs escalation, and verification that stale Redis respects the `RELEASED` state (already partially verified in 3.5.3, but we'll add the release flow).
