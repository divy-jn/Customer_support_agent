"""
LangGraph Orchestration — wires all agent nodes together into a state graph.
All runs are automatically traced to LangSmith when LANGCHAIN_TRACING_V2=true.
"""

import os
from typing import TypedDict, Annotated, Sequence
import operator
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END

# Load .env so LangSmith env vars are available
load_dotenv()

# Import the agents
from app.agents import intent_router, rag_agent, db_agent, web_agent, escalation_agent

# Import tools directly for fast, in-process execution within the graph
from app.tools import (
    lookup_customer, get_customer_history, get_ticket, create_ticket,
    update_ticket, track_order, cancel_order, process_refund, check_inventory,
    web_search,
)


# ──────────────────────────────────────────────
#  State Definition
# ──────────────────────────────────────────────
class AgentState(TypedDict):
    """The state that is passed between nodes in the graph."""
    customer_id: int | None
    session_id: str | None
    message: str
    conversation_history: list[dict]
    
    # Router Outputs
    intent: str
    sentiment: str
    urgency: str
    route_to: str
    
    # Agent Outputs
    tool_results: str | None
    response: str | None
    escalated: bool

    # Approval Gate
    pending_approval: dict | None      # {"action": str, "params": dict, "message": str}
    approval_granted: bool | None      # Set by the WebSocket handler after user responds


# High-risk tools that require customer confirmation
HIGH_RISK_ACTIONS = {"cancel_order", "process_refund"}


# ──────────────────────────────────────────────
#  Node Functions
# ──────────────────────────────────────────────

async def route_intent_node(state: AgentState) -> dict:
    """Node: Classifies intent and decides routing."""
    classification = await intent_router.classify_intent(
        message=state["message"],
        conversation_history=state["conversation_history"]
    )
    return {
        "intent": classification.get("intent", "general"),
        "sentiment": classification.get("sentiment", "neutral"),
        "urgency": classification.get("urgency", "medium"),
        "route_to": classification.get("route_to", "rag_agent"),
    }


async def rag_node(state: AgentState) -> dict:
    """Node: Answers questions using ChromaDB knowledge base."""
    result = await rag_agent.generate_response(
        message=state["message"],
        conversation_history=state["conversation_history"]
    )
    return {
        "response": result["response"],
        "escalated": False
    }


async def db_plan_node(state: AgentState) -> dict:
    """Node: Determines DB action. If high-risk, pauses for approval."""
    action_plan = db_agent.determine_db_action(
        message=state["message"],
        intent=state["intent"],
        customer_id=state["customer_id"]
    )
    
    action = action_plan.get("action")
    params = action_plan.get("params", {})
    
    # Check if this is a high-risk action
    if action in HIGH_RISK_ACTIONS:
        # Build a human-readable confirmation message
        if action == "cancel_order":
            confirm_msg = f"I'd like to cancel order #{params.get('order_id', '?')} for you. Shall I go ahead?"
        elif action == "process_refund":
            confirm_msg = f"I'll process a refund for order #{params.get('order_id', '?')}. Would you like me to proceed?"
        else:
            confirm_msg = f"I need your confirmation to perform: {action}. Proceed?"
        
        return {
            "pending_approval": {
                "action": action,
                "params": params,
                "message": confirm_msg,
            },
            "response": confirm_msg,
            "escalated": False,
        }
    
    # Low-risk action — execute immediately
    tool_results = _execute_tool(action, params)
    
    result = await db_agent.generate_response(
        message=state["message"],
        tool_results=tool_results,
        intent=state["intent"],
        conversation_history=state["conversation_history"]
    )
    
    return {
        "tool_results": tool_results,
        "response": result["response"],
        "escalated": False,
        "pending_approval": None,
    }


async def db_execute_node(state: AgentState) -> dict:
    """Node: Executes a previously approved high-risk action."""
    pending = state.get("pending_approval")
    
    if not pending:
        return {"response": "There's nothing pending to execute.", "escalated": False}
    
    if not state.get("approval_granted"):
        return {
            "response": "No problem! I've cancelled that action. Is there anything else I can help with?",
            "escalated": False,
            "pending_approval": None,
        }
    
    # Execute the approved action
    action = pending["action"]
    params = pending["params"]
    tool_results = _execute_tool(action, params)
    
    result = await db_agent.generate_response(
        message=state["message"],
        tool_results=tool_results,
        intent=state["intent"],
        conversation_history=state["conversation_history"]
    )
    
    return {
        "tool_results": tool_results,
        "response": result["response"],
        "escalated": False,
        "pending_approval": None,
    }


def _execute_tool(action: str, params: dict) -> str:
    """Execute a database tool by name."""
    try:
        if action == "lookup_customer":
            return lookup_customer(**params)
        elif action == "get_customer_history":
            return get_customer_history(**params)
        elif action == "get_ticket":
            return get_ticket(**params)
        elif action == "track_order":
            return track_order(**params)
        elif action == "cancel_order":
            return cancel_order(**params)
        elif action == "process_refund":
            return process_refund(**params)
        elif action == "check_inventory":
            return check_inventory(**params)
        else:
            return f"Error: Unknown DB action '{action}'"
    except Exception as e:
        return f"Tool execution failed: {str(e)}"


async def web_node(state: AgentState) -> dict:
    """Node: Searches the web for external answers."""
    action_plan = web_agent.determine_search_query(state["message"])
    params = action_plan.get("params", {})
    
    try:
        tool_results = web_search(**params)
    except Exception as e:
        tool_results = f"Web search failed: {str(e)}"
        
    result = await web_agent.generate_response(
        message=state["message"],
        tool_results=tool_results,
        conversation_history=state["conversation_history"]
    )
    
    return {
        "tool_results": tool_results,
        "response": result["response"],
        "escalated": False
    }


async def escalation_node(state: AgentState) -> dict:
    """Node: Handles angry customers and human handoffs."""
    result = await escalation_agent.generate_escalation_response(state["message"])
    return {
        "response": result["response"],
        "escalated": True
    }


# ──────────────────────────────────────────────
#  Edge Routing Logic
# ──────────────────────────────────────────────

def route_after_classification(state: AgentState) -> str:
    """Conditional edge function to determine the next node."""
    route = state.get("route_to", "rag_agent")
    
    # Map the router's decision to graph nodes
    if route == "db_agent":
        return "db_plan_node"
    elif route == "web_agent":
        return "web_node"
    elif route == "escalation":
        return "escalation_node"
    
    return "rag_node"


# ──────────────────────────────────────────────
#  Graph Construction
# ──────────────────────────────────────────────

def create_customer_support_graph():
    """Build and compile the LangGraph workflow."""
    workflow = StateGraph(AgentState)
    
    # Add Nodes
    workflow.add_node("intent_router", route_intent_node)
    workflow.add_node("rag_node", rag_node)
    workflow.add_node("db_plan_node", db_plan_node)
    workflow.add_node("db_execute_node", db_execute_node)
    workflow.add_node("web_node", web_node)
    workflow.add_node("escalation_node", escalation_node)
    
    # Set Entry Point
    workflow.set_entry_point("intent_router")
    
    # Add Conditional Edges from Router
    workflow.add_conditional_edges(
        "intent_router",
        route_after_classification,
        {
            "rag_node": "rag_node",
            "db_plan_node": "db_plan_node",
            "web_node": "web_node",
            "escalation_node": "escalation_node"
        }
    )
    
    # db_plan_node always goes to END (response is either the confirmation or the result)
    workflow.add_edge("db_plan_node", END)
    # db_execute_node also goes to END after executing the approved action
    workflow.add_edge("db_execute_node", END)
    
    # Add Edges to END
    workflow.add_edge("rag_node", END)
    workflow.add_edge("web_node", END)
    workflow.add_edge("escalation_node", END)
    
    # Compile
    return workflow.compile()


# Singleton instance of the graph
customer_support_graph = create_customer_support_graph()
