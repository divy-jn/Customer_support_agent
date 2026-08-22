"""
Database MCP Server — exposes Supabase operations as MCP tools.

This server provides secure, structured access to the customer support
database for the LangGraph agents using the Supabase REST API.

Usage:
    cd backend
    python -m app.mcp.db_server
"""

import json
import os
import sys

from mcp.server.fastmcp import FastMCP
from supabase import create_client, Client

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.config import settings

# ──────────────────────────────────────────────
#  MCP Server Setup
# ──────────────────────────────────────────────
mcp = FastMCP(
    "CustomerSupportDB",
    description="MCP server providing secure access to the customer support Supabase database.",
)

# Initialize Supabase client
supabase: Client = create_client(settings.supabase_url, settings.supabase_key)


# ──────────────────────────────────────────────
#  MCP Tools — Customer Operations
# ──────────────────────────────────────────────

@mcp.tool()
def lookup_customer(identifier: str) -> str:
    """
    Look up a customer by name or email address.
    Returns the customer profile including their ID, name, email, age, and gender.
    Use this when a customer asks about their account or you need to identify them.

    Args:
        identifier: Customer name or email address to search for.
    """
    try:
        # Search by email first
        response = supabase.table("customers").select("*").ilike("email", f"%{identifier}%").limit(5).execute()
        
        # If no results, search by name
        if not response.data:
            response = supabase.table("customers").select("*").ilike("name", f"%{identifier}%").limit(5).execute()

        if not response.data:
            return json.dumps({"error": f"No customer found matching '{identifier}'"})
        
        return json.dumps(response.data, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_customer_history(customer_id: int) -> str:
    """
    Get a customer's complete history: their orders, tickets, and satisfaction ratings.
    Use this to understand a returning customer's past interactions.

    Args:
        customer_id: The numeric ID of the customer.
    """
    try:
        # Get orders (with product info via Supabase joined tables)
        # Note: Depending on Supabase relationships, we can do nested selects.
        orders_resp = supabase.table("orders").select("*, products(name, category)").eq("customer_id", customer_id).order("order_date", desc=True).limit(10).execute()
        
        # Get tickets
        tickets_resp = supabase.table("tickets").select("id, subject, type, status, priority, channel, satisfaction_rating, created_at").eq("customer_id", customer_id).order("created_at", desc=True).limit(10).execute()

        return json.dumps({
            "customer_id": customer_id,
            "recent_orders": orders_resp.data,
            "recent_tickets": tickets_resp.data,
        }, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ──────────────────────────────────────────────
#  MCP Tools — Ticket Operations
# ──────────────────────────────────────────────

@mcp.tool()
def get_ticket(ticket_id: int) -> str:
    """
    Get detailed information about a specific support ticket.

    Args:
        ticket_id: The numeric ID of the ticket.
    """
    try:
        response = supabase.table("tickets").select("*, customers(name, email)").eq("id", ticket_id).execute()
        
        if not response.data:
            return json.dumps({"error": f"Ticket #{ticket_id} not found"})
        
        return json.dumps(response.data[0], indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def create_ticket(
    customer_id: int,
    subject: str,
    description: str,
    ticket_type: str = "inquiry",
    priority: str = "medium",
    channel: str = "chat",
) -> str:
    """
    Create a new support ticket for a customer.

    Args:
        customer_id: The numeric ID of the customer.
        subject: Brief subject/title of the issue.
        description: Detailed description of the problem.
        ticket_type: Type of ticket: technical_issue, billing, refund, cancellation, or inquiry.
        priority: Priority level: low, medium, high, or critical.
        channel: Communication channel: chat, email, phone, or social_media.
    """
    try:
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


@mcp.tool()
def update_ticket(
    ticket_id: int,
    status: str = None,
    priority: str = None,
    resolution: str = None,
    assigned_agent: str = None,
) -> str:
    """
    Update an existing support ticket's status, priority, resolution, or assigned agent.

    Args:
        ticket_id: The numeric ID of the ticket to update.
        status: New status: open, in_progress, pending_customer, escalated, or closed.
        priority: New priority: low, medium, high, or critical.
        resolution: Resolution notes when closing a ticket.
        assigned_agent: Name of the agent to assign the ticket to.
    """
    updates = {}
    if status:
        updates["status"] = status
    if priority:
        updates["priority"] = priority
    if resolution:
        updates["resolution"] = resolution
    if assigned_agent:
        updates["assigned_agent"] = assigned_agent

    if not updates:
        return json.dumps({"error": "No fields to update"})

    try:
        response = supabase.table("tickets").update(updates).eq("id", ticket_id).execute()
        if not response.data:
            return json.dumps({"error": f"Ticket #{ticket_id} not found or update failed"})
            
        return json.dumps({"status": "success", "message": f"Ticket #{ticket_id} updated"})
    except Exception as e:
        return json.dumps({"error": str(e)})


# ──────────────────────────────────────────────
#  MCP Tools — Order Operations
# ──────────────────────────────────────────────

@mcp.tool()
def track_order(order_id: int) -> str:
    """
    Track the current status and delivery information of an order.

    Args:
        order_id: The numeric ID of the order.
    """
    try:
        response = supabase.table("orders").select("*, products(name, category, price), customers(name)").eq("id", order_id).execute()
        
        if not response.data:
            return json.dumps({"error": f"Order #{order_id} not found"})
            
        return json.dumps(response.data[0], indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def cancel_order(order_id: int) -> str:
    """
    Cancel an active order. Only orders with status 'active' can be cancelled.

    Args:
        order_id: The numeric ID of the order to cancel.
    """
    try:
        # Check current status
        response = supabase.table("orders").select("status").eq("id", order_id).execute()
        
        if not response.data:
            return json.dumps({"error": f"Order #{order_id} not found"})

        if response.data[0]["status"] != "active":
            return json.dumps({
                "error": f"Cannot cancel order #{order_id}. Current status: {response.data[0]['status']}. Only active orders can be cancelled."
            })

        update_resp = supabase.table("orders").update({"status": "cancelled"}).eq("id", order_id).execute()
        return json.dumps({"status": "success", "message": f"Order #{order_id} has been cancelled"})
    except Exception as e:
        return json.dumps({"error": str(e)})


@mcp.tool()
def process_refund(order_id: int) -> str:
    """
    Initiate a refund for an order. The order must be in 'cancelled' or 'delivered' status.

    Args:
        order_id: The numeric ID of the order to refund.
    """
    try:
        response = supabase.table("orders").select("status").eq("id", order_id).execute()
        
        if not response.data:
            return json.dumps({"error": f"Order #{order_id} not found"})

        if response.data[0]["status"] not in ("cancelled", "delivered"):
            return json.dumps({
                "error": f"Cannot refund order #{order_id}. Current status: {response.data[0]['status']}. Order must be cancelled or delivered."
            })

        update_resp = supabase.table("orders").update({"status": "refunded"}).eq("id", order_id).execute()
        return json.dumps({
            "status": "success",
            "message": f"Refund initiated for order #{order_id}. The refund will be processed within 5-7 business days."
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# ──────────────────────────────────────────────
#  MCP Tools — Product / Inventory
# ──────────────────────────────────────────────

@mcp.tool()
def check_inventory(product_name: str) -> str:
    """
    Check the current stock availability for a product.

    Args:
        product_name: The name (or partial name) of the product to check.
    """
    try:
        response = supabase.table("products").select("id, name, category, price, stock, description").ilike("name", f"%{product_name}%").execute()
        
        if not response.data:
            return json.dumps({"error": f"No product found matching '{product_name}'"})

        results = response.data
        for r in results:
            r["in_stock"] = r["stock"] > 0

        return json.dumps(results, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ──────────────────────────────────────────────
#  MCP Tools — Dashboard / Analytics
# ──────────────────────────────────────────────

@mcp.tool()
def get_dashboard_stats() -> str:
    """
    Get aggregated statistics for the agent dashboard: total tickets,
    open tickets, escalated count, and breakdowns by priority, type, and status.
    """
    try:
        # Note: Supabase REST API doesn't support complex aggregations directly out of the box 
        # as easily as raw SQL, so we fetch the data and aggregate in Python for simplicity, 
        # or use exact counts. For a real dashboard, we'd use a Supabase RPC (stored procedure).
        # We will fetch a simple summary for now.
        
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
#  Run Server
# ──────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="stdio")
