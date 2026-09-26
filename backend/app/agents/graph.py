"""
LangGraph Orchestration — wires all agent nodes together into a state graph.
All runs are automatically traced to LangSmith when LANGCHAIN_TRACING_V2=true.
"""

import os
from typing import TypedDict, Annotated, Sequence
import operator
import asyncio
from dotenv import load_dotenv
from langgraph.graph import StateGraph, END

# Load .env so LangSmith env vars are available
load_dotenv()

# Import the agents
from app.agents import intent_router, rag_agent, db_agent, web_agent, escalation_agent

from app.tools import (
    lookup_customer, get_customer_history, get_ticket, create_ticket,
    update_ticket, track_order, cancel_order, process_refund, check_inventory,
    web_search, list_all_products, get_chat_history, send_ticket_email_to_customer,
    search_manufacturer_warranty, check_warranty_status
)

from app.skills.registry import SkillRegistry
from app.skills.loader import SkillLoader
from app.skills.runtime import SkillRuntime
from app.agents.product_agent import ProductAgent, ProductSkillResolver, ProductDomainContext
from app.models import WorkflowState
import os
from pathlib import Path

# Setup ProductAgent singletons
product_registry = SkillRegistry()
try:
    _skill_path = Path(__file__).parent.parent / "skills" / "definitions" / "product" / "information" / "SKILL.md"
    if _skill_path.exists():
        product_registry.register(SkillLoader.load_from_file(_skill_path))
    _warranty_path = Path(__file__).parent.parent / "skills" / "definitions" / "product" / "warranty" / "SKILL.md"
    if _warranty_path.exists():
        product_registry.register(SkillLoader.load_from_file(_warranty_path))
except Exception as e:
    import logging
    logging.getLogger(__name__).error(f"Failed to load product skills: {e}")

product_resolver = ProductSkillResolver(product_registry)

class GraphLLMAdapter:
    def __init__(self):
        self._llm = None
        
    @property
    def llm(self):
        if self._llm is None:
            from app.llm_factory import get_llm
            from app.config import settings
            self._llm = get_llm(model=settings.llm_small_model, temperature=0.0)
        return self._llm
        
    async def invoke(self, system: str, user: str) -> str:
        from langchain_core.messages import SystemMessage, HumanMessage
        messages = [SystemMessage(content=system), HumanMessage(content=user)]
        res = await self.llm.ainvoke(messages)
        return res.content

product_llm_adapter = GraphLLMAdapter()


# ──────────────────────────────────────────────
#  State Definition
# ──────────────────────────────────────────────
class AgentState(TypedDict):
    """The state that is passed between nodes in the graph."""
    customer_id: int | None
    customer_name: str | None
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

    # Observability / Diagnostics
    router_error_type: str | None
    router_transport_failure: bool | None
    router_parse_failure: bool | None
    router_internal_failure: bool | None

    # Approval Gate
    pending_approval: dict | None      # {"action": str, "params": dict, "message": str}
    approval_granted: bool | None      # Set by the WebSocket handler after user responds

    # Workflow State
    workflow_state: dict | None


# High-risk tools that require customer confirmation
HIGH_RISK_ACTIONS = {"cancel_order", "process_refund"}


# ──────────────────────────────────────────────
#  Node Functions
# ──────────────────────────────────────────────

import logging
import json
from app.agents import semantic_router
from app.agents.routing_policy import map_domain_to_route

logger = logging.getLogger(__name__)

# Keep strong references to background tasks to prevent them from being orphaned and garbage collected
_shadow_tasks = set()

async def _run_shadow_router(message: str, conversation_history: list, session_id: str, classification: dict):
    """Executes the shadow router and logs the result safely."""
    try:
        semantic_result = await semantic_router.classify_semantic_intent(
            message=message,
            conversation_history=conversation_history
        )
        
        target_route = map_domain_to_route(semantic_result.domain)
        is_agreement = False
        legacy_route = classification.get("route_to")
        
        if target_route in ("OrderAgent", "PaymentAgent") and legacy_route == "db_agent":
            is_agreement = True
        elif target_route in ("GeneralAgent", "ProductAgent") and legacy_route == "rag_agent":
            is_agreement = True
        elif target_route == "EscalationAgent" and legacy_route == "escalation":
            is_agreement = True
            
        shadow_log = {
            "event": "semantic_router.shadow_comparison",
            "session_id": session_id,
            "legacy_intent": classification.get("intent"),
            "legacy_route": legacy_route,
            "semantic_domain": semantic_result.domain,
            "semantic_intent": semantic_result.intent,
            "confidence": semantic_result.confidence,
            "semantic_router_status": semantic_result.diagnostics.failure_type.value,
            "failure_type": semantic_result.diagnostics.failure_type.value,
            "agreement": is_agreement,
            "latency_ms": semantic_result.diagnostics.latency_ms
        }
        logger.info(json.dumps(shadow_log))
    except Exception as e:
        logger.error(f"Shadow router failed unexpectedly: {e}")

async def route_intent_node(state: AgentState) -> dict:
    """Node: Classifies intent and decides routing (with Phase B Shadow Mode)."""
    
    # Run legacy router (Authoritative)
    classification = await intent_router.classify_intent(
        message=state["message"],
        conversation_history=state["conversation_history"]
    )
    
    # Fire and forget the shadow router so we do not delay the customer response
    # Enforce bounded concurrency to prevent unbounded accumulation
    MAX_SHADOW_TASKS = 50
    if len(_shadow_tasks) >= MAX_SHADOW_TASKS:
        logger.warning(f"Shadow task dropped: concurrent limit reached ({MAX_SHADOW_TASKS})")
    else:
        task = asyncio.create_task(
            _run_shadow_router(
                message=state["message"], 
                conversation_history=state["conversation_history"], 
                session_id=state.get("session_id", "unknown"),
                classification=classification
            )
        )
        _shadow_tasks.add(task)
        task.add_done_callback(_shadow_tasks.discard)

    # Legacy path remains 100% authoritative and returns immediately
    return {
        "intent": classification.get("intent", "general"),
        "sentiment": classification.get("sentiment", "neutral"),
        "urgency": classification.get("urgency", "medium"),
        "route_to": classification.get("route_to", "rag_agent"),
        "router_error_type": classification.get("router_error_type"),
        "router_transport_failure": classification.get("router_transport_failure"),
        "router_parse_failure": classification.get("router_parse_failure"),
        "router_internal_failure": classification.get("router_internal_failure"),
    }


async def rag_node(state: AgentState) -> dict:
    """Node: Answers questions using Pinecone knowledge base."""
    result = await rag_agent.generate_response(
        message=state["message"],
        conversation_history=state["conversation_history"]
    )
    return {
        "response": result["response"],
        "escalated": False
    }

async def product_node(state: AgentState) -> dict:
    """Node: Product Domain Agent."""
    def bound_executor(tool_name: str, kwargs: dict):
        return _execute_tool(tool_name, kwargs, state.get("customer_id"))
        
    runtime = SkillRuntime(bound_executor)
    agent = ProductAgent(product_resolver, runtime, product_llm_adapter)
    
    ws = state.get("workflow_state")
    if not ws:
        ws = {"session_id": state.get("session_id", "unknown")}
        
    ctx = ProductDomainContext(
        customer_message=state["message"],
        semantic_intent=state.get("intent", ""),
        session_id=state.get("session_id", ""),
        customer_id=state.get("customer_id"),
        urgency=state.get("urgency", "medium"),
        sentiment=state.get("sentiment", "neutral"),
        workflow_state=WorkflowState(**ws),
        conversation_history=state["conversation_history"]
    )
    
    response = await agent.handle(ctx)
    return {
        "response": response.response,
        "workflow_state": response.workflow_state.model_dump(mode="json"),
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
                "customer_id": state.get("customer_id"),
                "session_id": state.get("session_id"),
            },
            "response": confirm_msg,
            "escalated": False,
        }
    
    # Low-risk action — execute immediately
    tool_results = await asyncio.to_thread(_execute_tool, action, params, state.get("customer_id"))
    
    result = await db_agent.generate_response(
        message=state["message"],
        tool_results=tool_results,
        intent=state["intent"],
        conversation_history=state["conversation_history"],
        customer_name=state.get("customer_name")
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
    if pending.get("customer_id") and pending.get("customer_id") != state.get("customer_id"):
        return {"response": "Approval blocked: Identity mismatch.", "escalated": False, "pending_approval": None}

    # Execute the approved action
    action = pending["action"]
    params = pending["params"]
    tool_results = await asyncio.to_thread(_execute_tool, action, params, state.get("customer_id"))
    
    result = await db_agent.generate_response(
        message=state["message"],
        tool_results=tool_results,
        intent=state["intent"],
        conversation_history=state["conversation_history"],
        customer_name=state.get("customer_name")
    )
    
    return {
        "tool_results": tool_results,
        "response": result["response"],
        "escalated": False,
        "pending_approval": None,
    }


def _execute_tool(action: str, params: dict, authenticated_customer_id: int | None) -> str:
    """Execute a database tool by name, injecting the authenticated customer_id securely."""
    try:
        # Security Boundary: Force the customer_id for all customer-scoped actions
        # This completely overwrites any customer_id the LLM tried to pass
        if action in ["get_customer_history", "track_order", "cancel_order", "process_refund", "get_ticket", "create_ticket", "update_ticket"]:
            if authenticated_customer_id:
                params["customer_id"] = authenticated_customer_id
            
        if action == "lookup_customer":
            return lookup_customer(**params)
        elif action == "get_customer_history":
            return get_customer_history(customer_id=params.get("customer_id"))
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
        elif action == "create_ticket":
            return create_ticket(**params)
        elif action == "update_ticket":
            return update_ticket(**params)
        elif action == "list_all_products":
            return list_all_products(**params)
        elif action == "get_chat_history":
            return get_chat_history(**params)
        elif action == "send_ticket_email_to_customer":
            return send_ticket_email_to_customer(**params)
        elif action == "search_manufacturer_warranty":
            return search_manufacturer_warranty(**params)
        elif action == "check_warranty_status":
            return check_warranty_status(**params)
        elif action == "retrieve_as_context":
            return rag_agent.query_pinecone(params.get("query", ""))
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
    intent = state.get("intent", "general")
    
    # Active workflow continuation
    ws_dict = state.get("workflow_state")
    if ws_dict:
        status = ws_dict.get("workflow_status")
        domain = ws_dict.get("active_domain")
        active_ticket = ws_dict.get("active_ticket_id")
        
        # A valid Product continuation signal: either we are explicitly awaiting input, 
        # or we have an established ticket for this session.
        if (status in ["awaiting_input", "in_progress"] or active_ticket) and domain == "product":
            # Unless there's a hard semantic switch to a completely unrelated domain
            if intent not in ["billing", "refund", "order_cancellation", "account_management", "complaint", "faq"]:
                return "product_node"
                
    if intent in ["product_inquiry", "product_information", "product_features", "product_specs", "product_details", "product_availability", "product_question", "technical_support", "product_comparison", "warranty_claim", "product_warranty"]:
        return "product_node"
    
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
    workflow.add_node("product_node", product_node)
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
            "product_node": "product_node",
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
    workflow.add_edge("product_node", END)
    workflow.add_edge("web_node", END)
    workflow.add_edge("escalation_node", END)
    
    # Compile
    return workflow.compile()


# Singleton instance of the graph
customer_support_graph = create_customer_support_graph()
