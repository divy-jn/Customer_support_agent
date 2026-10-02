# ADR-008: Workflow State Persistence & Concurrency Boundary (Discovery)

## Status
Proposed (Discovery Phase)

## Context
With the completion of the F.2 Typed WorkflowState modernization, the system's runtime persistence layer remains reliant on a legacy "read-execute-overwrite" mechanism inside `chat_handler.py`.

This discovery phase identified several architectural conditions and flaws:

1. **Source of Truth Distribution**: 
    - Logical orchestration state is the typed `WorkflowState` (owned by System/Supervisor).
    - Runtime storage is the Redis session record containing `workflow_state`.
    - Supabase and domain tools own transactional database/ticket truth.
    - Live session history lives in the Redis session blob, while the durable transcript is written to Supabase only on WebSocket termination (risking loss of live history on mid-session crashes).
2. **Lack of Concurrency Control & CAS Scope**: The entire session blob (containing `workflow_state`, `conversation_history`, `mode`, and `pending_approval`) is overwritten at the end of graph execution. If thread A modifies `workflow_state` and thread B concurrently modifies `conversation_history`, one will silently overwrite the other. 
3. **Dead `state_revision`**: `state_revision` exists in `WorkflowState` but is not atomically incremented by `SessionStore` and is not used for Compare-And-Swap (CAS).
4. **Tool Idempotency vs CAS**: CAS protects the orchestration state from internal clobbering but cannot undo or deduplicate an already completed external side effect (e.g., if a tool succeeds but the worker crashes before state persistence). Operation-level idempotency is a required future capability for external side-effecting operations (create/update/refund/email/etc.).
5. **Redis Failure Matrix**: `SessionStore` falls back to memory ONLY at startup if Redis is unconfigured or unavailable to import. If Redis is configured but fails during runtime (network error), the connection fails closed (it does not silently fall back to memory).
6. **LangGraph Checkpoints**: `workflow.compile()` is called without a checkpointer. LangGraph execution state is intentionally ephemeral and is a separate application persistence concern from `WorkflowState`.

## Decision
*(This document represents Discovery only; no immediate implementation decisions are made, but the following architecture decisions are required before implementation.)*

1. **CAS Scope Decision Required**: We must decide if `WorkflowState.state_revision` protects the *entire* session record (including history and approvals), or if the persistence layer requires a separate session-level token.
2. **Retain Separation of State vs. Checkpoints**: LangGraph execution state remains ephemeral. We will NOT introduce a LangGraph Postgres checkpointer. WorkflowState persistence remains an explicit orchestration concern.
3. **Address Side-Effect Idempotency**: Acknowledge the future requirement for operation-level idempotency for external mutating operations. External mutations that succeed before a persistence crash will be retried on next turn, which requires idempotency on the tool side.

## Proposed Implementation Slices
* F.2.8.1: Persistence API boundary.
* F.2.8.2: Revision/CAS semantics.
* F.2.8.3: Bounded persisted session state (handling history/pending approval).
* F.2.8.4: Concurrency tests and Failure/recovery tests.
* F.2.8.5: Side-effect/idempotency boundary definition.

## Consequences
- Formalizing the CAS boundary will prevent silent amnesia bugs during concurrent requests.
- Tool boundaries will eventually need idempotency keys to safely survive retry loops when Redis writes fail after a side-effect succeeds.
