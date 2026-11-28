# Open deployment decisions

## Workflow and approval recovery after cache loss

The transcript records messages and historical receipts. The live session holds the typed workflow, unresolved-attempt count, and authoritative pending approval claim. The current cache-miss recovery suppresses actionable approval cards from transcript history. A receipt can still be displayed. Workflow and approval state cannot be reconstructed safely from chat text.

- **Shared Redis session store.** Configure the existing Upstash integration to preserve live sessions across API restarts. State is still subject to the store's availability, eviction, and TTL.
- **Durable session state.** Persist a typed workflow snapshot, its revision, unresolved-attempt count, and the signed approval and claim state in a protected durable record. This requires a schema and retention decision plus a concurrency and recovery design. Replaying a transcript is insufficient to reauthorize a sensitive action.

**Recommendation.** Use the existing Redis integration if API process restarts are the only recovery requirement. Choose a durable store before promising approval recovery after cache loss or expiry. Keep hiding stale approval cards until authoritative restoration is available.

## JWT issuer and audience compatibility

The existing JWT lifetime setting defaults to 60 minutes. Previously issued tokens keep their signed expiration and remain valid under the current signature and role checks.

- **Require issuer and audience immediately.** Issued tokens without the claims would fail verification, including tokens held by clients reconnecting during rollout.
- **Stage issuer and audience enforcement.** Define claim values and which services consume them, then issue them while accepting existing tokens through a bounded compatibility window. Enforce them after the longest previously issued token lifetime has elapsed.

**Recommendation.** Stage enforcement after choosing claim values, the compatibility window, and rollout ownership. Keep login, storage, and the WebSocket handshake on the existing flow until that decision is made.

## Operating more than one API replica

HTTP rate limiting defaults to a process-local store. Customer WebSocket rate limiting uses shared Upstash when configured and falls back to a process-local budget. Model, tool, guardrail, connection, and observed model-health metrics are process-local. WebSocket connection ownership and agent delivery also live in each process.

With multiple replicas, local limits apply separately to each replica and reset on restart. A metrics request sees only the replica that answers it. Even with shared Redis session storage, an agent connected to one replica may miss a customer connected to another.

- **Keep one API replica.** This matches the current Compose and WebSocket design.
- **Scale replicas explicitly.** Choose shared rate-limit storage and what to do if it is unavailable, a metrics collector that labels each replica, and cross-replica presence and WebSocket delivery. Decide how to preserve session and approval consistency during failover.

**Recommendation.** Keep one API replica until shared limits, metrics aggregation, and cross-replica delivery have been designed and verified. No infrastructure change is included here.
