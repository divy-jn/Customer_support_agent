# Phase F.2.8.3 Slice 3.7.1 Implementation Plan

## Overview
This slice implements the read-only reconciliation planning layer. It maps durable source records (PostgreSQL) to their expected `LogicalNotificationIdentity` set, and queries the `outbox_events` table to determine if the intent is `PRESENT`, `MISSING`, `NOT_REQUIRED`, or `NOT_RECONSTRUCTIBLE`.

## 1. Typed Reconciliation Model (`app/outbox/reconciliation_planner.py`)
- `ReconciliationStatus(Enum)`: `PRESENT`, `MISSING`, `NOT_REQUIRED`, `NOT_RECONSTRUCTIBLE`.
- `PlannedNotification(BaseModel)`:
    - `source_event_id: str`
    - `notification_type: NotificationType`
    - `recipient_identity: str`
    - `recipient_address: str`
    - `logical_identity_hash: str`
    - `status: ReconciliationStatus`
- `ReconciliationPlanner` class with a synchronous or asynchronous database connection.

## 2. Source-to-Notification Mappings
### A. Escalation Events (`escalation_events`)
- **Query:** Select row by `escalation_event_id`. Join `customers` for email.
- **E1 (Team):** Always required.
    - Identity: `from_escalation(escalation_event_id)`
    - Recipient: `support_team()`
    - Expected hash: SHA-256 of canonical tuple.
- **E2 (Customer):** Required if `customer_notification_required = true`.
    - Identity: `from_escalation(escalation_event_id)`
    - Recipient: `customer(customer_id)`
    - Expected hash: SHA-256 of canonical tuple.
    - Status: `NOT_REQUIRED` if false.

### B. Ticket Notifications (`idempotency_records`)
- **Query:** We query `idempotency_records` using the exact `client_request_id` and `customer_id`. The planner does NOT scan a customer's broad history to avoid manufacturing unrelated notifications. We join `customers` via `idempotency_records.customer_id` to get the email.
- **TICKET_CREATED:**
    - If `operation_type = 'create_ticket'`, this durably proves a creation occurred.
    - Identity: `from_ticket_creation` -> `customer:{customer_id}|req:{client_request_id}`
    - Recipient: `customer(customer_id)`
    - Expected hash: SHA-256 of canonical tuple.
- **TICKET_RESOLVED:**
    - The `idempotency_records` for `update_ticket` does NOT durably prove the ticket was resolved, as the terminal result only contains a generic success message and the payload is not preserved.
    - Since we cannot anchor a resolution to a specific `client_request_id` from the existing RPC contract without making unsafe assumptions, missing `TICKET_RESOLVED` notifications are **NOT_RECONSTRUCTIBLE**. The planner must NEVER convert a generic update into a resolution.

### C. Custom Email (`idempotency_records`)
- **Query:** Look for `operation_type = 'custom_email'`.
- **Status:** Always `NOT_RECONSTRUCTIBLE` if missing. The `idempotency_records` row proves the operation existed, but does NOT provide sufficient subject/body/recipient content to reconstruct a missing email. The outbox payload is the only durable storage for LLM-generated email content.

## 3. Outbox Lookup Strategy
For a generated `logical_identity_hash`, query `outbox_events.logical_identity_hash`.
- If row exists (any state: `SENT`, `PROCESSING`, `RETRYABLE`, `FAILED`): `status = PRESENT`.
- If row does not exist: `status = MISSING`.

### FAILED Ownership
A `FAILED` terminal outbox row is considered `PRESENT` to reconciliation. The reconciler planner must NEVER silently create a replacement for a `FAILED` notification. Ownership of recovery from a terminal `FAILED` state belongs entirely to manual operator action or a future dedicated dead-letter recovery mechanism, NOT the missing-row reconciler.

## 4. Tests (`tests/test_reconciliation_planner.py`)
- Use real PostgreSQL connections.
- Insert mock records into `escalation_events`, `tickets`, `idempotency_records`, `customers`.
- Test E1/E2 creation, including `customer_notification_required` variations.
- Test missing rows return `MISSING`.
- Test existing rows (in various states) return `PRESENT`.
- Test Custom Email returns `NOT_RECONSTRUCTIBLE`.
- Test determinism and verify no session-based inference occurs.
