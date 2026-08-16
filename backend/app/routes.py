"""
REST API Routes — provides HTTP endpoints for the frontend
to interact with tickets, customers, and dashboard data.
"""

from fastapi import APIRouter, HTTPException
import json

from app.tools import (
    lookup_customer, get_customer_history, get_ticket, create_ticket,
    update_ticket, track_order, cancel_order, process_refund,
    check_inventory, get_dashboard_stats, get_all_customers, get_all_tickets,
    check_llm_health, check_vectordb_health, check_supabase_health,
    get_system_info, list_knowledge_base_docs, get_knowledge_base_doc,
)
from app.models import TicketCreate, TicketUpdate, TicketResponse, TicketStatus
from app.websocket.chat_handler import session_store
from app.email_service import send_ticket_created_email, send_resolution_email

router = APIRouter()


# ──────────────────────────────────────────────
#  Customer Endpoints
# ──────────────────────────────────────────────

@router.get("/customers/search")
async def search_customer(q: str):
    """Search for a customer by name or email."""
    if not q or not q.strip():
        raise HTTPException(status_code=400, detail="Search query cannot be empty")
    result = lookup_customer(q)
    data = json.loads(result)
    if isinstance(data, dict) and "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])
    return data


@router.get("/customers")
async def list_customers(limit: int = 50, offset: int = 0):
    """Get all customers with pagination."""
    if limit <= 0 or offset < 0:
        raise HTTPException(status_code=400, detail="Invalid pagination parameters")
    result = get_all_customers(limit=limit, offset=offset)
    return json.loads(result)


@router.get("/customers/{customer_id}/history")
async def customer_history(customer_id: int):
    """Get a customer's order and ticket history."""
    result = get_customer_history(customer_id)
    return json.loads(result)


# ──────────────────────────────────────────────
#  Ticket Endpoints
# ──────────────────────────────────────────────

@router.get("/tickets")
async def list_tickets(limit: int = 50, offset: int = 0, status: TicketStatus = None):
    """Get all tickets with optional status filter."""
    if limit <= 0 or offset < 0:
        raise HTTPException(status_code=400, detail="Invalid pagination parameters")
    result = get_all_tickets(limit=limit, offset=offset, status=status.value if status else None)
    return json.loads(result)


@router.get("/tickets/{ticket_id}")
async def get_ticket_detail(ticket_id: int):
    """Get details of a specific ticket."""
    result = get_ticket(ticket_id)
    data = json.loads(result)
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    return data


@router.post("/tickets", status_code=201)
async def create_new_ticket(ticket: TicketCreate):
    """Create a new support ticket."""
    result = create_ticket(
        customer_id=ticket.customer_id,
        subject=ticket.subject,
        description=ticket.description,
        ticket_type=ticket.type.value,
        priority=ticket.priority.value,
        channel=ticket.channel,
    )
    data = json.loads(result)
    if "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])

    # Send email notification on ticket creation
    if data.get("status") == "success" and data.get("ticket_id"):
        try:
            from app.tools import get_customer_by_id
            customer_result = get_customer_by_id(ticket.customer_id)
            customer_data = json.loads(customer_result)
            if isinstance(customer_data, list) and customer_data:
                c = customer_data[0]
                send_ticket_created_email(
                    customer_email=c.get("email", ""),
                    customer_name=c.get("name", "Customer"),
                    ticket_id=data["ticket_id"],
                    subject=ticket.subject,
                    description=ticket.description,
                    priority=ticket.priority.value,
                )
        except Exception:
            pass  # Don't fail ticket creation if email fails

    return data


@router.patch("/tickets/{ticket_id}")
@router.put("/tickets/{ticket_id}")
async def update_existing_ticket(ticket_id: int, update: TicketUpdate):
    """Update a ticket's status, priority, resolution, or assigned agent."""
    result = update_ticket(
        ticket_id=ticket_id,
        status=update.status.value if update.status else None,
        priority=update.priority.value if update.priority else None,
        resolution=update.resolution,
        assigned_agent=update.assigned_agent,
        satisfaction_rating=update.satisfaction_rating,
    )
    data = json.loads(result)
    if "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])

    # Send resolution email when ticket is closed
    if update.status and update.status.value == "closed":
        try:
            ticket_result = get_ticket(ticket_id)
            ticket_data = json.loads(ticket_result)
            if ticket_data.get("customers"):
                c = ticket_data["customers"]
                send_resolution_email(
                    customer_email=c.get("email", ""),
                    customer_name=c.get("name", "Customer"),
                    ticket_id=ticket_id,
                    subject=ticket_data.get("subject", ""),
                    resolution=update.resolution or ticket_data.get("resolution", ""),
                )
        except Exception:
            pass

    return data


# ──────────────────────────────────────────────
#  Order Endpoints
# ──────────────────────────────────────────────

@router.get("/orders/{order_id}/track")
async def track_order_status(order_id: int):
    """Track the status of a specific order."""
    result = track_order(order_id)
    data = json.loads(result)
    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])
    return data


@router.post("/orders/{order_id}/cancel")
async def cancel_order_endpoint(order_id: int):
    """Cancel an active order."""
    result = cancel_order(order_id)
    data = json.loads(result)
    if "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])
    return data


@router.post("/orders/{order_id}/refund")
async def refund_order_endpoint(order_id: int):
    """Process a refund for an order."""
    result = process_refund(order_id)
    data = json.loads(result)
    if "error" in data:
        raise HTTPException(status_code=400, detail=data["error"])
    return data


# ──────────────────────────────────────────────
#  Product Endpoints
# ──────────────────────────────────────────────

@router.get("/products/search")
@router.get("/products/inventory")
async def search_products(q: str):
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
async def dashboard_stats():
    """Get aggregated dashboard statistics."""
    result = get_dashboard_stats()
    return json.loads(result)


@router.get("/dashboard/sessions")
async def active_sessions():
    """Get a list of all active chat sessions."""
    sessions = []
    for sid, session in list(session_store.items()):
        history = session.get("conversation_history", [])
        last_msg = history[-1]["content"] if history else ""
        sessions.append({
            "session_id": sid,
            "customer_id": session.get("customer_id"),
            "message_count": len(history),
            "last_message": last_msg[:100],
            "created_at": session.get("created_at"),
        })
    return {"active_sessions": sessions, "total": len(sessions)}


# ──────────────────────────────────────────────
#  Admin / Tech Dashboard Endpoints
# ──────────────────────────────────────────────

@router.get("/admin/health")
async def admin_health_check():
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
async def admin_system_info():
    """Get system configuration and version info."""
    return get_system_info()


@router.get("/admin/knowledge-base")
@router.get("/admin/knowledge")
async def admin_list_kb():
    """List all knowledge base documents."""
    docs = list_knowledge_base_docs()
    return {"documents": docs, "total": len(docs)}


@router.get("/admin/knowledge-base/{filename}")
async def admin_get_kb_doc(filename: str):
    """Read a specific knowledge base document."""
    content = get_knowledge_base_doc(filename)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Document '{filename}' not found")
    return {"filename": filename, "content": content}


@router.post("/admin/knowledge-base/reingest")
async def admin_reingest_kb():
    """Trigger re-ingestion of knowledge base into ChromaDB."""
    try:
        from app.rag.retriever import ingest_knowledge_base
        result = ingest_knowledge_base()
        return {"status": "success", "message": "Knowledge base re-ingested", "details": result}
    except ImportError:
        # Fallback: run the script directly
        import subprocess
        result = subprocess.run(
            ["python", "-m", "scripts.ingest_knowledge"],
            capture_output=True, text=True, timeout=120,
        )
        return {
            "status": "success" if result.returncode == 0 else "error",
            "stdout": result.stdout[-500:] if result.stdout else "",
            "stderr": result.stderr[-500:] if result.stderr else "",
        }


from app.logger import get_recent_logs

@router.get("/admin/logs")
async def admin_get_logs(level: str = None, search: str = None, limit: int = 100):
    """Get recent activity from the production JSON log file with optional filtering."""
    if limit <= 0:
        raise HTTPException(status_code=400, detail="Limit must be a positive integer")
    logs = get_recent_logs(lines=limit, level_filter=level, search=search)
    return {"logs": logs, "total": len(logs)}


from app.middleware.tracking import get_aggregate_metrics

@router.get("/admin/metrics")
async def admin_get_metrics():
    """Get aggregate LLM, tool, and guardrail metrics for the admin dashboard."""
    return get_aggregate_metrics()


from app.websocket.connection import get_connection_metrics

@router.get("/admin/connections")
async def admin_get_connection_metrics():
    """Get WebSocket connection metrics."""
    return get_connection_metrics()

from pydantic import BaseModel
from app.llm_factory import get_dynamic_settings, SETTINGS_FILE

class LLMSettingsUpdate(BaseModel):
    llm_base_url: str
    small_model: str
    large_model: str

@router.get("/admin/llm-settings")
@router.get("/admin/settings")
async def admin_get_llm_settings():
    """Get the current dynamic LLM settings."""
    return get_dynamic_settings()

@router.post("/admin/llm-settings")
async def admin_update_llm_settings(settings_update: LLMSettingsUpdate):
    """Update the dynamic LLM settings."""
    new_settings = settings_update.model_dump()
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(new_settings, f, indent=4)
        return {"status": "success", "settings": new_settings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save settings: {str(e)}")

