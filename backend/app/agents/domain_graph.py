"""Opt-in domain-oriented support graph.

Every enabled V2 domain follows the same control boundary:
semantic router -> supervisor -> domain agent -> guardian -> response composer.
Unsupported intents deliberately use the legacy graph; the rollout flag remains
the rollback boundary rather than silently treating a partial agent as complete.
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from app.agents import semantic_router
from app.agents.graph import AgentState, customer_support_graph, product_node
from app.agents.supervisor import Supervisor, SupervisorAction
from app.agents.guardian import Guardian, GuardianAction, GuardianDecision, GuardianInput, SupportCase
from app.agents.response_composer import compose_response
from app.agents.order_agent import handle_order_request
from app.agents.payment_agent import handle_payment_request
from app.agents.general_agent import handle_general_request
from app.config import settings
from app.models import RouterFailureType, WorkflowState
from app.tools import create_ticket, find_customer_support_cases, update_ticket

logger = logging.getLogger(__name__)

_ORDER_V2_INTENTS = {
    "tracking", "track_order", "order_tracking", "cancel_order", "order_cancellation", "cancellation",
    "delivery_issue", "delivery_delay", "missing_item", "wrong_item", "damaged_item", "order_issue", "shipping_issue",
}
_PAYMENT_V2_INTENTS = {
    "refund", "refund_request", "process_refund", "billing_issue", "payment_failure", "payment_failed",
    "transaction_issue", "duplicate_charge", "charged_twice", "payment_method",
}


class DomainAgentState(AgentState):
    """Legacy-compatible state enriched with deterministic V2 routing facts."""

    semantic_domain: str | None
    semantic_confidence: float | None
    supervisor_action: str | None
    supervisor_reason: str | None
    architecture_mode: str | None
    guardian_action: str | None
    guardian_reason: str | None
    guardian_ticket_id: int | None
    requires_human: bool | None
    resumed_domain: str | None
    suspended_domain: str | None


async def semantic_supervisor_node(state: DomainAgentState) -> dict[str, Any]:
    """Classify facts, then let the Supervisor own the workflow transition."""
    semantic = await semantic_router.classify_semantic_intent(
        message=state["message"],
        conversation_history=state.get("conversation_history", []),
    )

    base_workflow = state.get("workflow_state") or {
        "session_id": state.get("session_id", "unknown"),
        "customer_id": state.get("customer_id"),
    }

    current_mode = state.get("architecture_mode") or settings.agent_architecture_mode
    if current_mode in {"legacy", "shadow"}:
        current_mode = "v2_product"

    try:
        workflow = WorkflowState.from_legacy(base_workflow)
        if semantic.diagnostics.failure_type == RouterFailureType.SUCCESS:
            decision = Supervisor.decide(
                semantic.domain,
                semantic.intent,
                workflow,
                state["message"],
            )
            workflow = Supervisor.apply_decision(workflow, decision)
            res_dom = (
                decision.transition_metadata.resumed_domain.value
                if decision.transition_metadata and decision.transition_metadata.resumed_domain
                else None
            )
            sus_dom = (
                decision.transition_metadata.suspended_domain.value
                if decision.transition_metadata and decision.transition_metadata.suspended_domain
                else None
            )
            return {
                "intent": semantic.intent,
                "sentiment": semantic.sentiment,
                "urgency": semantic.urgency,
                "semantic_domain": semantic.domain,
                "semantic_confidence": semantic.confidence,
                "supervisor_action": decision.action.value,
                "supervisor_reason": decision.transition_reason,
                "resumed_domain": res_dom,
                "suspended_domain": sus_dom,
                "workflow_state": workflow.model_dump(mode="json"),
                "architecture_mode": current_mode,
            }
    except (ValueError, TypeError):
        # A malformed persisted workflow must never create a new V2 state.
        pass

    # Router or state failure: retain the established legacy behavior.
    return {
        "semantic_domain": "unknown",
        "semantic_confidence": 0.0,
        "supervisor_action": "legacy_fallback",
        "supervisor_reason": "Semantic routing or workflow validation failed.",
        "architecture_mode": current_mode,
    }


def route_after_supervisor(state: DomainAgentState) -> str:
    """Dispatch only implemented slices; all enabled slices finish at Guardian."""
    supervisor_action = state.get("supervisor_action")
    if supervisor_action == SupervisorAction.ESCALATE.value:
        return "guardian"
    if supervisor_action == SupervisorAction.REQUEST_CLARIFICATION.value:
        return "clarification"

    # Only continue with enabled domains if supervisor approved continuing/starting/resuming/switching
    valid_actions = {
        SupervisorAction.CONTINUE.value,
        SupervisorAction.START_NEW.value,
        SupervisorAction.SUSPEND_AND_SWITCH.value,
        SupervisorAction.RESUME.value,
    }
    if supervisor_action not in valid_actions:
        return "legacy"

    domain = state.get("semantic_domain")
    intent = state.get("intent")

    if domain == "escalation":
        return "guardian"

    mode = state.get("architecture_mode") or settings.agent_architecture_mode
    if mode in {"legacy", "shadow"}:
        return "legacy"

    if domain == "product":
        if mode in {"v2_product", "v2_product_order", "v2_core", "v2_all"}:
            return "product"
        return "legacy"

    if domain == "order":
        if mode in {"v2_product_order", "v2_core", "v2_all"} and intent in _ORDER_V2_INTENTS:
            return "order"
        return "legacy"

    if domain == "payment":
        if mode in {"v2_core", "v2_all"} and intent in _PAYMENT_V2_INTENTS:
            return "payment"
        return "legacy"

    if domain == "general":
        if mode == "v2_all":
            return "general"
        return "legacy"

    return "legacy"


async def v2_product_node(state: DomainAgentState) -> dict[str, Any]:
    """Run the domain skill without allowing it to bypass Guardian policy."""
    return await product_node({**state, "manage_tickets": False})


async def v2_order_node(state: DomainAgentState) -> dict[str, Any]:
    """Order tracking/cancellation slice; mutations remain approval-gated."""
    return await handle_order_request(state)


async def v2_payment_node(state: DomainAgentState) -> dict[str, Any]:
    """Refund-only slice; payment mutations stay in the existing approval path."""
    return await handle_payment_request(state)


async def v2_general_node(state: DomainAgentState) -> dict[str, Any]:
    """Read-only FAQ and policy support through the approved knowledge base."""
    return await handle_general_request(state)


def _requests_human(message: str) -> bool:
    return bool(re.search(r"\b(human|manager|representative|real person|agent)\b", message, re.IGNORECASE))


def _previously_requested_human(history: list[dict[str, Any]], current_message: str) -> bool:
    """Return whether the customer asked for a human on a prior turn."""
    current = current_message.strip().casefold()
    for item in history[-10:]:
        if item.get("role") not in {"customer", "user"}:
            continue
        content = str(item.get("content", "")).strip()
        if not content or content.casefold() == current:
            continue
        if _requests_human(content):
            return True
    return False


def _safety_or_legal_issue(message: str) -> bool:
    return bool(re.search(r"\b(safety|unsafe|injury|legal|lawyer|police)\b", message, re.IGNORECASE))


def _case_domain(ticket: dict) -> str | None:
    ticket_type = ticket.get("type")
    return {
        "technical_issue": "product",
        "billing": "payment",
        "refund": "payment",
        "cancellation": "order",
    }.get(ticket_type)


def _case_intent(ticket: dict) -> str | None:
    subject = ticket.get("subject", "")
    if not subject.startswith("Issue: "):
        return None
    return subject[7:].split(" - ", 1)[0].lower().replace(" ", "_") or None


def _recent_cases(customer_id: int | None) -> tuple[list[SupportCase], bool]:
    """Read a narrow, recent ticket projection; DB trouble must not leak data.

    Returns:
        tuple[list[SupportCase], bool]: (cases, lookup_failed)
    """
    if not customer_id:
        return [], False
    since = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    try:
        raw = find_customer_support_cases(customer_id, created_since=since)
        if not raw or not isinstance(raw, str):
            return [], True
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get("error"):
            return [], True
        if "cases" not in payload or not isinstance(payload.get("cases"), list):
            return [], True
        cases: list[SupportCase] = []
        for case in payload.get("cases", []):
            if not isinstance(case, dict) or "id" not in case or "status" not in case:
                return [], True
            cases.append(
                SupportCase(
                    id=case["id"],
                    status=case["status"],
                    domain=_case_domain(case),
                    intent=_case_intent(case),
                    priority=case.get("priority"),
                )
            )
        return cases, False
    except Exception:
        return [], True


async def guardian_response_node(state: DomainAgentState) -> dict[str, Any]:
    """Apply deterministic policy after the domain result and compose output."""
    raw_workflow = state.get("workflow_state") or {}
    if hasattr(raw_workflow, "model_dump"):
        new_workflow = raw_workflow.model_dump(mode="json")
    elif isinstance(raw_workflow, dict):
        new_workflow = dict(raw_workflow)
    else:
        new_workflow = {}

    domain = state.get("semantic_domain", "general")
    prior_attempts = max(0, int(new_workflow.get("unresolved_attempts", 0)))
    recent_cases, ticket_history_lookup_failed = _recent_cases(state.get("customer_id"))
    customer_requests_human = _requests_human(state["message"])
    previously_requested_human = _previously_requested_human(
        state.get("conversation_history", []), state["message"],
    )
    one_off_human_request = (
        customer_requests_human
        and not previously_requested_human
        and not _safety_or_legal_issue(state["message"])
        and state.get("urgency", "low").lower() not in {"high", "critical"}
        and (domain == "escalation" or state.get("supervisor_action") == SupervisorAction.ESCALATE.value)
    )
    domain_failed = bool(state.get("requires_human")) or (
        state.get("response") is None
        and not one_off_human_request
        and state.get("supervisor_action") != SupervisorAction.ESCALATE.value
    )
    attempts_for_decision = prior_attempts + int(domain_failed)
    decision = Guardian.decide(GuardianInput(
        domain=domain,
        intent=state.get("intent", ""),
        customer_requests_human=customer_requests_human,
        # A failed domain skill requires handoff, but a one-off human keyword
        # routed to Guardian is not itself proof that automation has failed.
        requires_human=domain_failed,
        safety_or_legal_issue=_safety_or_legal_issue(state["message"]),
        domain_can_resolve=state.get("response") is not None,
        unresolved_attempts=attempts_for_decision,
        recent_cases=recent_cases,
        history_lookup_failed=ticket_history_lookup_failed,
        sentiment=state.get("sentiment", "neutral"),
        urgency=state.get("urgency", "low"),
        customer_repeats_human_request=previously_requested_human,
    ))
    if ticket_history_lookup_failed and decision.action in {
        GuardianAction.CREATE_TICKET, GuardianAction.UPDATE_EXISTING_TICKET,
    }:
        # Route to human review without mutating ticket state on incomplete history.
        decision = GuardianDecision(
            action=GuardianAction.ESCALATE,
            reason=(
                "Ticket history could not be verified; hand off for human review "
                "without creating a potentially duplicate ticket."
            ),
        )
    ticket_id, mutation_error = await _apply_guardian_ticket_decision(
        {**state, "history_lookup_failed": ticket_history_lookup_failed}, decision
    )
    response = state.get("response") or "I need additional help with this request."
    if mutation_error:
        response += " I couldn’t register the support request just now. Please try again."
    elif decision.action is GuardianAction.CONTINUE and state.get("response") is None:
        response = "I can try to help with the issue here first. What would you like me to look into?"
    else:
        response = compose_response(response, decision)

    if decision.action in {GuardianAction.RESOLVE, GuardianAction.CONTINUE}:
        next_unresolved_attempts = 0 if state.get("response") is not None else prior_attempts
    else:
        next_unresolved_attempts = attempts_for_decision if domain_failed else prior_attempts

    new_workflow["unresolved_attempts"] = min(next_unresolved_attempts, 100)
    new_workflow["turn_count"] = int(new_workflow.get("turn_count", 0)) + 1
    new_workflow["updated_at"] = datetime.now(timezone.utc).isoformat()
    if decision.action == GuardianAction.ESCALATE:
        new_workflow["global_status"] = "escalated"

    if ticket_id:
        active_dom = new_workflow.get("active_domain") or domain
        state_key = f"{active_dom}_state"
        if state_key in new_workflow and isinstance(new_workflow[state_key], dict):
            new_workflow[state_key]["active_ticket_id"] = ticket_id

    return {
        "response": response,
        "guardian_action": decision.action.value,
        "guardian_reason": decision.reason,
        "guardian_ticket_id": ticket_id or decision.ticket_id,
        "pending_approval": state.get("pending_approval"),
        "resumed_domain": state.get("resumed_domain"),
        "suspended_domain": state.get("suspended_domain"),
        "workflow_state": new_workflow,
        "escalated": decision.action in {GuardianAction.ESCALATE, GuardianAction.CREATE_TICKET},
    }


async def _apply_guardian_ticket_decision(
    state: DomainAgentState, decision: GuardianDecision,
) -> tuple[int | None, bool]:
    """Perform the sole V2 ticket mutation boundary, using idempotent tools."""
    if decision.action not in {GuardianAction.CREATE_TICKET, GuardianAction.UPDATE_EXISTING_TICKET}:
        return decision.ticket_id, False
    if state.get("history_lookup_failed"):
        logger.warning("Guardian ticket action blocked: ticket history could not be verified")
        return decision.ticket_id, True
    customer_id = state.get("customer_id")
    request_id = state.get("client_request_id")
    if not customer_id or not request_id:
        logger.warning("Guardian ticket action skipped: missing authenticated identity or request ID")
        return decision.ticket_id, True
    try:
        if decision.action is GuardianAction.UPDATE_EXISTING_TICKET:
            raw = await asyncio.to_thread(
                update_ticket,
                ticket_id=decision.ticket_id,
                customer_id=customer_id,
                description_append=state["message"],
                client_request_id=request_id,
            )
            payload = json.loads(raw)
            return decision.ticket_id, bool(payload.get("error"))

        raw = await asyncio.to_thread(
            create_ticket,
            customer_id=customer_id,
            subject=f"Issue: {state.get('intent', 'support_request').replace('_', ' ').title()}",
            description=state["message"],
            ticket_type={
                "product": "technical_issue", "order": "cancellation",
                "payment": "billing",
            }.get(state.get("semantic_domain"), "inquiry"),
            priority="high" if state.get("urgency") in {"high", "critical"} else "medium",
            channel="chat",
            client_request_id=request_id,
        )
        payload = json.loads(raw)
        return payload.get("ticket_id"), bool(payload.get("error") or not payload.get("ticket_id"))
    except Exception:
        logger.exception("Guardian ticket mutation failed")
        return decision.ticket_id, True


async def safe_clarification_node(state: DomainAgentState) -> dict[str, Any]:
    """Dedicated safe clarification node that asks for clarification without tool execution."""
    raw_workflow = state.get("workflow_state") or {}
    new_workflow = dict(raw_workflow) if isinstance(raw_workflow, dict) else {}
    new_workflow["turn_count"] = int(new_workflow.get("turn_count", 0)) + 1
    new_workflow["updated_at"] = datetime.now(timezone.utc).isoformat()
    reason = state.get("supervisor_reason")

    clarification_msg = (
        "Could you please share a few more details or clarify your request so I can help you correctly?"
    )
    return {
        "response": clarification_msg,
        "guardian_action": GuardianAction.RESOLVE.value,
        "guardian_reason": reason or "Clarification requested safely without tool execution.",
        "pending_approval": state.get("pending_approval"),
        "resumed_domain": state.get("resumed_domain"),
        "suspended_domain": state.get("suspended_domain"),
        "workflow_state": new_workflow,
        "escalated": False,
    }


async def legacy_fallback_node(state: DomainAgentState) -> dict[str, Any]:
    """Delegate unsupported domains to the existing, production graph."""
    return await customer_support_graph.ainvoke(state)


def create_domain_support_graph():
    workflow = StateGraph(DomainAgentState)
    workflow.add_node("semantic_supervisor", semantic_supervisor_node)
    workflow.add_node("v2_product", v2_product_node)
    workflow.add_node("v2_order", v2_order_node)
    workflow.add_node("v2_payment", v2_payment_node)
    workflow.add_node("v2_general", v2_general_node)
    workflow.add_node("safe_clarification", safe_clarification_node)
    workflow.add_node("guardian_response", guardian_response_node)
    workflow.add_node("legacy_fallback", legacy_fallback_node)
    workflow.set_entry_point("semantic_supervisor")
    workflow.add_conditional_edges(
        "semantic_supervisor",
        route_after_supervisor,
        {
            "product": "v2_product",
            "order": "v2_order",
            "payment": "v2_payment",
            "general": "v2_general",
            "guardian": "guardian_response",
            "clarification": "safe_clarification",
            "legacy": "legacy_fallback",
        },
    )
    workflow.add_edge("v2_product", "guardian_response")
    workflow.add_edge("v2_order", "guardian_response")
    workflow.add_edge("v2_payment", "guardian_response")
    workflow.add_edge("v2_general", "guardian_response")
    workflow.add_edge("safe_clarification", END)
    workflow.add_edge("guardian_response", END)
    workflow.add_edge("legacy_fallback", END)
    return workflow.compile()


domain_support_graph = create_domain_support_graph()
