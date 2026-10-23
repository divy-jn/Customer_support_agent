"""
WebSocket Chat Handler — processes incoming customer messages
through the LangGraph pipeline and streams responses back.
"""

import uuid
import json
import asyncio
import logging
import time
import re
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect, status

from app.config import settings
from app.websocket.connection import manager
from app.agents.graph import customer_support_graph, AgentState
from app.agents.domain_graph import domain_support_graph
from app.agents.approval_service import (
    execute_approved_action,
    validate_approval,
    claim_pending_approval_in_session,
    ApprovalClaimOutcome,
)
from app.models import ApprovalStatus, PendingApproval
from app.email_service import send_escalation_email
from app.tools import lookup_customer
from app.guardrails import validate_input, validate_output, get_rejection_message
from app.middleware.tracking import emit_workflow_turn

from app.persistence.session import session_store

logger = logging.getLogger(__name__)


async def _execute_cas(session_id: str, loader_fn, mutator_fn, retries: int) -> tuple[dict, bool]:
    from app.persistence.session import SaveResult
    for attempt in range(retries):
        session = await loader_fn()
        expected_revision = session.get("_session_revision", 1)
        
        should_save = mutator_fn(session)
        if not should_save:
            return session, False
            
        result = await session_store.save_session_conditional(session_id, session, expected_revision)
        
        if result == SaveResult.SUCCESS:
            return session, True
        elif result == SaveResult.MISSING:
            logger.error("Session missing during CAS mutation", extra={"session_id": session_id})
            raise RuntimeError(f"Session {session_id} missing")
            
        logger.warning(
            f"CAS conflict on session {session_id}, attempt {attempt+1}/{retries}", 
            extra={
                "session_id": session_id,
                "expected_revision": expected_revision,
                "retry_count": attempt + 1
            }
        )
                      
    logger.error("CAS conflict exhausted retries or retry disabled", extra={"session_id": session_id, "retry_count": retries})
    raise RuntimeError("Concurrency conflict: stale session data could not be saved safely.")


async def _get_session(session_id: str, authenticated_customer_id: int | None = None) -> dict:
    """Retrieve an existing session or initialize a new one with the authenticated customer_id."""
    session = await session_store.get_session(session_id)
    if not session:
        # Initialize new session bounded to the authenticated customer
        new_session = {
            "session_id": session_id,
            "customer_id": authenticated_customer_id,
            "_session_revision": 1,
            "mode": "ai",
            "conversation_history": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pending_approval": None,
            "workflow_state": {
                "active_domain": None,
                "state_revision": 1,
                "session_id": session_id,
                "customer_id": authenticated_customer_id,
                "turn_count": 0
            }
        }
        from app.persistence.session import SaveResult
        res = await session_store.create_session_if_absent(session_id, new_session)
        if res == SaveResult.SUCCESS:
            return new_session
        
        # If it failed to create, it was created concurrently. Fall through to load it.
        session = await session_store.get_session(session_id)
        if not session:
            raise RuntimeError("Session creation race but session is missing")

    # Enforce session ownership
    if authenticated_customer_id is not None:
        if session.get("customer_id") is not None and session.get("customer_id") != authenticated_customer_id:
            raise ValueError(f"Session {session_id} belongs to a different customer.")
        if session.get("customer_id") is None:
            async def _load_raw():
                s = await session_store.get_session(session_id)
                if not s:
                    raise RuntimeError(f"Session {session_id} missing")
                # OWNERSHIP REVALIDATION AFTER RELOAD
                if s.get("customer_id") is not None and s.get("customer_id") != authenticated_customer_id:
                    raise ValueError(f"Session {session_id} belongs to a different customer.")
                return s
                
            def _init_customer(s):
                if s.get("customer_id") is not None:
                    return False
                s["customer_id"] = authenticated_customer_id
                return True
                
            session, _ = await _execute_cas(session_id, _load_raw, _init_customer, 3)
            
    return session



async def _mutate_session(session_id: str, mutator_fn, allow_retry: bool = True, customer_id: int | None = None) -> tuple[dict, bool]:
    MAX_CAS_RETRIES = 3
    retries = MAX_CAS_RETRIES if allow_retry else 1
    
    async def _load_fn():
        return await _get_session(session_id, customer_id)
        
    return await _execute_cas(session_id, _load_fn, mutator_fn, retries)


async def get_all_active_sessions() -> list:
    """Return sessions with a currently connected customer WebSocket."""
    sessions = await session_store.get_all_active_sessions()
    connected_session_ids = set(manager.active_connections.keys())
    return [
        session for session in sessions
        if session.get("session_id") in connected_session_ids
    ]


async def handle_customer_ws(websocket: WebSocket, session_id: str | None = None, authenticated_customer_id: int | None = None):
    """
    Main handler for customer WebSocket connections.
    Receives messages, processes them through LangGraph, and sends responses.
    """
    caller_supplied_session_id = session_id is not None
    if not session_id:
        session_id = str(uuid.uuid4())

    # A caller-supplied ID may refer to a persisted conversation that is not in
    # local/Redis session state. Verify ownership before connecting; if the
    # authoritative lookup is unavailable, fail closed rather than recreating
    # the session under the caller's identity. Fresh server-generated IDs do
    # not need this round-trip.
    if authenticated_customer_id is not None and caller_supplied_session_id:
        # A cached session that already has an owner is itself an authoritative
        # session-store record. Reject a mismatch immediately, even if the DB is down.
        cached_session = await session_store.get_session(session_id)
        cached_owner = cached_session.get("customer_id") if cached_session else None
        if cached_owner is not None and cached_owner != authenticated_customer_id:
            await websocket.close(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Session belongs to a different customer.",
            )
            return

        try:
            from app.tools import supabase
            res = await asyncio.to_thread(
                supabase.table("conversations")
                .select("customer_id")
                .eq("session_id", session_id)
                .execute
            )
            persisted_rows = res.data or []
            if persisted_rows:
                persisted_owner = persisted_rows[0].get("customer_id")
                if persisted_owner is not None and persisted_owner != authenticated_customer_id:
                    await websocket.close(
                        code=status.WS_1008_POLICY_VIOLATION,
                        reason="Session belongs to a different customer.",
                    )
                    return
                if persisted_owner is None and cached_owner is None:
                    # Never let a customer claim a persisted conversation whose
                    # identity cannot be established, even if the row is legacy.
                    await websocket.close(
                        code=status.WS_1008_POLICY_VIOLATION,
                        reason="Session ownership could not be verified.",
                    )
                    return
            elif cached_session is not None and cached_owner is None:
                # Do not bind ownerless cached state that may include another
                # user's history merely because the persistent index has no row.
                await websocket.close(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason="Session ownership could not be verified.",
                )
                return
        except Exception:
            if cached_owner == authenticated_customer_id:
                logger.warning(
                    "Persistent session ownership lookup unavailable; relying on owner-bound session state",
                    extra={"session_id": session_id},
                )
            else:
                logger.exception(
                    "Persistent session ownership could not be verified; rejecting WebSocket",
                    extra={"session_id": session_id},
                )
                await websocket.close(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason="Session ownership could not be verified.",
                )
                return

    await manager.connect_customer(websocket, session_id)

    try:
        session = await _get_session(session_id, authenticated_customer_id)
    except ValueError as e:
        # Reject connection if ownership fails
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(e))
        return

    customer_id = authenticated_customer_id
    history = list(session.get("conversation_history", []))
    pending_app = session.get("pending_approval")
    if pending_app and pending_app.get("status") == "pending":
        has_approval = any(msg.get("approval") for msg in history if isinstance(msg, dict))
        if not has_approval:
            history.append({
                "role": "agent",
                "content": pending_app.get("message") or "Confirmation required for your request.",
                "timestamp": pending_app.get("created_at") or datetime.now(timezone.utc).isoformat(),
                "approval": {
                    "approval_id": pending_app.get("approval_id"),
                    "action": pending_app.get("action"),
                    "params": pending_app.get("params"),
                    "status": "pending",
                    "message": pending_app.get("message"),
                }
            })

    # Send welcome message with session ID and history
    await manager.send_personal_message({
        "type": "system",
        "session_id": session_id,
        "mode": session.get("mode", "ai"),
        "message": "Hello! Welcome to our customer support. How can I help you today?",
        "agent_name": "Adi",
        "history": history,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, session_id)

    rate_limit_window = 60
    rate_limit_max = settings.ws_message_rate_limit

    try:
        while True:
            # Receive message from customer
            try:
                raw = await websocket.receive_text()
            except RuntimeError:
                logger.warning(f"WebSocket {session_id} receive_text failed (likely disconnected already).")
                break

            # Limit by authenticated customer identity, not socket/session ID, so
            # reconnecting or opening another tab cannot reset the message budget.
            from app.websocket.rate_limiter import consume_customer_message
            rate_limit_identity = (
                f"customer:{customer_id}" if customer_id is not None else f"session:{session_id}"
            )
            allowed = await consume_customer_message(
                rate_limit_identity,
                limit=rate_limit_max,
                window_seconds=rate_limit_window,
            )
            if not allowed:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": "Rate limit exceeded. Please wait a moment before sending more messages."
                }))
                continue

            try:
                data = json.loads(raw)
                message = data.get("message", "").strip()
                client_request_id = data.get("client_request_id")
                # Ignore customer_id in JSON payload, use authenticated_customer_id
            except json.JSONDecodeError:
                message = raw.strip()
                client_request_id = None
                
            if not client_request_id:
                await manager.send_personal_message({
                    "type": "error",
                    "message": "Missing client_request_id in payload."
                }, session_id)
                continue

            customer_id = authenticated_customer_id

            if not message:
                continue

            if message.startswith("[System]"):
                # Ignore system messages sent incorrectly from older frontends
                continue

            # ── Input Guardrails ──
            guard_result = validate_input(message)
            if not guard_result.passed:
                rejection = get_rejection_message(guard_result)
                logger.warning(
                    f"Input blocked for session {session_id}",
                    extra={"violations": guard_result.violations, "risk_score": guard_result.risk_score},
                )
                await manager.send_personal_message({
                    "type": "agent_response",
                    "message": rejection,
                    "agent_name": "Adi",
                    "escalated": False,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, session_id)
                continue

            # Use sanitized message (PII redacted for logging)
            sanitized_message = guard_result.sanitized_text

            def _append_customer(s):
                s["conversation_history"].append({
                    "role": "customer",
                    "content": message,
                    "sanitized": sanitized_message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "pii_detected": guard_result.pii_detected,
                })
                return True
            session, _ = await _mutate_session(session_id, _append_customer, allow_retry=True, customer_id=customer_id)

            # Broadcast to monitoring agents
            await manager.broadcast_to_agents({
                "type": "customer_message",
                "session_id": session_id,
                "customer_id": customer_id,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, session_id)

            # ── 1. Durable state is authoritative BEFORE routing ──
            try:
                from app.persistence.escalation import ensure_durable_escalation_state
                from app.models import EscalationLifecycleStatus
                
                if customer_id is None:
                    durable_status = EscalationLifecycleStatus.NONE
                else:
                    durable_status = await asyncio.to_thread(
                        ensure_durable_escalation_state,
                        session_id,
                        customer_id
                    )
                
                expected_mode = "human" if durable_status == EscalationLifecycleStatus.ACTIVE else "ai"
                
                if session.get("mode") != expected_mode:
                    logger.warning(
                        f"Stale Redis mode detected for {session_id}. "
                        f"Postgres: {durable_status.value}, Redis: {session.get('mode')}. "
                        f"Repairing Redis to {expected_mode}."
                    )
                    
                    def _repair_mode(s):
                        if s.get("mode") != expected_mode:
                            s["mode"] = expected_mode
                            return True
                        return False
                        
                    session, _ = await _mutate_session(session_id, _repair_mode, allow_retry=True, customer_id=customer_id)
            except Exception as e:
                logger.warning(
                    f"Could not verify durable escalation state for session {session_id} ({e}); "
                    "falling back to current session mode."
                )

            # If human agent has taken over, do NOT run ANY AI logic
            if session.get("mode") == "human":
                continue

            # Check if this is an approval response for a pending action
            pending = session.get("pending_approval")
            clean_msg = message.lower().strip()
            is_rejection = (
                clean_msg in ("no", "nope", "nah", "cancel", "don't", "dont", "stop", "never mind")
                or clean_msg.startswith("no,")
                or clean_msg.startswith("no ")
                or "do not" in clean_msg
                or "don't" in clean_msg
                or "dont" in clean_msg
                or "cancel this" in clean_msg
            )
            is_confirmation = (
                not is_rejection and (
                    clean_msg in ("yes", "yeah", "yep", "sure", "ok", "okay", "go ahead", "proceed", "confirm", "do it")
                    or clean_msg.startswith("yes,")
                    or clean_msg.startswith("yes ")
                    or clean_msg.startswith("confirm ")
                    or clean_msg.startswith("proceed ")
                )
            )

            pending_action = pending.get("action") if pending else None
            pending_domain = "order" if pending_action == "cancel_order" else "payment" if pending_action == "process_refund" else None
            ws_state = session.get("workflow_state") or {}
            active_domain = ws_state.get("active_domain")

            explicitly_matches_pending = False
            if pending and not is_rejection:
                oid_param = str(pending.get("params", {}).get("order_id", ""))
                has_oid = bool(oid_param and oid_param in clean_msg)
                if pending_action == "cancel_order":
                    explicitly_matches_pending = bool(has_oid or ("cancel" in clean_msg and "order" in clean_msg))
                elif pending_action == "process_refund":
                    explicitly_matches_pending = bool(has_oid or "refund" in clean_msg)

            # Cross-domain Ambiguity Guard:
            # If active domain does not match the pending approval's domain, a bare confirmation
            # is ambiguous (customer may be answering an informational turn in product/general).
            if pending and is_confirmation and active_domain != pending_domain and not explicitly_matches_pending:
                action_desc = "cancellation" if pending_action == "cancel_order" else "refund"
                order_ref = pending.get("params", {}).get("order_id")
                clarification_msg = (
                    f"You have a pending {action_desc} request for order #{order_ref}. "
                    f"Did you want to confirm that {action_desc}, or were you referring to our previous discussion?"
                )
                emit_workflow_turn({
                    "session_id": session_id,
                    "customer_id": customer_id,
                    "architecture_mode": settings.agent_architecture_mode,
                    "outcome": "clarification_requested",
                    "approval_id": pending.get("approval_id"),
                    "approval_action": pending.get("action"),
                    "approval_status": "pending",
                    "pending_approval": True,
                    "escalated": False,
                })
                await manager.send_personal_message({
                    "type": "agent_response",
                    "message": clarification_msg,
                    "approval": {
                        "approval_id": pending.get("approval_id"),
                        "action": pending.get("action"),
                        "params": pending.get("params"),
                        "status": "ambiguous",
                    },
                    "agent_name": "Adi",
                    "escalated": False,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, session_id)
                continue

            # If user sent rejection while in a different domain without referencing the action,
            # treat it as part of that domain (e.g. "no, thanks" to product agent) rather than cancelling approval.
            is_valid_rejection = is_rejection and (
                active_domain == pending_domain
                or (pending and str(pending.get("params", {}).get("order_id", "")) in clean_msg)
                or (pending and ("cancel" in clean_msg or "refund" in clean_msg))
                or (clean_msg in ("no", "nope", "nah", "cancel", "stop", "never mind") and active_domain == pending_domain)
            )

            if pending and is_valid_rejection:
                # Customer cancelled the pending action
                def _approve_reject(s):
                    p = s.get("pending_approval")
                    if p:
                        p["status"] = ApprovalStatus.CANCELLED.value
                    s.pop("pending_approval", None)
                    s["conversation_history"].append({
                        "role": "agent",
                        "content": "No problem! I've cancelled that action. Is there anything else I can help with?",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "approval": {
                            "approval_id": pending.get("approval_id"),
                            "action": pending.get("action"),
                            "params": pending.get("params"),
                            "status": "cancelled",
                        },
                    })
                    return True
                session, _ = await _mutate_session(session_id, _approve_reject, allow_retry=True, customer_id=customer_id)
                emit_workflow_turn({
                    "session_id": session_id,
                    "customer_id": customer_id,
                    "architecture_mode": settings.agent_architecture_mode,
                    "outcome": "approval_rejected",
                    "approval_id": pending.get("approval_id"),
                    "approval_action": pending.get("action"),
                    "approval_status": "cancelled",
                    "pending_approval": False,
                    "escalated": False,
                })
                await manager.send_personal_message({
                    "type": "agent_response",
                    "message": "No problem! I've cancelled that action. Is there anything else I can help with?",
                    "approval": {
                        "approval_id": pending.get("approval_id"),
                        "action": pending.get("action"),
                        "params": pending.get("params"),
                        "status": "cancelled",
                    },
                    "agent_name": "Adi",
                    "escalated": False,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, session_id)
                continue

            elif pending and (is_confirmation or explicitly_matches_pending):
                # If resuming from a different domain, restore pending domain as active
                if active_domain and active_domain != pending_domain and pending_domain:
                    def _resume_pending_domain(s):
                        ws = s.get("workflow_state")
                        if isinstance(ws, dict):
                            old_dom = ws.get("active_domain")
                            if old_dom in ("product", "order", "payment"):
                                sus_list = ws.setdefault("suspended_domains", [])
                                if old_dom not in sus_list:
                                    sus_list.append(old_dom)
                            sus_list = ws.get("suspended_domains", [])
                            if pending_domain in sus_list:
                                sus_list.remove(pending_domain)
                            ws["active_domain"] = pending_domain
                            ws["resumed_domain"] = pending_domain
                            if pending_domain == "order" and isinstance(ws.get("order_state"), dict):
                                ws["order_state"]["domain_status"] = "in_progress"
                            elif pending_domain == "payment" and isinstance(ws.get("payment_state"), dict):
                                ws["payment_state"]["domain_status"] = "in_progress"
                            return True
                        return False
                    await _mutate_session(session_id, _resume_pending_domain, allow_retry=True, customer_id=customer_id)

                # Authoritative single trusted claim entrypoint
                claim_outcome: ApprovalClaimOutcome | None = None
                claim_reason: str | None = None

                def _claim_mutator(s):
                    nonlocal claim_outcome, claim_reason
                    outcome, reason = claim_pending_approval_in_session(
                        session_data=s,
                        authenticated_customer_id=customer_id,
                        session_id=session_id,
                    )
                    claim_outcome = outcome
                    claim_reason = reason
                    return outcome == ApprovalClaimOutcome.CLAIMED

                try:
                    session, claimed = await _mutate_session(session_id, _claim_mutator, allow_retry=False, customer_id=customer_id)
                except RuntimeError as e:
                    if "Concurrency conflict" in str(e):
                        claimed = False
                        session = await session_store.get_session(session_id) or {}
                        claim_outcome = ApprovalClaimOutcome.ALREADY_CLAIMED
                    else:
                        raise e

                if not claimed:
                    if claim_outcome == ApprovalClaimOutcome.IDENTITY_MISMATCH:
                        emit_workflow_turn({
                            "session_id": session_id,
                            "customer_id": customer_id,
                            "architecture_mode": settings.agent_architecture_mode,
                            "outcome": "approval_failed",
                            "approval_id": pending.get("approval_id"),
                            "approval_action": pending.get("action"),
                            "approval_status": "failed",
                            "pending_approval": False,
                            "escalated": False,
                        })
                        await manager.send_personal_message({
                            "type": "agent_response",
                            "message": "Approval blocked: Identity mismatch.",
                            "approval": {
                                "approval_id": pending.get("approval_id"),
                                "action": pending.get("action"),
                                "params": pending.get("params"),
                                "status": "failed",
                                "error": "Identity mismatch",
                            },
                            "agent_name": "Adi",
                            "escalated": False,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }, session_id)
                        continue

                    if claim_outcome == ApprovalClaimOutcome.SESSION_MISMATCH:
                        emit_workflow_turn({
                            "session_id": session_id,
                            "customer_id": customer_id,
                            "architecture_mode": settings.agent_architecture_mode,
                            "outcome": "approval_failed",
                            "approval_id": pending.get("approval_id"),
                            "approval_action": pending.get("action"),
                            "approval_status": "failed",
                            "pending_approval": False,
                            "escalated": False,
                        })
                        await manager.send_personal_message({
                            "type": "agent_response",
                            "message": "Approval blocked: Session mismatch.",
                            "approval": {
                                "approval_id": pending.get("approval_id"),
                                "action": pending.get("action"),
                                "params": pending.get("params"),
                                "status": "failed",
                                "error": "Session mismatch",
                            },
                            "agent_name": "Adi",
                            "escalated": False,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }, session_id)
                        continue

                    if claim_outcome == ApprovalClaimOutcome.SIGNATURE_FAILED:
                        emit_workflow_turn({
                            "session_id": session_id,
                            "customer_id": customer_id,
                            "architecture_mode": settings.agent_architecture_mode,
                            "outcome": "approval_failed",
                            "approval_id": pending.get("approval_id"),
                            "approval_action": pending.get("action"),
                            "approval_status": "failed",
                            "pending_approval": False,
                            "escalated": False,
                        })
                        await manager.send_personal_message({
                            "type": "agent_response",
                            "message": "Approval blocked: Cryptographic signature mismatch or tampering detected.",
                            "approval": {
                                "approval_id": pending.get("approval_id"),
                                "action": pending.get("action"),
                                "params": pending.get("params"),
                                "status": "failed",
                                "error": "Signature mismatch or tampering",
                            },
                            "agent_name": "Adi",
                            "escalated": False,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }, session_id)
                        continue

                    if claim_outcome == ApprovalClaimOutcome.EXPIRED:
                        def _cleanup_expired(s):
                            s.pop("pending_approval", None)
                            return True
                        await _mutate_session(session_id, _cleanup_expired, allow_retry=True, customer_id=customer_id)
                        emit_workflow_turn({
                            "session_id": session_id,
                            "customer_id": customer_id,
                            "architecture_mode": settings.agent_architecture_mode,
                            "outcome": "approval_failed",
                            "approval_id": pending.get("approval_id"),
                            "approval_action": pending.get("action"),
                            "approval_status": "expired",
                            "pending_approval": False,
                            "escalated": False,
                        })
                        await manager.send_personal_message({
                            "type": "agent_response",
                            "message": "This approval request has expired. Please submit a new request if you still need assistance.",
                            "approval": {
                                "approval_id": pending.get("approval_id"),
                                "action": pending.get("action"),
                                "params": pending.get("params"),
                                "status": "expired",
                            },
                            "agent_name": "Adi",
                            "escalated": False,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }, session_id)
                        continue
                    else:
                        emit_workflow_turn({
                            "session_id": session_id,
                            "customer_id": customer_id,
                            "architecture_mode": settings.agent_architecture_mode,
                            "outcome": "approval_failed",
                            "approval_id": pending.get("approval_id"),
                            "approval_action": pending.get("action"),
                            "approval_status": "already_claimed",
                            "pending_approval": False,
                            "escalated": False,
                        })
                        await manager.send_personal_message({
                            "type": "agent_response",
                            "message": "This action is already being processed or has already been completed.",
                            "approval": {
                                "approval_id": pending.get("approval_id"),
                                "action": pending.get("action"),
                                "params": pending.get("params"),
                                "status": "already_claimed",
                            },
                            "agent_name": "Adi",
                            "escalated": False,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }, session_id)
                        continue

                # 4. Successfully claimed! Execute within trusted boundary
                emit_workflow_turn({
                    "session_id": session_id,
                    "customer_id": customer_id,
                    "architecture_mode": settings.agent_architecture_mode,
                    "outcome": "approval_executing",
                    "approval_id": pending.get("approval_id"),
                    "approval_action": pending.get("action"),
                    "approval_status": "executing",
                    "pending_approval": True,
                    "escalated": False,
                })
                await manager.send_personal_message({
                    "type": "agent_response",
                    "message": "Processing your requested action with the authoritative system...",
                    "approval": {
                        "approval_id": pending.get("approval_id"),
                        "action": pending.get("action"),
                        "params": pending.get("params"),
                        "status": "executing",
                    },
                    "agent_name": "Adi",
                    "escalated": False,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, session_id)

                exec_result = await execute_approved_action(
                    approval=session.get("pending_approval") or pending,
                    authenticated_customer_id=customer_id,
                    session_id=session_id,
                )

                output_guard = validate_output(exec_result.response_text)
                final_response_text = output_guard.sanitized_text

                if exec_result.success:
                    def _finalize_success(s):
                        p = s.get("pending_approval")
                        if p:
                            p["status"] = ApprovalStatus.SUCCEEDED.value
                        s.pop("pending_approval", None)
                        s["conversation_history"].append({
                            "role": "agent",
                            "content": final_response_text,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "approval": {
                                "approval_id": pending.get("approval_id"),
                                "action": pending.get("action"),
                                "params": pending.get("params"),
                                "status": "succeeded",
                                "operation_key": pending.get("operation_key"),
                                "authoritative": True,
                            },
                        })
                        ws = s.get("workflow_state")
                        if isinstance(ws, dict):
                            if pending_domain == "order" and isinstance(ws.get("order_state"), dict):
                                ws["order_state"]["domain_status"] = "completed"
                            elif pending_domain == "payment" and isinstance(ws.get("payment_state"), dict):
                                ws["payment_state"]["domain_status"] = "completed"
                        return True
                    await _mutate_session(session_id, _finalize_success, allow_retry=True, customer_id=customer_id)
                    emit_workflow_turn({
                        "session_id": session_id,
                        "customer_id": customer_id,
                        "architecture_mode": settings.agent_architecture_mode,
                        "outcome": "approval_succeeded",
                        "approval_id": pending.get("approval_id"),
                        "approval_action": pending.get("action"),
                        "approval_status": "succeeded",
                        "pending_approval": False,
                        "escalated": False,
                    })
                    await manager.send_personal_message({
                        "type": "agent_response",
                        "message": final_response_text,
                        "approval": {
                            "approval_id": pending.get("approval_id"),
                            "action": pending.get("action"),
                            "params": pending.get("params"),
                            "status": "succeeded",
                            "operation_key": pending.get("operation_key"),
                            "authoritative": True,
                        },
                        "agent_name": "Adi",
                        "escalated": False,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }, session_id)
                else:
                    def _finalize_failure(s):
                        p = s.get("pending_approval")
                        if p:
                            p["status"] = ApprovalStatus.FAILED.value
                        s.pop("pending_approval", None)
                        s["conversation_history"].append({
                            "role": "agent",
                            "content": final_response_text,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "approval": {
                                "approval_id": pending.get("approval_id"),
                                "action": pending.get("action"),
                                "params": pending.get("params"),
                                "status": "uncertain" if exec_result.is_uncertain else "failed",
                                "error": exec_result.error,
                                "authoritative": True,
                            },
                        })
                        return True
                    await _mutate_session(session_id, _finalize_failure, allow_retry=True, customer_id=customer_id)
                    outcome_name = "approval_uncertain" if exec_result.is_uncertain else "approval_failed"
                    emit_workflow_turn({
                        "session_id": session_id,
                        "customer_id": customer_id,
                        "architecture_mode": settings.agent_architecture_mode,
                        "outcome": outcome_name,
                        "approval_id": pending.get("approval_id"),
                        "approval_action": pending.get("action"),
                        "approval_status": "failed",
                        "pending_approval": False,
                        "escalated": False,
                    })
                    await manager.send_personal_message({
                        "type": "agent_response",
                        "message": final_response_text,
                        "approval": {
                            "approval_id": pending.get("approval_id"),
                            "action": pending.get("action"),
                            "params": pending.get("params"),
                            "status": "uncertain" if exec_result.is_uncertain else "failed",
                            "error": exec_result.error,
                            "authoritative": True,
                        },
                        "agent_name": "Adi",
                        "escalated": False,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }, session_id)
                continue



            # Send typing indicator (only when AI will actually process)
            await manager.send_personal_message({
                "type": "typing",
                "agent_name": "Adi",
            }, session_id)

            # Run LangGraph
            try:
                initial_state: AgentState = {
                    "customer_id": customer_id,
                    "customer_name": session.get("customer_name"),
                    "session_id": session_id,
                    "client_request_id": client_request_id,
                    "message": message,
                    "conversation_history": session["conversation_history"],
                    "intent": "",
                    "sentiment": "",
                    "urgency": "",
                    "route_to": "",
                    "tool_results": None,
                    "response": None,
                    "escalated": False,
                    "pending_approval": session.get("pending_approval"),
                    "approval_granted": None,
                    "workflow_state": session.get("workflow_state", {
                        "session_id": session_id,
                        "customer_id": customer_id,
                        "turn_count": 0
                    }),
                }

                # Invoke LangGraph with timeout and periodic typing keepalive
                timeout_secs = settings.langgraph_timeout
                async def _keepalive_typing():
                    try:
                        while True:
                            await asyncio.sleep(4)
                            await manager.send_personal_message({
                                "type": "typing",
                                "agent_name": "Adi",
                            }, session_id)
                    except asyncio.CancelledError:
                        pass

                turn_started = time.perf_counter()
                typing_task = asyncio.create_task(_keepalive_typing())
                try:
                    active_graph = (
                        domain_support_graph
                        if settings.agent_architecture_mode.startswith("v2_")
                        else customer_support_graph
                    )
                    result = await asyncio.wait_for(
                        active_graph.ainvoke(initial_state),
                        timeout=timeout_secs,
                    )
                except asyncio.TimeoutError:
                    logger.error(f"LangGraph timed out after {timeout_secs}s for session {session_id}")
                    emit_workflow_turn({
                        "session_id": session_id,
                        "customer_id": customer_id,
                        "architecture_mode": settings.agent_architecture_mode,
                        "escalated": False,
                        "latency_ms": round((time.perf_counter() - turn_started) * 1000, 1),
                        "outcome": "timeout",
                    })
                    await manager.send_personal_message({
                        "type": "agent_response",
                        "message": "I'm taking longer than expected to process your request. Please try again or let me connect you with a human agent.",
                        "agent_name": "Adi",
                        "escalated": False,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }, session_id)
                    continue
                finally:
                    typing_task.cancel()

                # We still re-check mode but the whole mutation must be atomic
                raw_response = result.get("response", "I'm sorry, I couldn't process your request.")
                is_escalated = result.get("escalated", False)
                emit_workflow_turn({
                    "session_id": session_id,
                    "customer_id": customer_id,
                    "architecture_mode": settings.agent_architecture_mode,
                    "domain": result.get("semantic_domain"),
                    "intent": result.get("intent"),
                    "supervisor_action": result.get("supervisor_action"),
                    "supervisor_reason": result.get("supervisor_reason"),
                    "guardian_action": result.get("guardian_action"),
                    "guardian_reason": result.get("guardian_reason"),
                    "ticket_id": result.get("guardian_ticket_id"),
                    "guardian_ticket_id": result.get("guardian_ticket_id"),
                    "pending_approval": bool(result.get("pending_approval")),
                    "approval_id": (result.get("pending_approval") or {}).get("approval_id"),
                    "approval_action": (result.get("pending_approval") or {}).get("action"),
                    "approval_status": "pending" if result.get("pending_approval") else None,
                    "escalated": is_escalated,
                    "latency_ms": round((time.perf_counter() - turn_started) * 1000, 1),
                    "outcome": "approval_proposed" if result.get("pending_approval") else "response_generated",
                })

                # ── Output Guardrails ──
                output_guard = validate_output(raw_response)
                response_text = output_guard.sanitized_text
                if output_guard.violations:
                    logger.warning(
                        f"Output guardrails triggered for session {session_id}",
                        extra={"violations": output_guard.violations, "risk_score": output_guard.risk_score},
                    )

                def _apply_graph_result(s):
                    if s.get("mode") == "human":
                        return False # do not save, human took over
                    
                    if result.get("pending_approval"):
                        s["pending_approval"] = result["pending_approval"]
                        s["pending_approval"]["original_message"] = message
                        s["pending_approval"]["intent"] = result.get("intent", "")

                    if result.get("workflow_state"):
                        s["workflow_state"] = result["workflow_state"]

                    s["workflow_outcome"] = {
                        "domain": result.get("semantic_domain"),
                        "guardian_action": result.get("guardian_action"),
                        "guardian_reason": result.get("guardian_reason"),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }

                    agent_entry = {
                        "role": "agent",
                        "content": response_text,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    if result.get("pending_approval"):
                        p = result["pending_approval"]
                        agent_entry["approval"] = {
                            "approval_id": p.get("approval_id"),
                            "action": p.get("action"),
                            "params": p.get("params"),
                            "status": "pending",
                            "message": p.get("message"),
                        }
                    s["conversation_history"].append(agent_entry)
                    return True

                try:
                    # NEVER blindly merge arbitrary typed domain state. 
                    session, was_changed = await _mutate_session(session_id, _apply_graph_result, allow_retry=False, customer_id=customer_id)
                    if not was_changed:
                        logger.info(f"AI response discarded for {session_id} - agent took over during processing.")
                        continue
                except RuntimeError as e:
                    if "Concurrency conflict" in str(e):
                        logger.error(f"CAS conflict on graph result for session {session_id}: {e}")
                        await manager.send_personal_message({
                            "type": "error",
                            "message": "The conversation was updated in another window. Please try your request again.",
                            "agent_name": "Adi",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }, session_id)
                        continue
                    raise e

                # Send response to customer
                response_payload = {
                    "type": "agent_response",
                    "session_id": session_id,
                    "message": response_text,
                    "intent": result.get("intent", ""),
                    "sentiment": result.get("sentiment", ""),
                    "urgency": result.get("urgency", ""),
                    "domain": result.get("semantic_domain", ""),
                    "guardian_action": result.get("guardian_action", ""),
                    "guardian_reason": result.get("guardian_reason", ""),
                    "escalated": is_escalated,
                    "agent_name": "Adi",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                if result.get("pending_approval"):
                    p = result["pending_approval"]
                    response_payload["approval"] = {
                        "approval_id": p.get("approval_id"),
                        "action": p.get("action"),
                        "params": p.get("params"),
                        "status": "pending",
                        "message": p.get("message"),
                    }
                await manager.send_personal_message(response_payload, session_id)

                # Broadcast to monitoring agents
                await manager.broadcast_to_agents({
                    "type": "agent_response",
                    **response_payload,
                }, session_id)

                # If escalated, trigger transactional EscalationService
                if is_escalated and customer_id is not None:
                    from app.escalation.service import EscalationService, EscalationRequest, IdempotencyConflict
                    
                    ticket_id = result.get("guardian_ticket_id")
                    ws_state = result.get("workflow_state", {})
                    if ws_state.get("active_domain") == "product" and ws_state.get("product_state"):
                        ticket_id = ws_state["product_state"].get("active_ticket_id")
                    elif ws_state.get("active_domain") == "order" and ws_state.get("order_state"):
                        ticket_id = ws_state["order_state"].get("active_ticket_id")
                    elif ws_state.get("active_domain") == "payment" and ws_state.get("payment_state"):
                        ticket_id = ws_state["payment_state"].get("active_ticket_id")
                        
                    if not client_request_id:
                        logger.error(f"Missing client_request_id for escalation in session {session_id}")
                        await manager.send_personal_message({
                            "type": "error",
                            "message": "Missing client_request_id. Cannot perform escalation.",
                            "agent_name": "System",
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }, session_id)
                        continue
                        
                    esc_req = EscalationRequest(
                        customer_id=customer_id,
                        session_id=session_id,
                        client_request_id=uuid.UUID(client_request_id),
                        ticket_id=ticket_id,
                        sentiment=result.get("sentiment", "negative"),
                        urgency=result.get("urgency", "high"),
                        last_message=message,
                        customer_notification_required=True
                    )
                    
                    try:
                        esc_res = await asyncio.to_thread(EscalationService.escalate_session, esc_req)
                        
                        if esc_res.is_success or getattr(esc_res, 'status', None) == "bypassed":
                            def _escalate_mode(s):
                                if s.get("mode") != "human":
                                    s["mode"] = "human"
                                    return True
                                return False
                            session, _ = await _mutate_session(session_id, _escalate_mode, allow_retry=True, customer_id=customer_id)
                            
                        # Send escalation alert to ALL dashboard agents so they can pick it up
                        escalation_msg = {
                            "type": "escalation_alert",
                            "session_id": session_id,
                            "customer_id": customer_id,
                            "sentiment": result.get("sentiment", "negative"),
                            "urgency": result.get("urgency", "high"),
                            "last_message": message,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }
                        await manager.broadcast_to_all_agents(escalation_msg)
                        
                    except IdempotencyConflict as ic:
                        logger.warning(f"Idempotency conflict on escalation for session {session_id}: {ic}")
                    except Exception as e:
                        logger.error(f"Failed to execute escalation service for session {session_id}: {e}")

            except Exception as e:
                logger.error(f"LangGraph error for session {session_id}: {e}")
                emit_workflow_turn({
                    "session_id": session_id,
                    "customer_id": customer_id,
                    "architecture_mode": settings.agent_architecture_mode,
                    "escalated": False,
                    "latency_ms": round((time.perf_counter() - turn_started) * 1000, 1) if "turn_started" in locals() else 0.0,
                    "outcome": "error",
                })
                await manager.send_personal_message({
                    "type": "error",
                    "message": "I'm experiencing a technical issue. Let me connect you with a human agent.",
                    "agent_name": "Adi",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, session_id)

    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Error in customer websocket: {e}")
        
    finally:
        manager.disconnect_customer(session_id, websocket)
        logger.info(f"Customer disconnected: {session_id}")
        
        # Save conversation to database.
        # We use local variables to avoid `await` which would raise CancelledError if the task is cancelled.
        try:
            # Only save if we have initialized the session earlier in the function
            if 'session' in locals() and session and session.get("conversation_history"):
                was_escalated = "true" if session.get("mode") == "human" or any(
                     msg.get("escalated") for msg in session.get("conversation_history", [])
                ) else "false"
                
                # #review: Defaulting customer_id to 1 assigns transcripts from unauthenticated/anonymous guests to Customer 1 in Supabase
                data = {
                    "session_id": session_id,
                    "customer_id": session.get("customer_id") or 1,
                    "transcript": session.get("conversation_history"),
                    "escalated": True if was_escalated == "true" else False,
                    "ended_at": datetime.now(timezone.utc).isoformat()
                }
                from app.tools import supabase
                import anyio
                
                # Instead of asyncio.shield (which fails under anyio cancellation),
                # we spawn a background thread natively so it survives cancellation
                # and we don't await it here to prevent CancelledError.
                def save_to_db(save_data):
                    try:
                        supabase.table("conversations").upsert(save_data, on_conflict="session_id").execute()
                    except Exception as ex:
                        pass
                
                import threading
                t = threading.Thread(target=save_to_db, args=(data,))
                t.start()
                # In tests we might need it to finish, so we join with a short timeout.
                # This safely waits for the persistence operation without raising CancelledError.
                t.join(timeout=2.0)
                
        except Exception as e:
            logger.error(f"Failed to save conversation to DB for session {session_id}: {e}")

async def handle_agent_ws(websocket: WebSocket, session_id: str):
    """
    Handler for agent dashboard WebSocket connections.
    Allows agents to monitor and take over customer conversations.
    """
    await manager.connect_agent(websocket, session_id)

    # Send current conversation history to the agent
    session = await _get_session(session_id)
    await websocket.send_text(json.dumps({
        "type": "session_state",
        "session_id": session_id,
        "conversation_history": session.get("conversation_history", []),
        "customer_id": session.get("customer_id"),
    }))

    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except RuntimeError as e:
                logger.warning(f"WebSocket RuntimeError (agent likely disconnected): {e}")
                break
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")

            if msg_type == "agent_message":
                # Keep AI and human modes mutually exclusive.
                if session.get("mode") != "human":
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "Take over the conversation before sending a human-agent reply.",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }))
                    continue

                # Human agent is sending a message to the customer
                agent_message = data.get("message", "").strip()
                agent_name = data.get("agent_name", "Support Agent")

                if not agent_message:
                    continue

                # Append to history
                def _agent_message(s):
                    s["conversation_history"].append({
                        "role": "agent",
                        "content": agent_message,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "human_agent": True,
                        "agent_name": agent_name,
                    })
                    return True
                session, _ = await _mutate_session(session_id, _agent_message, allow_retry=True)

                # Send to customer
                await manager.send_personal_message({
                    "type": "human_message",
                    "session_id": session_id,
                    "message": agent_message,
                    "agent_name": agent_name,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, session_id)

            elif msg_type == "takeover":
                # Persist the takeover before changing Redis/in-memory session mode.
                client_request_id_str = data.get("client_request_id")
                customer_id = session.get("customer_id")
                if not client_request_id_str or not customer_id:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "Takeover requires a valid request ID and customer session.",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }))
                    continue

                try:
                    from app.persistence.escalation import ensure_durable_escalation_state
                    from app.models import EscalationLifecycleStatus
                    durable_status = await asyncio.to_thread(
                        ensure_durable_escalation_state, session_id, customer_id
                    )

                    if durable_status != EscalationLifecycleStatus.ACTIVE:
                        from app.escalation.service import (
                            EscalationService, EscalationRequest, IdempotencyConflict
                        )
                        latest_customer_message = next(
                            (
                                item.get("content", "")
                                for item in reversed(session.get("conversation_history", []))
                                if item.get("role") == "customer"
                            ),
                            "",
                        )
                        request = EscalationRequest(
                            customer_id=customer_id,
                            session_id=session_id,
                            client_request_id=uuid.UUID(client_request_id_str),
                            sentiment="neutral",
                            urgency="normal",
                            last_message=latest_customer_message,
                            customer_notification_required=False,
                        )
                        result = await asyncio.to_thread(
                            EscalationService.escalate_session, request
                        )
                        if not (result.is_success or result.status == "bypassed"):
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "message": result.error or result.message or "Could not persist takeover.",
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }))
                            continue

                    def _takeover(s):
                        if s.get("mode") != "human":
                            s["mode"] = "human"
                            return True
                        return False
                    session, was_changed = await _mutate_session(
                        session_id, _takeover, allow_retry=True
                    )
                    if was_changed and session and session.get("mode") == "human":
                        await manager.send_personal_message({
                            "type": "mode_change",
                            "mode": "human",
                            "agent_name": data.get("agent_name", "Support Agent"),
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }, session_id)
                except Exception as e:
                    logger.error(f"Failed to persist takeover for session {session_id}: {e}")
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "Could not take over this conversation. Please retry.",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }))

            elif msg_type == "release":
                # Agent is returning the conversation to the AI
                client_request_id_str = data.get("client_request_id")
                if not client_request_id_str:
                    logger.error("Missing client_request_id for release.")
                    continue
                
                customer_id = session.get("customer_id")
                if not customer_id:
                    logger.error("No customer_id associated with session.")
                    continue
                    
                from app.escalation.service import EscalationService, ReleaseRequest, IdempotencyConflict
                
                try:
                    req = ReleaseRequest(
                        customer_id=customer_id,
                        session_id=session_id,
                        client_request_id=uuid.UUID(client_request_id_str)
                    )
                    res = EscalationService.release_session(req)
                    
                    if res.status == "success":
                        def _release(s):
                            if s.get("mode") != "ai":
                                s["mode"] = "ai"
                                return True
                            return False
                        session, was_changed = await _mutate_session(session_id, _release, allow_retry=True)
                        if was_changed and session and session.get("mode") == "ai":
                            await manager.send_personal_message({
                                "type": "mode_change",
                                "mode": "ai",
                                "agent_name": data.get("agent_name", "AI Assistant"),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }, session_id)
                    else:
                        logger.error(f"Release failed: {res.error}")
                        
                except IdempotencyConflict as ic:
                    logger.warning(f"Idempotency conflict on release: {ic}")
                except Exception as e:
                    logger.error(f"Exception during release: {e}")

    except WebSocketDisconnect:
        manager.disconnect_agent(websocket, session_id)
        logger.info(f"Agent disconnected from session: {session_id}")
