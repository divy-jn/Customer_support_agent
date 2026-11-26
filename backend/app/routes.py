"""
REST API Routes — provides HTTP endpoints for the frontend
to interact with tickets, customers, and dashboard data.
"""

from fastapi import APIRouter, HTTPException, Depends, Header
import json
import os
import uuid
from pathlib import Path
from tempfile import NamedTemporaryFile
import asyncio

from app.dependencies import (
    verify_admin_key, verify_agent_token, verify_customer_token, verify_any_token,
    verify_operator_access, verify_staff_directory,
)
from app.config import settings
from app.supabase_auth import exchange_supabase_token

# ──────────────────────────────────────────────
#  Development Authentication
# ──────────────────────────────────────────────
from pydantic import BaseModel, Field
from datetime import datetime, timedelta, timezone
import jwt

class DevLoginRequest(BaseModel):
    customer_id: int

# Move dev_login below

from app.tools import (
    lookup_customer, get_customer_history, get_ticket, create_ticket,
    update_ticket, track_order, cancel_order, process_refund,
    check_inventory, get_dashboard_stats, get_all_customers, get_all_tickets,
    check_llm_health, check_vectordb_health, check_supabase_health,
    get_system_info, list_knowledge_base_docs, get_knowledge_base_doc,
)
from app.models import TicketCreate, TicketUpdate, TicketResponse, TicketStatus
from app.persistence.session import session_store
from app.email_service import send_ticket_created_email, send_resolution_email

router = APIRouter()
auth_router = APIRouter()
auth_router.add_api_route("/auth/supabase/exchange", exchange_supabase_token, methods=["POST"])

# ──────────────────────────────────────────────
#  Development Authentication Endpoints
# ──────────────────────────────────────────────
@router.post("/auth/agent/dev-login")
def dev_agent_login():
    """
    Local-development agent login. Disabled unless DEBUG_MODE is explicitly enabled.
    Production deployments must use a real agent identity provider.
    """
    if not settings.debug_mode or settings.app_environment not in {"development", "test"}:
        raise HTTPException(status_code=403, detail="Development agent login is disabled")
    expiration = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expiration_minutes)
    payload = {
        "sub": "local-support-agent",
        "role": "agent",
        "exp": expiration,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return {"access_token": token, "token_type": "bearer"}


@router.post("/auth/dev-login")
def dev_login(email: str):
    """
    DEVELOPMENT ONLY endpoint.
    Generates a JWT token for the specified email without password verification.
    #review: In DEBUG_MODE, this endpoint allows impersonating any customer by email.
    # Must remain strictly disabled in production environments.
    """
    if not settings.debug_mode or settings.app_environment not in {"development", "test"}:
        raise HTTPException(status_code=403, detail="Development customer login is disabled")
        
    from app.tools import supabase
    res = supabase.table("customers").select("id").eq("email", email).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Customer not found")
    customer_id = res.data[0]["id"]
        
    expiration = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expiration_minutes)
    payload = {
        "sub": str(customer_id),
        "role": "customer",
        "exp": expiration
    }
    
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return {"access_token": token, "token_type": "bearer"}


# ──────────────────────────────────────────────
#  Customer Endpoints
# ──────────────────────────────────────────────

@router.get("/customers/search")
def search_customer(q: str, _: dict = Depends(verify_staff_directory)):
    """Search for a customer by name or email."""
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Search query cannot be empty")
    data = json.loads(lookup_customer(q))
    if isinstance(data, dict) and "error" in data:
        raise HTTPException(status_code=503, detail="Customer search is unavailable")
    return data


@router.get("/customers")
def list_customers(limit: int = 50, offset: int = 0, _: dict = Depends(verify_staff_directory)):
    """Get all customers with pagination."""
    if limit <= 0 or offset < 0:
        raise HTTPException(status_code=400, detail="Invalid pagination parameters")
    data = json.loads(get_all_customers(limit=limit, offset=offset))
    if isinstance(data, dict) and "error" in data:
        raise HTTPException(status_code=503, detail="Customer directory is unavailable")
    return data


@router.get("/customers/{customer_id}/history")
def customer_history(customer_id: int, auth_user: dict = Depends(verify_any_token)):
    """Allow staff to read customer history and customers to read their own."""
    if auth_user["role"] == "customer" and auth_user["id"] != customer_id:
        raise HTTPException(status_code=403, detail="Access denied")
    data = json.loads(get_customer_history(customer_id))
    if "error" in data:
        if data["error"] == f"Customer #{customer_id} not found":
            raise HTTPException(status_code=404, detail="Customer not found")
        raise HTTPException(status_code=503, detail="Customer history is unavailable")
    return data


# ──────────────────────────────────────────────
#  Ticket Endpoints
# ──────────────────────────────────────────────

@router.get("/tickets")
def list_tickets(limit: int = 50, offset: int = 0, status: TicketStatus = None, _: bool = Depends(verify_agent_token)):
    """Get all tickets with optional status filter."""
    if limit <= 0 or offset < 0:
        raise HTTPException(status_code=400, detail="Invalid pagination parameters")
    result = get_all_tickets(limit=limit, offset=offset, status=status.value if status else None)
    return json.loads(result)


@router.get("/tickets/{ticket_id}")
def get_ticket_detail(ticket_id: int, auth_user: dict = Depends(verify_any_token)):
    """Get details of a specific ticket."""
    result = get_ticket(ticket_id)
    data = json.loads(result)
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
        
    # Enforce ownership: only the ticket owner or an agent can view it
    if auth_user.get("role") == "customer" and data.get("customer_id") != auth_user.get("id"):
        raise HTTPException(status_code=403, detail="Not authorized to access this ticket")
        
    return data


@router.post("/tickets", status_code=201)
def create_new_ticket(
    ticket: TicketCreate, 
    _: bool = Depends(verify_agent_token),
    client_request_id: uuid.UUID = Header(..., alias="X-Client-Request-Id")
):
    """Create a new support ticket."""
    result = create_ticket(
        customer_id=ticket.customer_id,
        subject=ticket.subject,
        description=ticket.description,
        ticket_type=ticket.type.value,
        priority=ticket.priority.value,
        channel=ticket.channel,
        client_request_id=str(client_request_id),
    )
    data = json.loads(result)
    if "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])

    return data


@router.patch("/tickets/{ticket_id}")
@router.put("/tickets/{ticket_id}")
def update_existing_ticket(
    ticket_id: int, 
    update: TicketUpdate, 
    _: bool = Depends(verify_agent_token),
    client_request_id: uuid.UUID = Header(..., alias="X-Client-Request-Id")
):
    """Update a ticket's status, priority, resolution, or assigned agent."""
    result = update_ticket(
        ticket_id=ticket_id,
        status=update.status.value if update.status else None,
        priority=update.priority.value if update.priority else None,
        resolution=update.resolution,
        assigned_agent=update.assigned_agent,
        satisfaction_rating=update.satisfaction_rating,
        client_request_id=str(client_request_id),
    )
    data = json.loads(result)
    if "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])

    return data


# ──────────────────────────────────────────────
#  Order Endpoints
# ──────────────────────────────────────────────

@router.get("/orders/{order_id}/track")
def track_order_status(order_id: int, authenticated_customer_id: int = Depends(verify_customer_token)):
    """Track the status of a specific order."""
    result = track_order(order_id)
    data = json.loads(result)
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
        
    # Enforce ownership
    if data.get("customer_id") != authenticated_customer_id:
        raise HTTPException(status_code=403, detail="Not authorized to track this order")
        
    return data


@router.post("/orders/{order_id}/cancel")
def cancel_order_endpoint(
    order_id: int, 
    _: bool = Depends(verify_agent_token),
    client_request_id: uuid.UUID = Header(..., alias="X-Client-Request-Id")
):
    """Cancel an active order."""
    result = cancel_order(order_id, client_request_id=str(client_request_id))
    data = json.loads(result)
    if "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])
    return data


@router.post("/orders/{order_id}/refund")
def refund_order_endpoint(
    order_id: int, 
    _: bool = Depends(verify_agent_token),
    client_request_id: uuid.UUID = Header(..., alias="X-Client-Request-Id")
):
    """Process a refund for an order."""
    result = process_refund(order_id, client_request_id=str(client_request_id))
    data = json.loads(result)
    if "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])
    return data


# ──────────────────────────────────────────────
#  Product Endpoints
# ──────────────────────────────────────────────

@router.get("/products/search")
@router.get("/products/inventory")
def search_products(q: str):
    """Search for products by name."""
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Search query cannot be empty")
    result = check_inventory(q)
    data = json.loads(result)
    if isinstance(data, dict) and "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])
    return data


# ──────────────────────────────────────────────
#  Dashboard / Analytics Endpoints
# ──────────────────────────────────────────────

@router.get("/dashboard/stats")
def dashboard_stats(_: bool = Depends(verify_agent_token)):
    """Get aggregated dashboard statistics."""
    data = json.loads(get_dashboard_stats())
    if "error" in data:
        raise HTTPException(status_code=503, detail="Dashboard statistics are unavailable")
    return data


@router.get("/dashboard/sessions")
async def active_sessions(_: bool = Depends(verify_agent_token)):
    """Get a list of all active chat sessions."""
    from app.websocket.chat_handler import get_all_active_sessions
    all_sessions = await get_all_active_sessions()
    
    sessions = []
    for session in all_sessions:
        sid = session.get("session_id")
        history = session.get("conversation_history", [])
        last_msg = history[-1]["content"] if history else ""
        workflow = session.get("workflow_state") or {}
        outcome = session.get("workflow_outcome") or {}
        sessions.append({
            "session_id": sid,
            "customer_id": session.get("customer_id"),
            "message_count": len(history),
            "last_message": last_msg[:100],
            "created_at": session.get("created_at"),
            "mode": session.get("mode", "ai"),
            "escalated": session.get("escalated", False),
            # These are workflow facts only; support operators do not need the
            # full customer message or tool payload to triage a live session.
            "domain": outcome.get("domain") or workflow.get("active_domain"),
            "workflow_status": workflow.get("global_status"),
            "pending_approval": bool(session.get("pending_approval")),
            "guardian_action": outcome.get("guardian_action"),
            "guardian_reason": outcome.get("guardian_reason"),
        })
    return {"active_sessions": sessions, "total": len(sessions)}


# ──────────────────────────────────────────────
#  Admin / Tech Dashboard Endpoints
# ──────────────────────────────────────────────

@router.get("/admin/health")
def admin_health_check(_: bool = Depends(verify_operator_access)):
    """Aggregated health check for all services."""
    llm = check_llm_health()
    vectordb = check_vectordb_health()
    supabase_h = check_supabase_health()

    all_healthy = all(
        s.get("status") == "healthy"
        for s in [llm, vectordb, supabase_h]
    )

    return {
        "overall": "healthy" if all_healthy else "degraded",
        "services": {
            "llm": llm,
            "vectordb": vectordb,
            "supabase": supabase_h,
            "fastapi": {"status": "healthy"},
        },
    }


@router.get("/admin/system-info")
def admin_system_info(_: bool = Depends(verify_operator_access)):
    """Get system configuration and version info."""
    return get_system_info()


@router.get("/admin/knowledge-base")
@router.get("/admin/knowledge")
def admin_list_kb(_: bool = Depends(verify_admin_key)):
    """List all knowledge base documents."""
    docs = list_knowledge_base_docs()
    return {"documents": docs, "total": len(docs)}


@router.get("/admin/knowledge-base/{filename}")
def admin_get_kb_doc(filename: str, _: bool = Depends(verify_admin_key)):
    """Read a specific knowledge base document."""
    content = get_knowledge_base_doc(filename)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Document '{filename}' not found")
    return {"filename": filename, "content": content}


@router.post("/admin/knowledge-base/reingest")
async def admin_reingest_kb(_: bool = Depends(verify_admin_key)):
    """Trigger re-ingestion of knowledge base into Pinecone/Chroma."""
    try:
        from app.rag.retriever import ingest_knowledge_base
    except ImportError:
        import subprocess
        import sys

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "scripts.ingest_knowledge"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(status_code=503, detail="Knowledge base ingestion failed") from exc
        if result.returncode != 0:
            raise HTTPException(status_code=503, detail="Knowledge base ingestion failed. Check the knowledge directory and backend logs.")
        return {"status": "success", "stdout": (result.stdout or "")[-500:], "stderr": (result.stderr or "")[-500:]}

    try:
        result = await asyncio.to_thread(ingest_knowledge_base)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=422, detail="No ingestible knowledge base documents were found") from exc
    return {"status": "success", "message": "Knowledge base re-ingested", "details": result}


@router.get("/admin/logs")
def admin_get_logs(level: str = None, search: str = None, limit: int = 100, _: bool = Depends(verify_admin_key)):
    """Get recent activity from the production JSON log file with optional filtering."""
    if limit <= 0:
        raise HTTPException(status_code=400, detail="Limit must be a positive integer")
    logs = get_recent_logs(lines=limit, level_filter=level, search=search)
    return {"logs": logs, "total": len(logs)}


from app.middleware.tracking import get_aggregate_metrics

@router.get("/admin/metrics")
async def admin_get_metrics(_: bool = Depends(verify_operator_access)):
    """Get aggregate LLM, tool, and guardrail metrics for the admin dashboard."""
    return get_aggregate_metrics()


from app.websocket.connection import get_connection_metrics

@router.get("/admin/connections")
async def admin_get_connection_metrics(_: bool = Depends(verify_operator_access)):
    """Get WebSocket connection metrics."""
    return get_connection_metrics()

from pydantic import BaseModel, Field
from app.llm_factory import get_dynamic_settings, SETTINGS_FILE

class LLMSettingsUpdate(BaseModel):
    model_config = {"str_strip_whitespace": True}
    model: str = Field(default="gemini-3.8-flash", min_length=1)

@router.get("/admin/llm-settings")
@router.get("/admin/settings")
def admin_get_llm_settings(_: bool = Depends(verify_admin_key)):
    """Get the current dynamic LLM settings."""
    return get_dynamic_settings()

@router.post("/admin/llm-settings")
def admin_update_llm_settings(settings_update: LLMSettingsUpdate, _: bool = Depends(verify_admin_key)):
    """Update the dynamic LLM settings."""
    new_settings = settings_update.model_dump()
    temp_path = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=Path(SETTINGS_FILE).parent, delete=False) as file:
            temp_path = Path(file.name)
            json.dump(new_settings, file, indent=4)
        os.replace(temp_path, SETTINGS_FILE)
        return {"status": "success", "settings": new_settings}
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Failed to save settings") from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

