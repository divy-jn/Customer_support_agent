"""Opt-in domain-oriented support graph.

Every enabled V2 domain follows the same control boundary:
semantic router -> supervisor -> domain agent -> guardian -> response composer.
Partial rollout modes retain the legacy fallback. Full V2 asks for clarification
through Guardian when it cannot dispatch a supported intent.
"""

import uuid
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
from app.agents.response_composer import compose_response, format_support_timestamp
from app.agents.order_agent import handle_order_request
from app.agents.payment_agent import handle_payment_request
from app.agents.general_agent import handle_general_request
from app.agents.routing_policy import (
    extract_ticket_id, is_ticket_status_query, normalize_intent, telemetry_entity_keys, telemetry_intent,
)
from app.config import settings
from app.models import OrchestrationDomain, RouterFailureType, WorkflowState
from app.tools import create_ticket, find_customer_support_cases, get_ticket, update_ticket

logger = logging.getLogger(__name__)

class DomainAgentState(AgentState):
    """Legacy-compatible state enriched with deterministic V2 routing facts."""

    semantic_domain: str | None
    semantic_confidence: float | None
    raw_classifier_intent: str | None
    is_continuation: bool
    entity_keys: list[str]
    semantic_failure_type: str
    pre_turn_workflow_domain: str | None
    supervisor_action: str | None
    supervisor_target_domain: str | None
    ticket_snapshot: tuple[list[dict] | None, int]
    lookup_ticket_id: int | None
    ticket_lookup_failed: bool
    supervisor_reason: str | None
    architecture_mode: str | None
    guardian_action: str | None
    guardian_reason: str | None
    guardian_ticket_id: int | None
    requires_human: bool | None
    resumed_domain: str | None
    suspended_domain: str | None
    clarification_only: bool


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

    pre_turn_domain = (
        base_workflow.get("active_domain") if isinstance(base_workflow, dict)
        else getattr(base_workflow, "active_domain", None)
    )
    telemetry = {
        "semantic_confidence": semantic.confidence,
        "raw_classifier_intent": telemetry_intent(semantic.intent),
        "is_continuation": semantic.is_continuation,
        "entity_keys": telemetry_entity_keys(semantic.entities),
        "semantic_failure_type": semantic.diagnostics.failure_type.value,
        "pre_turn_workflow_domain": (
            pre_turn_domain if pre_turn_domain in tuple(OrchestrationDomain) else None
        ),
    }

    current_mode = state.get("architecture_mode") or settings.agent_architecture_mode
    if current_mode in {"legacy", "shadow"}:
        current_mode = "v2_product"

    domain = semantic.domain
    intent = normalize_intent(domain, semantic.intent)
    classified = semantic.diagnostics.failure_type == RouterFailureType.SUCCESS
    ticket_query = False
    lookup_ticket_id = None
    if current_mode == "v2_all":
        message = state["message"]
        ticket_query = is_ticket_status_query(message)
        lookup_ticket_id = extract_ticket_id(
            message,
            semantic.entities if classified else {},
            ticket_context=ticket_query or (domain == "general" and intent == "ticket_status"),
        )
        if ticket_query or lookup_ticket_id is not None:
            domain, intent = "general", "ticket_status"

    try:
        workflow = WorkflowState.from_legacy(base_workflow)
        if ticket_query or lookup_ticket_id is not None or classified:
            decision = Supervisor.decide(
                domain,
                intent,
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
                **telemetry,
                "intent": intent,
                "sentiment": semantic.sentiment,
                "urgency": semantic.urgency,
                "semantic_domain": domain,
                "lookup_ticket_id": lookup_ticket_id,
                "supervisor_action": decision.action.value,
                "supervisor_target_domain": decision.target_domain.value,
                "supervisor_reason": decision.transition_reason,
                "resumed_domain": res_dom,
                "suspended_domain": sus_dom,
                "workflow_state": workflow.model_dump(mode="json"),
                "architecture_mode": current_mode,
            }
    except (ValueError, TypeError):
        # A malformed persisted workflow must never create a new V2 state.
        telemetry["semantic_failure_type"] = RouterFailureType.WORKFLOW_VALIDATION_ERROR.value

    # Full V2 handles router or state failures through clarification.
    return {
        **telemetry,
        "semantic_domain": "unknown",
        "supervisor_action": "legacy_fallback",
        "supervisor_reason": "Semantic routing or workflow validation failed.",
        "architecture_mode": current_mode,
    }


def route_after_supervisor(state: DomainAgentState) -> str:
    """Dispatch only implemented slices; all enabled slices finish at Guardian."""
    mode = state.get("architecture_mode") or settings.agent_architecture_mode
    fallback = "clarification" if mode == "v2_all" else "legacy"
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
        return fallback

    domain = state.get("supervisor_target_domain") or state.get("semantic_domain")
    intent = state.get("intent")

    if domain == "escalation":
        return "guardian"

    if mode in {"legacy", "shadow"}:
        return "legacy"

    if domain == "product":
        if mode in {"v2_product", "v2_product_order", "v2_core", "v2_all"}:
            return "product"
        return "legacy"

    if domain == "order":
        if mode in {"v2_product_order", "v2_core", "v2_all"}:
            return "order"
        return fallback

    if domain == "payment":
        if mode == "v2_all" and intent == "payment_method":
            return "general"
        if mode in {"v2_core", "v2_all"}:
            return "payment"
        return fallback

    if domain == "general":
        if mode == "v2_all":
            return "ticket_status" if intent == "ticket_status" else "general"
        return "legacy"

    return fallback


async def v2_product_node(state: DomainAgentState) -> dict[str, Any]:
    """Run the domain skill without allowing it to bypass Guardian policy."""
    return await product_node({**state, "manage_tickets": False})


async def v2_order_node(state: DomainAgentState) -> dict[str, Any]:
    """Order tracking/cancellation slice; mutations remain approval-gated."""
    return await handle_order_request(state)


async def v2_payment_node(state: DomainAgentState) -> dict[str, Any]:
    """Verify ledger facts and route payment issues for specialist review."""
    return await handle_payment_request(state)


async def v2_general_node(state: DomainAgentState) -> dict[str, Any]:
    """Read-only FAQ and policy support through the approved knowledge base."""
    return await handle_general_request(state)


def _requests_human(message: str) -> bool:
    return bool(re.search(r"\b(human|manager|representative|real person|agent)\b", message, re.IGNORECASE))


def _previously_requested_human(history: list[dict[str, Any]], current_message: str) -> bool:
    """Return whether the customer asked for a human on a prior turn."""
    prior = history
    if history and history[-1].get("role") in {"customer", "user"}:
        if str(history[-1].get("content", "")).strip().casefold() == current_message.strip().casefold():
            prior = history[:-1]
    return any(
        item.get("role") in {"customer", "user"}
        and _requests_human(str(item.get("content", "")))
        for item in prior[-10:]
    )


def _safety_or_legal_issue(message: str) -> bool:
    return bool(re.search(r"\b(safety|unsafe|injury|legal|lawyer|police)\b", message, re.IGNORECASE))


def _case_domain(ticket: dict) -> str | None:
    ticket_type = ticket.get("type")
    if not isinstance(ticket_type, str):
        return None
    return {
        "technical_issue": "product",
        "billing": "payment",
        "refund": "payment",
        "cancellation": "order",
    }.get(ticket_type)


def _case_intent(ticket: dict) -> str | None:
    subject = ticket.get("subject", "")
    if not isinstance(subject, str) or not subject.startswith("Issue: "):
        return None
    return subject[7:].split(" - ", 1)[0].lower().replace(" ", "_") or None


def _load_ticket_snapshot(customer_id: int | None) -> tuple[list[dict] | None, int]:
    """Read the authenticated customer's active cases and recent ticket count."""
    if type(customer_id) is not int or customer_id <= 0:
        return None, 0
    since = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    try:
        payload = json.loads(find_customer_support_cases(customer_id, created_since=since))
        cases = payload["cases"]
        recent_count = payload["recent_count"]
        if (
            payload.get("error") or not isinstance(cases, list)
            or type(recent_count) is not int or recent_count < 0
            or any(
                not isinstance(case, dict)
                or type(case.get("id")) is not int
                or not isinstance(case.get("status"), str)
                for case in cases
            )
        ):
            return None, 0
        return cases, recent_count
    except Exception:
        # Keep lookup failures distinct from an authoritative empty result.
        return None, 0


def _guardian_ticket_history(snapshot: tuple[list[dict] | None, int]) -> tuple[list[SupportCase], int, bool]:
    rows, recent_count = snapshot
    cases = [
        SupportCase(
            id=case["id"],
            status=case["status"],
            domain=_case_domain(case),
            intent=_case_intent(case),
            priority=case.get("priority"),
        )
        for case in rows or []
    ]
    return cases, recent_count, rows is None


def _format_ticket_reply(ticket: dict, *, include_priority: bool = True) -> str:
    subject = " ".join(str(ticket.get("subject") or "Support request").split())[:120]
    status = ticket["status"].replace("_", " ").capitalize()
    response = f"**Ticket #{ticket['id']}** — {subject}. Status: {status}."
    priority = ticket.get("priority")
    if include_priority and isinstance(priority, str) and priority.strip():
        response += f" Priority: {priority.replace('_', ' ').capitalize()}."
    updated = format_support_timestamp(ticket.get("updated_at")) or "Unavailable"
    return f"{response} Last updated: {updated}."


async def _exact_ticket_reply(ticket_id: int, customer_id: int | None) -> dict[str, Any]:
    unavailable = {
        "response": f"I couldn’t verify ticket #{ticket_id} right now. Please try again.",
        "ticket_lookup_failed": True,
    }
    if type(customer_id) is not int or customer_id <= 0:
        return unavailable

    try:
        payload = json.loads(await asyncio.to_thread(get_ticket, ticket_id, customer_id=customer_id))
    except Exception:
        return unavailable
    if not isinstance(payload, dict):
        return unavailable
    not_found = {
        "response": f"I couldn’t find ticket #{ticket_id} on your account.",
        "ticket_lookup_failed": False,
    }
    if payload.get("error"):
        if payload.get("error_code") == "not_found" or (
            "error_code" not in payload and payload["error"] in (
                f"Ticket #{ticket_id} not found",
                f"Ticket #{ticket_id} not found or does not belong to you",
            )
        ):
            return not_found
        return unavailable
    if payload.get("customer_id", customer_id) != customer_id:
        return not_found
    if payload.get("id") != ticket_id or not isinstance(payload.get("status"), str):
        return unavailable

    return {"response": _format_ticket_reply(payload), "ticket_lookup_failed": False}


async def v2_ticket_status_node(state: DomainAgentState) -> dict[str, Any]:
    if ticket_id := state.get("lookup_ticket_id"):
        return await _exact_ticket_reply(ticket_id, state.get("customer_id"))

    snapshot = await asyncio.to_thread(_load_ticket_snapshot, state.get("customer_id"))
    cases, _ = snapshot
    if cases is None:
        response = "I couldn’t verify your active support tickets right now. Please try again."
    elif not cases:
        response = "You have no active support tickets on your account."
    else:
        lines = ["Here are your active support tickets:", ""]
        lines.extend(f"- {_format_ticket_reply(case, include_priority=False)}" for case in cases[:10])
        if len(cases) > 10:
            lines.extend(("", f"Showing 10 of {len(cases)} active tickets."))
        response = "\n".join(lines)
    return {"response": response, "ticket_snapshot": snapshot}


_READ_ONLY_INTENTS = {
    "order": frozenset({
        "view_order_history", "track_order", "tracking", "order_tracking",
        "order_status", "order_details", "check_order",
    }),
    "payment": frozenset({"payment_method"}),
    "product": frozenset({
        "product_inquiry", "product_information", "product_features", "product_specs", "product_details",
        "product_availability", "product_comparison", "product_question", "product_stock", "product_warranty",
        "warranty", "warranty_status", "warranty_inquiry", "check_inventory", "inventory", "stock_check",
    }),
}


async def guardian_response_node(state: DomainAgentState) -> dict[str, Any]:
    """Apply deterministic policy after the domain result and compose output."""
    raw_workflow = state.get("workflow_state") or {}
    if hasattr(raw_workflow, "model_dump"):
        new_workflow = raw_workflow.model_dump(mode="json")
    elif isinstance(raw_workflow, dict):
        new_workflow = dict(raw_workflow)
    else:
        new_workflow = {}

    domain = state.get("supervisor_target_domain") or state.get("semantic_domain", "general")
    prior_attempts = max(0, int(new_workflow.get("unresolved_attempts", 0)))
    clarification_only = bool(state.get("clarification_only"))
    intent = normalize_intent(domain, state.get("intent", ""))
    read_only = domain == "general" or intent in _READ_ONLY_INTENTS.get(domain, ())
    exact_ticket = "ticket_lookup_failed" in state
    ticket_status = exact_ticket or "ticket_snapshot" in state
    customer_id = state.get("customer_id")
    if exact_ticket:
        active_cases, recent_count, ticket_history_lookup_failed = [], 0, state["ticket_lookup_failed"]
    else:
        snapshot = state.get("ticket_snapshot")
        if snapshot is None:
            snapshot = await asyncio.to_thread(_load_ticket_snapshot, customer_id) if customer_id else ([], 0)
        active_cases, recent_count, ticket_history_lookup_failed = _guardian_ticket_history(snapshot)
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
    domain_failed = (
        clarification_only
        or (ticket_status and ticket_history_lookup_failed)
        or bool(state.get("requires_human"))
        or (
            state.get("response") is None
            and not one_off_human_request
            and state.get("supervisor_action") != SupervisorAction.ESCALATE.value
        )
    )
    answered_ticket_status = ticket_status and not ticket_history_lookup_failed
    attempts_for_decision = 0 if answered_ticket_status else prior_attempts + int(domain_failed)
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
        active_cases=active_cases,
        recent_ticket_count=0 if answered_ticket_status else recent_count,
        allow_ticket_mutation=not (clarification_only or read_only or ticket_status),
        history_lookup_failed=ticket_history_lookup_failed,
        sentiment=state.get("sentiment", "neutral"),
        urgency=state.get("urgency", "low"),
        customer_repeats_human_request=previously_requested_human,
    ))
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
        answered = state.get("response") is not None and not domain_failed
        next_unresolved_attempts = 0 if answered else attempts_for_decision
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

        handoff = {}
        if session_id := state.get("session_id"):
            try:
                handoff_id = uuid.uuid5(uuid.UUID(request_id), f"escalate_session:{session_id}")
            except ValueError:
                logger.warning("Guardian ticket action skipped: invalid request ID")
                return None, True
            handoff["pending_handoff"] = {
                "session_id": session_id,
                "handoff_request_id": str(handoff_id),
                "escalation_reason": decision.reason,
                "message": state["message"],
                "sentiment": state.get("sentiment", "negative"),
                "urgency": state.get("urgency", "high"),
                "intent": state.get("intent"),
                "semantic_domain": state.get("semantic_domain"),
            }
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
            **handoff,
        )
        payload = json.loads(raw)
        return payload.get("ticket_id"), bool(payload.get("error") or not payload.get("ticket_id"))
    except Exception:
        logger.exception("Guardian ticket mutation failed")
        return decision.ticket_id, True


async def safe_clarification_node(state: DomainAgentState) -> dict[str, Any]:
    """Dedicated safe clarification node that asks for clarification without tool execution."""
    clarification_msg = (
        "Could you please share a few more details or clarify your request so I can help you correctly?"
    )
    if (state.get("architecture_mode") or settings.agent_architecture_mode) == "v2_all":
        return {
            "response": clarification_msg,
            "clarification_only": True,
            "pending_approval": None,
            "escalated": False,
        }

    raw_workflow = state.get("workflow_state") or {}
    new_workflow = dict(raw_workflow) if isinstance(raw_workflow, dict) else {}
    new_workflow["turn_count"] = int(new_workflow.get("turn_count", 0)) + 1
    new_workflow["updated_at"] = datetime.now(timezone.utc).isoformat()
    reason = state.get("supervisor_reason")
    return {
        "response": clarification_msg,
        "guardian_action": GuardianAction.RESOLVE.value,
        "guardian_reason": reason or "Clarification requested safely without tool execution.",
        "pending_approval": None,
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
    workflow.add_node("v2_ticket_status", v2_ticket_status_node)
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
            "ticket_status": "v2_ticket_status",
            "guardian": "guardian_response",
            "clarification": "safe_clarification",
            "legacy": "legacy_fallback",
        },
    )
    workflow.add_edge("v2_product", "guardian_response")
    workflow.add_edge("v2_order", "guardian_response")
    workflow.add_edge("v2_payment", "guardian_response")
    workflow.add_edge("v2_general", "guardian_response")
    workflow.add_edge("v2_ticket_status", "guardian_response")
    workflow.add_conditional_edges(
        "safe_clarification",
        lambda state: "guardian_response" if state.get("clarification_only") else END,
        ["guardian_response", END],
    )
    workflow.add_edge("guardian_response", END)
    workflow.add_edge("legacy_fallback", END)
    return workflow.compile()


domain_support_graph = create_domain_support_graph()
