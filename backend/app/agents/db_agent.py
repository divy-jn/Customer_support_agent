"""
Database / Memory Agent — handles customer-specific operations by
querying the PostgreSQL database via the DB MCP server tools.

Manages: customer lookups, ticket CRUD, order tracking/cancellation/refunds,
and inventory checks.
"""

import json
from app.config import settings
from app.llm_factory import get_llm
from app.middleware.tracking import track_llm_call


DB_AGENT_SYSTEM_PROMPT = """You are a customer support agent with access to the company's database.

You have access to the following database tools (provided as function results):
- lookup_customer: Find a customer by name or email
- get_customer_history: Get a customer's orders and tickets
- get_ticket: Get details of a specific ticket
- create_ticket: Create a new support ticket
- update_ticket: Update a ticket's status/priority/resolution
- track_order: Check order status and tracking info
- cancel_order: Cancel an active order
- process_refund: Initiate a refund for a cancelled/delivered order
- check_inventory: Check product stock availability
- list_all_products: List all available products in the catalog
- get_chat_history: Get past chat conversations for a customer
- send_ticket_email_to_customer: Send an email update to a customer regarding their ticket

Based on the TOOL RESULTS provided, formulate a helpful, professional response to the customer.

Rules:
1. Always reference specific data from the tool results (order numbers, ticket IDs, status, etc.).
2. If a tool returned an error, explain the situation clearly to the customer.
3. Be empathetic and professional.
4. Suggest next steps when appropriate.
5. Never expose internal database IDs or technical details to the customer — use friendly references instead.
6. Always address the customer by their actual name if provided. Never use placeholders like [Customer Name].
7. If the customer asks you to place an order, create an order, or buy an item for them, politely refuse. Explain that you cannot process purchases directly, and suggest they browse the catalog and add items to their cart to checkout."""


def get_db_llm():
    """Get the large LLM for generating database-informed responses."""
    return get_llm(model=settings.llm_large_model, temperature=0.3)


def determine_db_action(message: str, intent: str, customer_id: int = None) -> dict:
    """
    Determine which database action(s) to take based on the customer message and intent.

    Returns a dict with:
        - action: the MCP tool to call
        - params: parameters for the tool
    """
    message_lower = message.lower()

    # Order-related actions
    if intent == "order_tracking" or ("track" in message_lower and "order" in message_lower):
        # Try to extract order ID from message
        order_id = _extract_number(message, "order")
        if order_id:
            return {"action": "track_order", "params": {"order_id": order_id}}
        elif customer_id:
            return {"action": "get_customer_history", "params": {"customer_id": customer_id}}

    if intent == "order_cancellation" or ("cancel" in message_lower and "order" in message_lower):
        order_id = _extract_number(message, "order")
        if order_id:
            return {"action": "cancel_order", "params": {"order_id": order_id}}

    if intent == "refund" or "refund" in message_lower:
        order_id = _extract_number(message, "order")
        if order_id:
            return {"action": "process_refund", "params": {"order_id": order_id}}

    # Ticket-related actions
    if "create" in message_lower and "ticket" in message_lower:
        return {"action": "create_ticket", "params": {"customer_id": customer_id or 1, "subject": "New Inquiry", "description": message}}
    
    if "ticket" in message_lower:
        ticket_id = _extract_number(message, "ticket")
        if ticket_id:
            return {"action": "get_ticket", "params": {"ticket_id": ticket_id}}

    # Product/inventory checks
    if "all products" in message_lower or "list products" in message_lower:
        return {"action": "list_all_products", "params": {}}
        
    if intent == "product_inquiry" or "stock" in message_lower or "available" in message_lower:
        # Extract product name (rough heuristic)
        return {"action": "check_inventory", "params": {"product_name": message}}
        
    # Chat history
    if "chat history" in message_lower or "last chat" in message_lower:
        if customer_id:
            return {"action": "get_chat_history", "params": {"customer_id": customer_id}}

    # Account/customer lookup
    if intent == "account_management" or intent == "billing":
        if customer_id:
            return {"action": "get_customer_history", "params": {"customer_id": customer_id}}
        return {"action": "lookup_customer", "params": {"identifier": message}}

    # Default: try customer history if we have a customer ID
    if customer_id:
        return {"action": "get_customer_history", "params": {"customer_id": customer_id}}

    return {"action": "lookup_customer", "params": {"identifier": message}}


async def generate_response(
    message: str,
    tool_results: str,
    intent: str,
    conversation_history: list[dict] = None,
    customer_name: str = None,
) -> dict:
    """
    Generate a response using database tool results.

    Args:
        message: The customer's message.
        tool_results: JSON string of results from the MCP tool call.
        intent: The classified intent.
        conversation_history: Previous messages.

    Returns:
        Dict with the response text.
    """
    llm = get_db_llm()

    history_text = ""
    if conversation_history:
        recent = conversation_history[-4:]
        for msg in recent:
            role = "Customer" if msg.get("role") == "customer" else "Agent"
            history_text += f"{role}: {msg.get('content', '')}\n"

    user_prompt = f"""Customer's message: "{message}"
Intent classified as: {intent}
{f"Customer Name: {customer_name}" if customer_name else ""}

TOOL RESULTS from database:
{tool_results}

{f"Conversation history:{chr(10)}{history_text}" if history_text else ""}

Based on the tool results above, provide a helpful response to the customer."""

    try:
        with track_llm_call(settings.llm_large_model, "db_node", user_prompt) as tracker:
            response = await llm.ainvoke([
                {"role": "system", "content": DB_AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ])
            tracker["output_text"] = response.content
        return {"response": response.content}
    except Exception as e:
        return {
            "response": "I apologize, but I'm having trouble accessing your account information right now. Let me connect you with a human agent who can assist.",
            "error": str(e),
        }


def _extract_number(text: str, prefix: str) -> int | None:
    """Extract a number following a prefix word (e.g., 'order 1234' -> 1234)."""
    import re
    # Match patterns like "order #1234", "order 1234", "order number 1234"
    patterns = [
        rf'{prefix}\s*#?\s*(\d+)',
        rf'{prefix}\s+number\s*#?\s*(\d+)',
        rf'#(\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None
