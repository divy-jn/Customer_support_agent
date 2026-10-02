# Customer Support Agent — Submission Checklist

## Phase 1: Security Foundation
- [x] JWT Authentication implemented across all endpoints.
- [x] WebSocket connections require JWT validation.
- [x] Default dev-login properly identifies as a specific customer.
- [x] Obsolete/hardcoded authentication patterns removed.

## Phase 1A: Resource Ownership & Security Hardening
- [x] Backend automatically extracts `customer_id` from the validated JWT.
- [x] `customer_id` from client payloads/queries is STRICTLY IGNORED.
- [x] `_execute_tool` automatically injects the authenticated `customer_id` into all DB operations.
- [x] Database queries enforce ownership (Customers can only view their own orders/tickets).

## Phase 2: Core Correctness
- [x] Supabase schema strictly followed (Orders vs. Tickets).
- [x] LangGraph state management and transitions execute correctly.
- [x] High-Risk Action approval workflow prevents automatic tool execution (cancel order, refund).
- [x] Tool execution correctly awaits async tasks (`asyncio.to_thread`).

## Phase 3: Human Support UX
- [x] Agent dashboard clearly shows escalations.
- [x] Bi-directional Chat works flawlessly in Human mode.
- [x] Takeover and Release events broadcast correctly to all participants.
- [x] Escalations trigger proper alerts and notification emails (simulated).
- [x] UI handles Reconnecting and Offline gracefully.

## Phase 4: AI Polish
- [x] Intent routing robustly distinguishes Support vs Action queries.
- [x] RAG correctly handles out-of-domain questions with fallback mechanisms.
- [x] No LLM hallucinations when lacking context.
- [x] Complete, readable code documentation and clear architectural diagrams.

## Phase 5: Audit & Validation
- [x] All obsolete debug logs and temporary files removed.
- [x] Tests cover critical security boundaries (Identity spoofing fails).
- [x] Flaky tests isolated or fixed.
- [x] Environment files (.env.example) correctly reflect usage.
- [x] Ready for SDE Interview Demonstration!
