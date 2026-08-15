"""
Database Tools — Supabase operations that power both the MCP server and the FastAPI routes.

All tools are plain Python functions. They are used:
1. Directly by LangGraph graph nodes and REST API routes (in-process).
2. Exposed via MCP by db_server.py (as an MCP server process).
"""

import json
import os
import sys
import platform
from datetime import datetime, timezone

from supabase import create_client, Client
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from app.config import settings
from app.middleware.tracking import track_tool_call

# ──────────────────────────────────────────────
#  Supabase Client
# ──────────────────────────────────────────────
supabase: Client = create_client(settings.supabase_url, settings.supabase_key)


# ──────────────────────────────────────────────
#  Retry decorator for Supabase calls
# ──────────────────────────────────────────────
_supabase_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    reraise=True,
)


# ──────────────────────────────────────────────
#  Input Validators
# ──────────────────────────────────────────────
def _validate_positive_int(value: int, name: str) -> int:
    """Ensure a parameter is a positive integer."""
    if not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer, got: {value}")
    return value


def _validate_non_empty_str(value: str, name: str) -> str:
    """Ensure a parameter is a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


# ──────────────────────────────────────────────
#  Customer Operations
# ──────────────────────────────────────────────

@track_tool_call("lookup_customer")
@_supabase_retry
def lookup_customer(identifier: str) -> str:
    """Look up a customer by name or email address."""
    try:
        identifier = _validate_non_empty_str(identifier, "identifier")
        response = supabase.table("customers").select("*").ilike("email", f"%{identifier}%").limit(5).execute()
        if not response.data:
            response = supabase.table("customers").select("*").ilike("name", f"%{identifier}%").limit(5).execute()
        if not response.data:
            return json.dumps([])
        return json.dumps(response.data, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@track_tool_call("get_customer_by_id")
@_supabase_retry
def get_customer_by_id(customer_id: int) -> str:
    """Look up a customer strictly by their ID."""
    try:
        _validate_positive_int(customer_id, "customer_id")
        response = supabase.table("customers").select("*").eq("id", customer_id).execute()
        if not response.data:
            return json.dumps({"error": f"Customer #{customer_id} not found"})
        return json.dumps(response.data, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@track_tool_call("get_customer_history")
@_supabase_retry
def get_customer_history(customer_id: int) -> str:
    """Get a customer's complete order and ticket history."""
    try:
        _validate_positive_int(customer_id, "customer_id")
        customer_resp = supabase.table("customers").select("id").eq("id", customer_id).execute()
        if not customer_resp.data:
            return json.dumps({"error": f"Customer #{customer_id} not found"})
        orders_resp = supabase.table("orders").select("*, products(name, category)").eq("customer_id", customer_id).order("order_date", desc=True).limit(10).execute()
        tickets_resp = supabase.table("tickets").select("id, subject, type, status, priority, channel, satisfaction_rating, created_at").eq("customer_id", customer_id).order("created_at", desc=True).limit(10).execute()
        return json.dumps({
            "customer_id": customer_id,
            "recent_orders": orders_resp.data,
            "recent_tickets": tickets_resp.data,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ──────────────────────────────────────────────
#  Ticket Operations
# ──────────────────────────────────────────────

@track_tool_call("get_ticket")
@_supabase_retry
def get_ticket(ticket_id: int) -> str:
    """Get detailed information about a specific support ticket."""
    try:
        _validate_positive_int(ticket_id, "ticket_id")
        response = supabase.table("tickets").select("*, customers(name, email)").eq("id", ticket_id).execute()
        if not response.data:
            return json.dumps({"error": f"Ticket #{ticket_id} not found"})
        return json.dumps(response.data[0], indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@track_tool_call("create_ticket")
@_supabase_retry
def create_ticket(
    customer_id: int,
    subject: str,
    description: str,
    ticket_type: str = "inquiry",
    priority: str = "medium",
    channel: str = "chat",
) -> str:
    """Create a new support ticket for a customer."""
    try:
        _validate_positive_int(customer_id, "customer_id")
        subject = _validate_non_empty_str(subject, "subject")
        description = _validate_non_empty_str(description, "description")
        data = {
            "customer_id": customer_id,
            "subject": subject,
            "description": description,
            "type": ticket_type,
            "status": "open",
            "priority": priority,
            "channel": channel,
        }
        response = supabase.table("tickets").insert(data).execute()
        if not response.data:
            return json.dumps({"error": "Failed to create ticket"})
        ticket_id = response.data[0]["id"]
        return json.dumps({
            "status": "success",
            "message": f"Ticket #{ticket_id} created successfully",
            "ticket_id": ticket_id,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


@track_tool_call("update_ticket")
@_supabase_retry
def update_ticket(
    ticket_id: int,
    status: str = None,
    priority: str = None,
    resolution: str = None,
    assigned_agent: str = None,
    satisfaction_rating: int = None,
) -> str:
    """Update an existing support ticket."""
    try:
        _validate_positive_int(ticket_id, "ticket_id")
        updates = {}
        if status:
            updates["status"] = status
        if priority:
            updates["priority"] = priority
        if resolution:
            updates["resolution"] = resolution
        if assigned_agent:
            updates["assigned_agent"] = assigned_agent
        if satisfaction_rating is not None:
            updates["satisfaction_rating"] = satisfaction_rating
        if not updates:
            return json.dumps({"error": "No fields to update"})
        response = supabase.table("tickets").update(updates).eq("id", ticket_id).execute()
        if not response.data:
            return json.dumps({"error": f"Ticket #{ticket_id} not found or update failed"})
        return json.dumps({"status": "success", "message": f"Ticket #{ticket_id} updated"})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ──────────────────────────────────────────────
#  Order Operations
# ──────────────────────────────────────────────

@track_tool_call("track_order")
@_supabase_retry
def track_order(order_id: int) -> str:
    """Track the current status and delivery information of an order."""
    try:
        _validate_positive_int(order_id, "order_id")
        response = supabase.table("orders").select("*, products(name, category, price), customers(name)").eq("id", order_id).execute()
        if not response.data:
            return json.dumps({"error": f"Order #{order_id} not found"})
        return json.dumps(response.data[0], indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@track_tool_call("cancel_order")
@_supabase_retry
def cancel_order(order_id: int) -> str:
    """Cancel an active order. Only orders with status 'active' can be cancelled."""
    try:
        _validate_positive_int(order_id, "order_id")
        response = supabase.table("orders").select("status").eq("id", order_id).execute()
        if not response.data:
            return json.dumps({"error": f"Order #{order_id} not found"})
        if response.data[0]["status"] != "active":
            return json.dumps({
                "error": f"Cannot cancel order #{order_id}. Current status: {response.data[0]['status']}. Only active orders can be cancelled."
            })
        supabase.table("orders").update({"status": "cancelled"}).eq("id", order_id).execute()
        return json.dumps({"status": "success", "message": f"Order #{order_id} has been cancelled"})
    except Exception as e:
        return json.dumps({"error": str(e)})


@track_tool_call("process_refund")
@_supabase_retry
def process_refund(order_id: int) -> str:
    """Initiate a refund for an order. Must be cancelled or delivered."""
    try:
        _validate_positive_int(order_id, "order_id")
        response = supabase.table("orders").select("status").eq("id", order_id).execute()
        if not response.data:
            return json.dumps({"error": f"Order #{order_id} not found"})
        if response.data[0]["status"] not in ("cancelled", "delivered"):
            return json.dumps({
                "error": f"Cannot refund order #{order_id}. Current status: {response.data[0]['status']}. Order must be cancelled or delivered."
            })
        supabase.table("orders").update({"status": "refunded"}).eq("id", order_id).execute()
        return json.dumps({
            "status": "success",
            "message": f"Refund initiated for order #{order_id}. The refund will be processed within 5-7 business days."
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# ──────────────────────────────────────────────
#  Product / Inventory
# ──────────────────────────────────────────────

@track_tool_call("check_inventory")
@_supabase_retry
def check_inventory(product_name: str) -> str:
    """Check stock availability for a product."""
    try:
        product_name = _validate_non_empty_str(product_name, "product_name")
        response = supabase.table("products").select("id, name, category, price, stock, description").ilike("name", f"%{product_name}%").execute()
        if not response.data:
            return json.dumps([])
        results = response.data
        for r in results:
            r["in_stock"] = r["stock"] > 0
        return json.dumps(results, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ──────────────────────────────────────────────
#  Dashboard / Analytics
# ──────────────────────────────────────────────

@track_tool_call("get_dashboard_stats")
@_supabase_retry
def get_dashboard_stats() -> str:
    """Get aggregated dashboard statistics."""
    try:
        all_tickets = supabase.table("tickets").select("id, status, priority, type, satisfaction_rating").execute()
        data = all_tickets.data

        total = len(data)
        open_count = sum(1 for t in data if t["status"] == "open")
        escalated = sum(1 for t in data if t["status"] == "escalated")

        by_priority = {}
        by_type = {}
        by_status = {}
        sat_scores = []

        for t in data:
            by_priority[t["priority"]] = by_priority.get(t["priority"], 0) + 1
            by_type[t["type"]] = by_type.get(t["type"], 0) + 1
            by_status[t["status"]] = by_status.get(t["status"], 0) + 1
            if t.get("satisfaction_rating"):
                sat_scores.append(t["satisfaction_rating"])

        avg_sat = sum(sat_scores) / len(sat_scores) if sat_scores else None

        return json.dumps({
            "total_tickets": total,
            "open_tickets": open_count,
            "escalated_tickets": escalated,
            "avg_satisfaction": round(avg_sat, 2) if avg_sat else None,
            "by_priority": by_priority,
            "by_type": by_type,
            "by_status": by_status,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ──────────────────────────────────────────────
#  Web Search (simple DuckDuckGo)
# ──────────────────────────────────────────────

@track_tool_call("web_search")
def web_search(query: str, num_results: int = 5) -> str:
    """Search the web using DuckDuckGo Instant Answer API."""
    import urllib.request
    import urllib.parse

    try:
        query = _validate_non_empty_str(query, "query")
        num_results = min(num_results, 10)
        encoded_query = urllib.parse.quote(query)
        url = f"https://api.duckduckgo.com/?q={encoded_query}&format=json&no_html=1&skip_disambig=1"
        req = urllib.request.Request(url, headers={"User-Agent": "Adi/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        results = []
        if data.get("AbstractText"):
            results.append({
                "title": data.get("Heading", ""),
                "snippet": data["AbstractText"],
                "source": data.get("AbstractURL", ""),
                "type": "abstract",
            })
        for topic in data.get("RelatedTopics", [])[:num_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": topic.get("Text", "")[:100],
                    "snippet": topic.get("Text", ""),
                    "source": topic.get("FirstURL", ""),
                    "type": "related",
                })
        if not results:
            return json.dumps({
                "message": f"No direct results found for '{query}'.",
                "suggestion": "Try rephrasing the search query.",
            })
        return json.dumps(results[:num_results], indent=2)
    except Exception as e:
        return json.dumps({"error": f"Web search failed: {str(e)}"})


# ──────────────────────────────────────────────
#  Customer Listing
# ──────────────────────────────────────────────

@track_tool_call("get_all_customers")
@_supabase_retry
def get_all_customers(limit: int = 50, offset: int = 0) -> str:
    """Get all customers with pagination."""
    try:
        response = (
            supabase.table("customers")
            .select("*, tickets(id, status), orders(id, status)")
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        customers = []
        for c in response.data:
            tickets = c.pop("tickets", [])
            orders = c.pop("orders", [])
            c["total_tickets"] = len(tickets)
            c["open_tickets"] = sum(1 for t in tickets if t.get("status") == "open")
            c["total_orders"] = len(orders)
            customers.append(c)
        return json.dumps(customers, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@track_tool_call("get_all_tickets")
@_supabase_retry
def get_all_tickets(limit: int = 50, offset: int = 0, status: str = None) -> str:
    """Get all tickets with optional status filter."""
    try:
        query = (
            supabase.table("tickets")
            .select("*, customers(name, email)")
            .order("created_at", desc=True)
        )
        if status:
            query = query.eq("status", status)
        response = query.range(offset, offset + limit - 1).execute()
        return json.dumps(response.data, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ──────────────────────────────────────────────
#  Admin / System Health
# ──────────────────────────────────────────────

def check_llm_health() -> dict:
    """Check if the cloud LLM API is reachable and which models are available."""
    import urllib.request
    from app.llm_factory import get_dynamic_settings
    try:
        dyn = get_dynamic_settings()
        base_url = dyn.get('llm_base_url', settings.llm_base_url).rstrip('/')
        req = urllib.request.Request(
            f"{base_url}/models",
            headers={"User-Agent": "IntelliSupport/1.0", "Authorization": f"Bearer {settings.llm_api_key}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("id", "") for m in data.get("data", [])]
            return {
                "status": "healthy",
                "url": dyn.get("llm_base_url", settings.llm_base_url),
                "models_available": models,
                "small_model": dyn.get("small_model", settings.llm_small_model),
                "large_model": dyn.get("large_model", settings.llm_large_model),
                "small_model_loaded": dyn.get("small_model", settings.llm_small_model) in models,
                "large_model_loaded": dyn.get("large_model", settings.llm_large_model) in models,
            }
    except Exception as e:
        dyn = get_dynamic_settings()
        return {"status": "unhealthy", "error": str(e), "url": dyn.get("llm_base_url", settings.llm_base_url)}


def check_vectordb_health() -> dict:
    """Check if Pinecone is accessible."""
    try:
        from pinecone import Pinecone
        pc = Pinecone(api_key=settings.pinecone_api_key)
        index = pc.Index(settings.pinecone_index_name)
        stats = index.describe_index_stats()
        return {
            "status": "healthy",
            "provider": "pinecone",
            "index": settings.pinecone_index_name,
            "total_vectors": stats.get("total_vector_count", 0),
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


def check_supabase_health() -> dict:
    """Check if Supabase is accessible."""
    try:
        response = supabase.table("customers").select("id").limit(1).execute()
        return {
            "status": "healthy",
            "url": settings.supabase_url,
            "test_query": "OK",
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e), "url": settings.supabase_url}


def get_system_info() -> dict:
    """Get system information for the admin dashboard."""
    return {
        "app_version": "1.0.0",
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "llm_base_url": settings.llm_base_url,
        "llm_small_model": settings.llm_small_model,
        "llm_large_model": settings.llm_large_model,
        "supabase_url": settings.supabase_url,
        "langsmith_tracing": os.environ.get("LANGCHAIN_TRACING_V2", "false"),
        "langsmith_project": os.environ.get("LANGCHAIN_PROJECT", ""),
        "api_host": settings.api_host,
        "api_port": settings.api_port,
        "email_configured": os.path.exists(os.path.join(os.path.dirname(__file__), "..", "credentials.json")),
        "log_level": settings.log_level,
        "debug_mode": settings.debug_mode,
    }


def list_knowledge_base_docs() -> list[dict]:
    """List all knowledge base documents."""
    from pathlib import Path
    kb_path = Path(settings.knowledge_base_dir)
    docs = []
    if kb_path.exists():
        for f in sorted(kb_path.iterdir()):
            if f.is_file() and f.suffix == ".md":
                stat = f.stat()
                docs.append({
                    "filename": f.name,
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
    return docs


def get_knowledge_base_doc(filename: str) -> str | None:
    """Read a knowledge base document's content."""
    from pathlib import Path
    kb_path = Path(settings.knowledge_base_dir) / filename
    if kb_path.exists() and kb_path.suffix == ".md":
        return kb_path.read_text(encoding="utf-8")
    return None
