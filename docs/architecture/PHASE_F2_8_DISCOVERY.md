# Phase F.2.8 Discovery: Workflow State Persistence & Concurrency

## Goal
Define the production persistence and concurrency boundary for the typed `WorkflowState` architecture, identifying existing race conditions, sources of truth, and failure modes.

## A. Current-State Architecture & Source of Truth Distinction
- **Logical orchestration state**: Authoritatively represented by the typed `WorkflowState` model. According to F.2 architecture, ownership of this state remains with the System/Supervisor.
- **Persistence/runtime storage**: Authoritatively held in a Redis session record containing the `workflow_state` JSON block.
- **Fallback**: Defaults to an in-memory `SessionStore` at startup ONLY when Redis is unavailable by configuration/import.
- **Module Ownership**: `app/websocket/chat_handler.py` owns session fetching, saving, and serialization. `app/agents/graph.py` only defines graph topology but does not handle persistence.

## B. Exact Persistence Flow
1. A WebSocket message arrives.
2. `chat_handler.py` calls `_get_session()` to retrieve the full session blob from Redis.
3. The graph (`customer_support_graph.ainvoke(initial_state)`) executes ephemerally.
4. After execution, `chat_handler.py` calls `_get_session()` *again* to verify `mode` hasn't changed.
5. `chat_handler.py` merges the resulting `workflow_state` into the session dictionary.
6. The entire session is JSON-serialized and written back to Redis via `_save_session()`.

## C. Exact Concurrency Risks & CAS Scope Analysis
Because persistence is a non-atomic "Read -> Execute Graph -> Read Again -> Overwrite" cycle without locking or Compare-And-Swap (CAS), severe races exist.

**The Concrete Race**:
- Thread A reads session (revision N).
- Thread B reads session (revision N).
- Thread A changes `workflow_state` (e.g., transitions domain to PRODUCT).
- Thread B changes `conversation_history` or `mode` or `pending_approval`.
- Thread A saves its session blob.
- Thread B saves its session blob containing the stale `workflow_state`.

**Architectural Alternatives for CAS Scope (Decision Required)**:
A. `state_revision` protects the *entire* session record.
B. Persistence layer gets a separate generic session revision/token independent of `WorkflowState.state_revision`.
C. Another explicitly justified mechanism (e.g., field-level updates in Redis).

## D. Source-of-Truth Matrix
1. **Logical Workflow Orchestration State**: Typed `WorkflowState` (owned by System/Supervisor).
2. **Runtime Orchestration Persistence**: Redis session record containing `workflow_state`.
3. **Transactional Database/Ticket Truth**: Authoritatively owned by Supabase and existing domain tools/services.
4. **Live Session History**: `conversation_history` inside the Redis session blob.
5. **Durable Transcript**: Written to the `conversations` table in Supabase upon WebSocket termination. (Crash/reconnect implies that if a worker crashes before termination, the durable transcript may lose the active live session history).

## E. Redis Failure Matrix
- **A. Redis not configured / B. Redis package unavailable**: `SessionStore` falls back to an in-memory dictionary *at startup*. Data is lost upon process restart.
- **C. Redis configured and healthy**: Works normally. Session survives worker restarts.
- **D. Redis configured but network/API call fails during runtime**: Fails closed/errors out. The code does NOT automatically fall back to memory upon connection failure.
- **E. Redis key expired**: Expiration triggers naturally after 24h. Data is lost; the agent will start a fresh session on the next user interaction.

## F. State Revision / CAS Analysis
- The schema contains `state_revision: int = 1` inside `WorkflowState`.
- Verification confirms that `state_revision` **currently exists but is not atomically incremented** by `SessionStore` and is **not currently used for CAS**. Optimistic concurrency does NOT exist.

## G. LangGraph Checkpoint Boundary
- **Current Checkpointing**: NONE. `workflow.compile()` is invoked with no checkpointer.
- LangGraph execution state is entirely ephemeral.
- `WorkflowState` persistence is a separate application persistence concern.
- F.2.8 must NOT introduce a LangGraph Postgres checkpointer.

## H. Boundedness & Persisted Structures
- **TTL**: The current Redis TTL is explicitly `EX 86400` (24 hours). The session expires after 24 hours. In-memory fallback loses data on process restart, while Redis survives process and worker restarts.
- **Pending Approval**: `pending_approval` is an existing, mutable, persisted structure within the session blob. It presents a boundedness and concurrency concern.
- **Conversation History**: Grows infinitely within the session blob during the 24h window.

## I. Tool Idempotency
- **READ OPERATIONS**: Safe to repeat subject to consistency semantics.
- **SIDE-EFFECTING OPERATIONS**: (create/update/cancel/refund/email/etc.) 
- CAS cannot undo or deduplicate an already completed external side effect. If an external mutation succeeds → process crashes before WorkflowState persistence → retry occurs. There is a future requirement for operation-level idempotency/deduplication for external tools.

## J. Required Decision Table

| Concern | Current implementation | Actual risk | F.2.8 decision required |
|---------|------------------------|-------------|-------------------------|
| Workflow state persistence | Saved in Redis as sub-dictionary of session | Stale overwrites due to read-modify-write | How to enforce atomic updates? |
| Complete session persistence | Whole JSON blob overwritten on every turn | Independent field updates clobber each other | Should we split session keys or protect the whole blob? |
| `state_revision` | Exists in schema, never incremented | Dead code, no protection | How/when to increment it? |
| CAS scope | None | Race conditions cause amnesia | Does `state_revision` protect the whole session or just `WorkflowState`? |
| Conversation history | Appended to array in session blob | Unbounded growth | Should history be decoupled? |
| TTL | `EX 86400` in Redis | Session expires after 24h | Retain 24h TTL? |
| Redis outage | Runtime failure (no fallback) | Ephemeral state loss | Is safe failure acceptable or do we need runtime retries? |
| In-memory fallback | Used only if Redis unconfigured at startup | Complete amnesia on restart | Should we retain memory fallback for production? |
| Pending approval | Mutable dict in session blob | Concurrency clobbering | Does it need separate CAS protection? |
| Side-effect idempotency | None; blind execution | Duplicate side-effects on retry | Identify which tools need idempotency keys |
| LangGraph checkpoints | None | N/A | Confirm no checkpointer needed |

## K. Proposed Implementation Slices
1. **F.2.8.1 - Persistence API Boundary**
2. **F.2.8.2 - Revision / CAS Semantics**
3. **F.2.8.3 - Bounded Persisted Session State**
4. **F.2.8.4 - Concurrency & Failure Tests**
5. **F.2.8.5 - Side-Effect Idempotency Boundary**

## L. Explicit Non-Goals
- Do NOT implement Postgres LangGraph checkpointing.
- Do NOT introduce generic locks around everything.
- Do NOT modify `WorkflowState` schema unless a missing field is strictly required.
- Do NOT reintroduce legacy fields or Boardroom to runtime.
- Do NOT implement an idempotency system during discovery.
