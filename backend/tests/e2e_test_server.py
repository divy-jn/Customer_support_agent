"""
Isolated FastAPI Test Server for Playwright Browser E2E Tests.

Features:
- Runs real FastAPI application (app.main:app) on port 8002.
- Mocked mutation adapters for process_refund and cancel_order (zero live DB/gateway changes).
- Mocked customer lookup and dev-login.
- Deterministic LangGraph domain responses for all test scenarios.
- Controlled simulation endpoints (/api/test/e2e/simulate, /api/test/e2e/state, /api/test/e2e/reset).
- In-memory session store (zero external Redis dependency).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import jwt
import uvicorn
from fastapi import Request
from fastapi.responses import JSONResponse

# Ensure backend root is on sys.path
backend_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_root))

from app.config import settings

# Force test configuration
settings.debug_mode = True
settings.jwt_secret = "dev-secret-key-that-is-long-enough-for-testing-purposes"
settings.approval_signing_secret = "test-approval-signing-secret-key-for-hmac-32chars"
settings.agent_architecture_mode = "v2_all"
settings.outbox_worker_mode = "disabled"
settings.outbox_worker_enabled = False
settings.reconciliation_enabled = False

# Ensure in-memory session storage
import app.persistence.session as session_module
session_module.redis_client = None
session_store = session_module.session_store
session_store.clear()
import app.main as main_module
main_module._run_startup_checks = lambda: None
from app.main import app
from app.models import ApprovalStatus
from app.agents.approval_service import (
    ApprovalClaimOutcome,
    ApprovalExecutionResult,
    create_pending_approval,
    execute_approved_action as original_execute_approved_action,
)

logger = logging.getLogger("e2e.test_server")

# ──────────────────────────────────────────────
# State Tracking & Deterministic Mutation Mocks
# ──────────────────────────────────────────────

state_tracker = {
    "refund_calls": [],
    "cancellation_calls": [],
    "email_calls": [],
    "simulated_outcome": None,
}


def mock_process_refund(order_id: int, customer_id: Optional[int] = None, client_request_id: Optional[str] = None, amount: Optional[float] = None, reason: Optional[str] = None, **kwargs):
    logger.info(f"[TEST MOCK] process_refund called for order {order_id}, customer {customer_id}")
    state_tracker["refund_calls"].append({
        "order_id": order_id,
        "customer_id": customer_id,
        "amount": amount,
        "reason": reason,
        "client_request_id": client_request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return json.dumps({
        "status": "success",
        "order_id": order_id,
        "refund_id": f"ref_{order_id}_{uuid.uuid4().hex[:6]}",
        "message": f"Refund of ${amount or 150.0} for order #{order_id} processed successfully.",
    })


def mock_cancel_order(order_id: int, customer_id: Optional[int] = None, client_request_id: Optional[str] = None, reason: Optional[str] = None, **kwargs):
    logger.info(f"[TEST MOCK] cancel_order called for order {order_id}, customer {customer_id}")
    state_tracker["cancellation_calls"].append({
        "order_id": order_id,
        "customer_id": customer_id,
        "reason": reason,
        "client_request_id": client_request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return json.dumps({
        "status": "success",
        "order_id": order_id,
        "message": f"Order #{order_id} has been cancelled successfully.",
    })


def mock_send_escalation_email(*args, **kwargs):
    state_tracker["email_calls"].append({"args": args, "kwargs": kwargs})
    return True


def mock_lookup_customer(query: str):
    return json.dumps([{
        "id": 1,
        "name": "Amit Sharma",
        "email": "amit.sharma@example.com",
        "phone": "+91 98765 43210",
        "status": "active",
    }])


# Patch tools in app
import app.tools as tools_module
import app.routes as routes_module
import app.email_service as email_module
import app.websocket.chat_handler as chat_handler_module
import app.persistence.escalation as escalation_module
from app.models import EscalationLifecycleStatus

def mock_ensure_durable_escalation_state(session_id, customer_id):
    return EscalationLifecycleStatus.NONE

class MockQuery:
    def select(self, *args, **kwargs): return self
    def eq(self, *args, **kwargs): return self
    def limit(self, *args, **kwargs): return self
    def order(self, *args, **kwargs): return self
    def execute(self):
        class Resp:
            data = [{"id": 1, "name": "Amit Sharma", "email": "amit.sharma@example.com", "customer_id": 1, "status": "delivered"}]
        return Resp()

class MockSupabase:
    def table(self, name):
        return MockQuery()
    def rpc(self, name, params=None):
        class Rpc:
            def execute(self):
                class Resp:
                    data = {"status": "success"}
                return Resp()
        return Rpc()

mock_supabase_client = MockSupabase()
tools_module.supabase = mock_supabase_client
routes_module.supabase = mock_supabase_client

async def mock_dev_login(email: str):
    expiration = datetime.now(timezone.utc) + timedelta(minutes=60)
    payload = {
        "sub": "1",
        "role": "customer",
        "exp": expiration,
        "email": email,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return {"access_token": token, "token_type": "bearer"}

routes_module.dev_login = mock_dev_login
escalation_module.ensure_durable_escalation_state = mock_ensure_durable_escalation_state
tools_module.process_refund = mock_process_refund
tools_module.cancel_order = mock_cancel_order
tools_module.lookup_customer = mock_lookup_customer
routes_module.lookup_customer = mock_lookup_customer
email_module.send_escalation_email = mock_send_escalation_email


# ──────────────────────────────────────────────
# Controlled execute_approved_action wrapper
# ──────────────────────────────────────────────

async def intercepted_execute_approved_action(*args, **kwargs):
    sim = state_tracker.get("simulated_outcome")
    approval = kwargs.get("approval") or (args[0] if args else {})
    if isinstance(approval, dict):
        action = approval.get("action", "process_refund")
        op_key = str(approval.get("operation_key", "00000000-0000-0000-0000-000000000000"))
        order_id = approval.get("params", {}).get("order_id", 4004)
    else:
        action = getattr(approval, "action", "process_refund")
        op_key = str(getattr(approval, "operation_key", "00000000-0000-0000-0000-000000000000"))
        order_id = getattr(approval, "params", {}).get("order_id", 4004)

    if sim == "failed":
        logger.info("[TEST SIMULATION] Returning simulated FAILED outcome")
        return ApprovalExecutionResult(
            success=False,
            action=action,
            operation_key=op_key,
            response_text=f"Refund Request Failed for Order #{order_id}: Insufficient merchant ledger balance.",
            error="Insufficient merchant ledger balance.",
            status=ApprovalStatus.FAILED,
            is_uncertain=False,
        )
    elif sim == "uncertain":
        logger.info("[TEST SIMULATION] Returning simulated UNCERTAIN outcome")
        return ApprovalExecutionResult(
            success=False,
            action=action,
            operation_key=op_key,
            response_text=f"Outcome Uncertain — Verification Active for Order #{order_id}. Idempotent background reconciliation is active.",
            error="Gateway timeout contacting acquiring bank. Background reconciliation active.",
            status=ApprovalStatus.FAILED,
            is_uncertain=True,
        )
    elif sim == "expired":
        logger.info("[TEST SIMULATION] Returning simulated EXPIRED outcome")
        return ApprovalExecutionResult(
            success=False,
            action=action,
            operation_key=op_key,
            response_text=f"Approval request expired for Order #{order_id}.",
            error="Approval request expired. Please ask again if still required.",
            status=ApprovalStatus.EXPIRED,
            is_uncertain=False,
        )
    elif sim == "tampered":
        logger.info("[TEST SIMULATION] Returning simulated TAMPERED outcome")
        return ApprovalExecutionResult(
            success=False,
            action=action,
            operation_key=op_key,
            response_text="Approval blocked: Cryptographic signature mismatch or tampering detected.",
            error="Cryptographic signature verification failed: Payload was tampered.",
            status=ApprovalStatus.FAILED,
            is_uncertain=False,
        )
    elif sim == "already_claimed":
        logger.info("[TEST SIMULATION] Returning simulated ALREADY_CLAIMED outcome")
        return ApprovalExecutionResult(
            success=False,
            action=action,
            operation_key=op_key,
            response_text="This action is already being processed or has already been completed.",
            error="Action already claimed or completed.",
            status=ApprovalStatus.FAILED,
            is_uncertain=False,
        )

    # Standard execution path
    return await original_execute_approved_action(*args, **kwargs)

chat_handler_module.execute_approved_action = intercepted_execute_approved_action


# ──────────────────────────────────────────────
# Deterministic Domain Support Graph Mock
# ──────────────────────────────────────────────

async def mock_graph_ainvoke(inputs: dict, *args, **kwargs) -> dict:
    last_msg = inputs.get("message", "")
    if not last_msg and inputs.get("messages"):
        m = inputs["messages"][-1]
        last_msg = getattr(m, "content", str(m))
    session_id = inputs.get("session_id", "test-session")
    customer_id = inputs.get("customer_id", 1) or 1
    clean = last_msg.lower().strip()

    logger.info(f"[TEST GRAPH] Processing customer turn: '{last_msg}'")

    if "refund" in clean and ("4004" in clean or "order" in clean):
        proposal = create_pending_approval(
            action="process_refund",
            params={"order_id": 4004},
            customer_id=customer_id,
            session_id=session_id,
            message="Would you like me to process a refund for order #4004?",
        )
        return {
            "response": "I can help refund order #4004. Would you like me to process this refund?",
            "pending_approval": proposal.model_dump(),
            "workflow_state": {
                "active_domain": "payment",
                "state_revision": 1,
                "session_id": session_id,
                "customer_id": customer_id,
                "turn_count": 1,
                "payment_state": {"domain_status": "in_progress", "order_id": 4004},
            },
            "semantic_domain": "payment",
            "intent": "process_refund",
            "escalated": False,
        }

    elif "cancel" in clean and ("1001" in clean or "order" in clean) and "do not cancel" not in clean and "don't cancel" not in clean:
        proposal = create_pending_approval(
            action="cancel_order",
            params={"order_id": 1001},
            customer_id=customer_id,
            session_id=session_id,
            message="Would you like me to cancel order #1001?",
        )
        return {
            "response": "I can cancel order #1001 for you. Please review and confirm below.",
            "pending_approval": proposal.model_dump(),
            "workflow_state": {
                "active_domain": "order",
                "state_revision": 1,
                "session_id": session_id,
                "customer_id": customer_id,
                "turn_count": 1,
                "order_state": {"domain_status": "in_progress", "order_id": 1001},
            },
            "semantic_domain": "order",
            "intent": "cancel_order",
            "escalated": False,
        }

    elif "earbuds" in clean or "warranty" in clean or "product" in clean:
        return {
            "response": "The wireless earbuds include a 1-year manufacturer warranty covering internal defects. Accidental physical damage is not covered.",
            "pending_approval": None,
            "workflow_state": {
                "active_domain": "product",
                "state_revision": 2,
                "session_id": session_id,
                "customer_id": customer_id,
                "turn_count": 2,
                "product_state": {"domain_status": "resolved"},
            },
            "semantic_domain": "product",
            "intent": "product_warranty",
            "escalated": False,
        }

    # Default fallback
    return {
        "response": f"I understand: '{last_msg}'. How else may I assist you today?",
        "pending_approval": None,
        "workflow_state": {
            "active_domain": "general",
            "state_revision": 1,
            "session_id": session_id,
            "customer_id": customer_id,
            "turn_count": 1,
        },
        "semantic_domain": "general",
        "intent": "faq",
        "escalated": False,
    }

chat_handler_module.domain_support_graph.ainvoke = mock_graph_ainvoke


# ──────────────────────────────────────────────
# Test Helper Routes
# ──────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def e2e_health_check():
    """Fast health check for E2E webServer probe."""
    return {"status": "healthy", "service": "e2e-backend", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/api/test/e2e/reset")
async def test_reset():
    """Reset session store and mutation counters."""
    session_store.clear()
    state_tracker["refund_calls"].clear()
    state_tracker["cancellation_calls"].clear()
    state_tracker["email_calls"].clear()
    state_tracker["simulated_outcome"] = None
    logger.info("[TEST CONTROL] Reset test state and in-memory sessions.")
    return {"status": "reset", "session_count": 0}


@app.get("/api/test/e2e/state")
async def test_get_state():
    """Inspect mutation call counts and state."""
    return {
        "refund_calls": state_tracker["refund_calls"],
        "cancellation_calls": state_tracker["cancellation_calls"],
        "email_calls": state_tracker["email_calls"],
        "simulated_outcome": state_tracker["simulated_outcome"],
        "active_sessions": list(session_store._sessions.keys()) if hasattr(session_store, "_sessions") else [],
    }


@app.post("/api/test/e2e/simulate")
async def test_simulate_outcome(outcome: Optional[str] = None):
    """Set simulated outcome for subsequent executions (failed, uncertain, expired, tampered, already_claimed)."""
    state_tracker["simulated_outcome"] = outcome
    logger.info(f"[TEST CONTROL] Set simulated outcome to: {outcome}")
    return {"simulated_outcome": outcome}


@app.post("/api/v1/auth/dev-login")
async def dev_login_override(email: str):
    """Deterministic dev-login without touching Supabase."""
    expiration = datetime.now(timezone.utc) + timedelta(minutes=60)
    payload = {
        "sub": "1",
        "role": "customer",
        "exp": expiration,
        "email": email,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return {"access_token": token, "token_type": "bearer"}


def run_server(host: str = "127.0.0.1", port: int = 8002):
    """Start uvicorn server."""
    logger.info(f"Starting E2E Test Server on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run_server()
