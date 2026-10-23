"""
Database / Memory Agent — handles customer-specific operations by
querying the PostgreSQL database via the DB MCP server tools.

Manages: customer lookups, ticket CRUD, order tracking/cancellation/refunds,
and inventory checks.
"""

import json
from typing import Literal
from pydantic import BaseModel, Field
from app.config import settings
from app.llm_factory import get_llm
from app.middleware.tracking import track_llm_call


from app.skills.base import render_skill_prompt
from app.skills.policy import GLOBAL_SYSTEM_POLICY
from app.skills.registry import DatabaseSkill

DB_AGENT_SYSTEM_PROMPT = GLOBAL_SYSTEM_POLICY + "\n\n" + render_skill_prompt(DatabaseSkill)


def get_db_llm():
    """Get the large LLM for generating database-informed responses."""
    return get_llm(model=settings.llm_model, temperature=0.3)


# Pydantic schema for structured DB action planning
class DBPlannerOutput(BaseModel):
    action: Literal[
        "track_order", "cancel_order", "process_refund", 
        "create_ticket", "get_ticket", "list_all_products", 
        "check_inventory", "get_chat_history", "lookup_customer", 
        "get_customer_history", "missing_argument", "unsupported_action"
    ] = Field(description="The database action to perform.")
    
    order_id: int | None = Field(None, description="The order ID, if required.")
    ticket_id: int | None = Field(None, description="The ticket ID, if required.")
    customer_id: int | None = Field(None, description="The customer ID, if required.")
    product_name: str | None = Field(None, description="The product name, if required.")
    identifier: str | None = Field(None, description="The customer identifier, if required.")
    subject: str | None = Field(None, description="The ticket subject, if required.")
    description: str | None = Field(None, description="The ticket description, if required.")

def determine_db_action(message: str, intent: str, customer_id: int = None) -> dict:
    """
    Determine which database action(s) to take based on the customer message and intent.
    Uses a structured LLM planner.
    """
    llm = get_db_llm()
    # Structured output forces model to map parameters directly to DBPlannerOutput
    # #review: If the LLM provider fails to produce structured output, exception is caught below.
    structured_llm = llm.with_structured_output(DBPlannerOutput, include_raw=False)
    
    prompt = f"""Customer's message: "{message}"
Intent classified as: {intent}
Customer ID available in context: {customer_id}

Determine the correct database action and extract any required arguments based on the message.
If a required argument for the action is missing from the message, select 'missing_argument'.
Do NOT invent IDs.
"""

    try:
        # Track LLM latency and token telemetry
        with track_llm_call(settings.llm_model, "db_plan", prompt) as tracker:
            result = structured_llm.invoke([
                {"role": "system", "content": DB_AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ])
            
        if not result:
            return {"action": "missing_argument", "params": {}}
            
        action = result.action
        params = {}
        
        # Post-parse validation: ensure extracted parameters are actually present
        if action in ["track_order", "cancel_order", "process_refund"]:
            if not result.order_id:
                action = "missing_argument"
            else:
                params["order_id"] = result.order_id
        elif action == "create_ticket":
            if not result.subject or not result.description:
                action = "missing_argument"
            else:
                # #review: Defaulting customer_id to 1 if missing can assign tickets to wrong customer or leak account 1
                params["customer_id"] = result.customer_id or customer_id or 1
                params["subject"] = result.subject
                params["description"] = result.description
        elif action == "get_ticket":
            if not result.ticket_id:
                action = "missing_argument"
            else:
                params["ticket_id"] = result.ticket_id
        elif action == "check_inventory":
            if not result.product_name:
                action = "missing_argument"
            else:
                params["product_name"] = result.product_name
        elif action == "get_chat_history":
            if not (result.customer_id or customer_id):
                action = "missing_argument"
            else:
                params["customer_id"] = result.customer_id or customer_id
        elif action == "lookup_customer":
            if not result.identifier:
                action = "missing_argument"
            else:
                params["identifier"] = result.identifier
        elif action == "get_customer_history":
            if not (result.customer_id or customer_id):
                action = "missing_argument"
            else:
                params["customer_id"] = result.customer_id or customer_id
        elif action == "list_all_products":
            pass
            
        return {"action": action, "params": params}
            
    except Exception as e:
        # #review: Silently swallowing exceptions here hides LLM call failures and returns generic missing_argument
        return {"action": "missing_argument", "params": {}}


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

    # Only include the last 4 messages to save tokens and stay focused on recent context
    history_text = ""
    if conversation_history:
        recent = conversation_history[-4:]
        for msg in recent:
            role = "Customer" if msg.get("role") == "customer" else "Agent"
            history_text += f"{role}: {msg.get('content', '')}\n"

    # Assemble grounding context with DB tool results
    user_prompt = f"""Customer's message: "{message}"
Intent classified as: {intent}
{f"Customer Name: {customer_name}" if customer_name else ""}

TOOL RESULTS from database:
{tool_results}

{f"Conversation history:{chr(10)}{history_text}" if history_text else ""}

Based on the tool results above, provide a helpful response to the customer."""

    try:
        # LLM call with system prompt containing DB skills policy
        with track_llm_call(settings.llm_model, "db_node", user_prompt) as tracker:
            response = await llm.ainvoke([
                {"role": "system", "content": DB_AGENT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ])
            tracker["output_text"] = response.content
        return {"response": response.content}
    except Exception as e:
        # #review: Fallback message triggers human escalation recommendation when DB LLM call fails
        return {
            "response": "I apologize, but I'm having trouble accessing your account information right now. Let me connect you with a human agent who can assist.",
            "error": str(e),
        }



