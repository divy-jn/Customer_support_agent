from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional
from app.models import (
    WorkflowState, OrchestrationDomain,
    GlobalWorkflowStatus, DomainWorkflowStatus,
    ProductState, OrderState, PaymentState
)

class SupervisorAction(str, Enum):
    CONTINUE = "continue"
    SUSPEND_AND_SWITCH = "suspend_and_switch"
    START_NEW = "start_new"
    RESUME = "resume"
    ESCALATE = "escalate"
    REQUEST_CLARIFICATION = "request_clarification"

class TransitionMetadata(BaseModel):
    """Bounded typed representation of transition context."""
    suspended_domain: Optional[OrchestrationDomain] = None
    resumed_domain: Optional[OrchestrationDomain] = None

class SupervisorDecision(BaseModel):
    action: SupervisorAction
    target_domain: OrchestrationDomain
    transition_reason: str
    transition_metadata: Optional[TransitionMetadata] = None

class InvalidSupervisorDecisionError(ValueError):
    """Raised when a SupervisorDecision contains an impossible action combination."""
    pass

# ──────────────────────────────────────────────
#  Domains that own typed workflow state and can
#  be suspended / resumed.  GENERAL and ESCALATION
#  are routing-only domains with no persistent
#  domain state, so they must never appear in
#  suspended_domains.
# ──────────────────────────────────────────────
_SUSPENDABLE_DOMAINS = frozenset({
    OrchestrationDomain.PRODUCT,
    OrchestrationDomain.ORDER,
    OrchestrationDomain.PAYMENT,
})


class Supervisor:
    """
    Deterministic Python Workflow Orchestrator (F.2 typed multi-domain architecture).

    Evaluates semantic facts against the typed multi-domain WorkflowState to
    determine the next orchestration action.

    Key design invariants:
    - The Supervisor operates exclusively on F.2 typed fields: ``global_status``,
      ``active_domain``, ``suspended_domains``, and per-domain ``domain_status``.
    - Legacy status fields are neither read nor written by this class.
      Legacy compatibility is maintained at the adapter/projection boundary only
      (see ``WorkflowState.to_legacy_projection``).
    - ``decide()`` checks ``suspended_domains`` *before* the generic
      active-domain switch logic so that returning to a previously suspended
      domain produces a RESUME rather than SUSPEND_AND_SWITCH.
    - ``apply_decision()`` validates decision integrity before mutation and
      raises ``InvalidSupervisorDecisionError`` for impossible combinations.
    """

    CONVERSATIONAL_INTENTS = {
        "conversational", "greeting", "clarification", "acknowledgment",
        "continuation", "affirmation", "negation", "small_talk", "chitchat"
    }

    @staticmethod
    def _is_valid_domain(domain_str: str) -> bool:
        try:
            OrchestrationDomain(domain_str)
            return True
        except ValueError:
            return False

    @staticmethod
    def _get_domain_status(state: WorkflowState, domain: OrchestrationDomain) -> DomainWorkflowStatus | None:
        """Return the typed domain_status for any domain, not just the active one."""
        if domain == OrchestrationDomain.PRODUCT and state.product_state:
            return state.product_state.domain_status
        if domain == OrchestrationDomain.ORDER and state.order_state:
            return state.order_state.domain_status
        if domain == OrchestrationDomain.PAYMENT and state.payment_state:
            return state.payment_state.domain_status
        return None

    @staticmethod
    def _get_active_domain_status(state: WorkflowState) -> DomainWorkflowStatus | None:
        if not state.active_domain:
            return None
        try:
            active = OrchestrationDomain(state.active_domain)
        except ValueError:
            return None
        return Supervisor._get_domain_status(state, active)

    @staticmethod
    def decide(semantic_domain: str, semantic_intent: str, state: WorkflowState, message: str) -> SupervisorDecision:
        # ── Guard: invalid active_domain ──
        if state.active_domain is not None and not Supervisor._is_valid_domain(state.active_domain):
            return SupervisorDecision(
                action=SupervisorAction.REQUEST_CLARIFICATION,
                target_domain=OrchestrationDomain.GENERAL,
                transition_reason=f"Invalid active_domain in state: '{state.active_domain}'"
            )

        # ── Guard: unknown global_status ──
        if state.global_status not in list(GlobalWorkflowStatus):
            return SupervisorDecision(
                action=SupervisorAction.REQUEST_CLARIFICATION,
                target_domain=OrchestrationDomain(state.active_domain) if state.active_domain else OrchestrationDomain.GENERAL,
                transition_reason=f"Unknown global_status: '{state.global_status}'"
            )

        # ── Guard: invalid/missing semantic domain ──
        if not semantic_domain or not Supervisor._is_valid_domain(semantic_domain):
            return SupervisorDecision(
                action=SupervisorAction.REQUEST_CLARIFICATION,
                target_domain=OrchestrationDomain(state.active_domain) if state.active_domain else OrchestrationDomain.GENERAL,
                transition_reason=f"Ambiguous, missing, or unknown semantic domain: '{semantic_domain}'"
            )

        target_domain = OrchestrationDomain(semantic_domain)

        # ── Terminal: escalated ──
        if state.global_status == GlobalWorkflowStatus.ESCALATED:
            return SupervisorDecision(
                action=SupervisorAction.ESCALATE,
                target_domain=OrchestrationDomain(state.active_domain) if state.active_domain else target_domain,
                transition_reason="Workflow is escalated, terminal state."
            )

        # ── Explicit escalation request ──
        if target_domain == OrchestrationDomain.ESCALATION or semantic_intent == "escalation":
            return SupervisorDecision(
                action=SupervisorAction.ESCALATE,
                target_domain=OrchestrationDomain(state.active_domain) if state.active_domain else OrchestrationDomain.GENERAL,
                transition_reason="Semantic escalation requested."
            )

        domain_status = Supervisor._get_active_domain_status(state)
        is_active_general = (state.active_domain == OrchestrationDomain.GENERAL.value and state.global_status == GlobalWorkflowStatus.IN_PROGRESS)

        # ── Completed active domain → start new ──
        if domain_status == DomainWorkflowStatus.COMPLETED:
            return SupervisorDecision(
                action=SupervisorAction.START_NEW,
                target_domain=target_domain,
                transition_reason="Completed workflow followed by new message."
            )

        # ── Idle / failed / no active domain → start new ──
        if state.global_status == GlobalWorkflowStatus.IDLE or domain_status == DomainWorkflowStatus.FAILED or not state.active_domain:
            return SupervisorDecision(
                action=SupervisorAction.START_NEW,
                target_domain=target_domain,
                transition_reason="Starting new workflow from idle/failed state."
            )

        # ── Active domain is IN_PROGRESS or AWAITING_INPUT (or GENERAL active) ──
        if domain_status in (DomainWorkflowStatus.IN_PROGRESS, DomainWorkflowStatus.AWAITING_INPUT) or is_active_general:
            active_enum = OrchestrationDomain(state.active_domain)
            active_is_suspendable = active_enum in _SUSPENDABLE_DOMAINS

            if target_domain.value == state.active_domain:
                return SupervisorDecision(
                    action=SupervisorAction.CONTINUE,
                    target_domain=active_enum,
                    transition_reason="Domain matches active workflow."
                )
            elif target_domain == OrchestrationDomain.GENERAL:
                if semantic_intent in Supervisor.CONVERSATIONAL_INTENTS:
                    return SupervisorDecision(
                        action=SupervisorAction.CONTINUE,
                        target_domain=active_enum,
                        transition_reason="Conversational intent continues active workflow."
                    )
                elif active_is_suspendable:
                    return SupervisorDecision(
                        action=SupervisorAction.SUSPEND_AND_SWITCH,
                        target_domain=OrchestrationDomain.GENERAL,
                        transition_reason="Unrelated general request suspends active workflow.",
                        transition_metadata=TransitionMetadata(suspended_domain=active_enum)
                    )
                else:
                    # GENERAL→GENERAL non-conversational: just start new
                    return SupervisorDecision(
                        action=SupervisorAction.START_NEW,
                        target_domain=OrchestrationDomain.GENERAL,
                        transition_reason="Non-conversational general request from non-suspendable domain."
                    )
            else:
                # ── CRITICAL FIX: check if target_domain is suspended ──
                # If the target domain was previously suspended (and still has
                # SUSPENDED typed state), we RESUME it instead of issuing
                # another SUSPEND_AND_SWITCH.
                if (target_domain in state.suspended_domains
                        and Supervisor._get_domain_status(state, target_domain) == DomainWorkflowStatus.SUSPENDED):
                    return SupervisorDecision(
                        action=SupervisorAction.RESUME,
                        target_domain=target_domain,
                        transition_reason="Resuming previously suspended domain.",
                        transition_metadata=TransitionMetadata(resumed_domain=target_domain)
                    )
                if active_is_suspendable:
                    return SupervisorDecision(
                        action=SupervisorAction.SUSPEND_AND_SWITCH,
                        target_domain=target_domain,
                        transition_reason="Switching to new domain.",
                        transition_metadata=TransitionMetadata(suspended_domain=active_enum)
                    )
                else:
                    # Active domain (GENERAL/ESCALATION) has no typed state
                    # to suspend — just start the new domain.
                    return SupervisorDecision(
                        action=SupervisorAction.START_NEW,
                        target_domain=target_domain,
                        transition_reason="Starting domain from non-suspendable active domain."
                    )

        # ── Active domain is SUSPENDED (legacy path: active_domain itself is suspended) ──
        if domain_status == DomainWorkflowStatus.SUSPENDED:
            if target_domain.value == state.active_domain:
                return SupervisorDecision(
                    action=SupervisorAction.RESUME,
                    target_domain=OrchestrationDomain(state.active_domain),
                    transition_reason="Resuming current suspended domain.",
                    transition_metadata=TransitionMetadata(resumed_domain=OrchestrationDomain(state.active_domain))
                )
            elif target_domain == OrchestrationDomain.GENERAL and semantic_intent in Supervisor.CONVERSATIONAL_INTENTS:
                return SupervisorDecision(
                    action=SupervisorAction.RESUME,
                    target_domain=OrchestrationDomain(state.active_domain),
                    transition_reason="Resuming current suspended domain via conversational intent.",
                    transition_metadata=TransitionMetadata(resumed_domain=OrchestrationDomain(state.active_domain))
                )
            else:
                return SupervisorDecision(
                    action=SupervisorAction.START_NEW,
                    target_domain=target_domain,
                    transition_reason="Starting new domain from an already suspended state."
                )

        return SupervisorDecision(
            action=SupervisorAction.REQUEST_CLARIFICATION,
            target_domain=OrchestrationDomain(state.active_domain) if state.active_domain else OrchestrationDomain.GENERAL,
            transition_reason="Unknown workflow state."
        )

    @staticmethod
    def _validate_decision(state: WorkflowState, decision: SupervisorDecision) -> None:
        """
        Typed decision-boundary validation.
        Raises InvalidSupervisorDecisionError for impossible action/metadata combinations.
        Uses only GlobalWorkflowStatus, DomainWorkflowStatus, active_domain, and
        suspended_domains — never legacy status enums.
        """
        meta = decision.transition_metadata

        if decision.action == SupervisorAction.RESUME:
            if not meta or not meta.resumed_domain:
                raise InvalidSupervisorDecisionError(
                    "RESUME requires transition_metadata.resumed_domain"
                )
            if meta.resumed_domain not in state.suspended_domains:
                raise InvalidSupervisorDecisionError(
                    f"RESUME target '{meta.resumed_domain.value}' is not in suspended_domains "
                    f"{[d.value for d in state.suspended_domains]}"
                )
            if decision.target_domain != meta.resumed_domain:
                raise InvalidSupervisorDecisionError(
                    f"RESUME target_domain '{decision.target_domain.value}' does not match "
                    f"resumed_domain '{meta.resumed_domain.value}'"
                )

        elif decision.action == SupervisorAction.SUSPEND_AND_SWITCH:
            if not meta or not meta.suspended_domain:
                raise InvalidSupervisorDecisionError(
                    "SUSPEND_AND_SWITCH requires transition_metadata.suspended_domain"
                )
            if state.active_domain and meta.suspended_domain.value != state.active_domain:
                raise InvalidSupervisorDecisionError(
                    f"SUSPEND_AND_SWITCH suspended_domain '{meta.suspended_domain.value}' "
                    f"does not match current active_domain '{state.active_domain}'"
                )
            if meta.suspended_domain not in _SUSPENDABLE_DOMAINS:
                raise InvalidSupervisorDecisionError(
                    f"Domain '{meta.suspended_domain.value}' is not a suspendable workflow domain"
                )

        elif decision.action == SupervisorAction.CONTINUE:
            if not state.active_domain:
                raise InvalidSupervisorDecisionError(
                    "CONTINUE requires an active workflow (active_domain is None)"
                )

        # Cross-cutting: metadata field mismatch
        if meta:
            if decision.action not in (SupervisorAction.RESUME,) and meta.resumed_domain:
                raise InvalidSupervisorDecisionError(
                    f"resumed_domain is set but action is {decision.action.value}, not RESUME"
                )
            if decision.action not in (SupervisorAction.SUSPEND_AND_SWITCH,) and meta.suspended_domain:
                raise InvalidSupervisorDecisionError(
                    f"suspended_domain is set but action is {decision.action.value}, not SUSPEND_AND_SWITCH"
                )

    @staticmethod
    def apply_decision(state: WorkflowState, decision: SupervisorDecision) -> WorkflowState:
        """
        Pure function to apply a SupervisorDecision to a WorkflowState.

        Mutation Boundary: Only active_domain, global_status, suspended_domains,
        and per-domain domain_status are modified. Domain facts (product_name,
        order_id, ticket IDs, etc.) are never mutated by the Supervisor.

        Raises InvalidSupervisorDecisionError for structurally impossible decisions.
        """
        # ── Validate decision integrity before any mutation ──
        Supervisor._validate_decision(state, decision)

        new_state = state.model_copy(deep=True)

        if decision.action == SupervisorAction.START_NEW:
            # If target was previously in suspended_domains, remove it to
            # prevent being simultaneously active + suspended.
            if decision.target_domain in new_state.suspended_domains:
                new_state.suspended_domains.remove(decision.target_domain)

            new_state.active_domain = decision.target_domain.value
            new_state.global_status = GlobalWorkflowStatus.IN_PROGRESS
            if decision.target_domain == OrchestrationDomain.PRODUCT:
                if not new_state.product_state:
                    new_state.product_state = ProductState()
                new_state.product_state.domain_status = DomainWorkflowStatus.IN_PROGRESS
            elif decision.target_domain == OrchestrationDomain.ORDER:
                if not new_state.order_state:
                    new_state.order_state = OrderState()
                new_state.order_state.domain_status = DomainWorkflowStatus.IN_PROGRESS
            elif decision.target_domain == OrchestrationDomain.PAYMENT:
                if not new_state.payment_state:
                    new_state.payment_state = PaymentState()
                new_state.payment_state.domain_status = DomainWorkflowStatus.IN_PROGRESS

        elif decision.action == SupervisorAction.SUSPEND_AND_SWITCH:
            sus_domain = decision.transition_metadata.suspended_domain
            # Suspend the typed domain state
            if sus_domain == OrchestrationDomain.PRODUCT and new_state.product_state:
                new_state.product_state.domain_status = DomainWorkflowStatus.SUSPENDED
            elif sus_domain == OrchestrationDomain.ORDER and new_state.order_state:
                new_state.order_state.domain_status = DomainWorkflowStatus.SUSPENDED
            elif sus_domain == OrchestrationDomain.PAYMENT and new_state.payment_state:
                new_state.payment_state.domain_status = DomainWorkflowStatus.SUSPENDED

            # Add to suspended_domains exactly once
            if sus_domain not in new_state.suspended_domains:
                new_state.suspended_domains.append(sus_domain)

            new_state.active_domain = decision.target_domain.value
            new_state.global_status = GlobalWorkflowStatus.IN_PROGRESS

            # Initialize target domain if needed
            if decision.target_domain == OrchestrationDomain.PRODUCT:
                if not new_state.product_state:
                    new_state.product_state = ProductState()
                new_state.product_state.domain_status = DomainWorkflowStatus.IN_PROGRESS
            elif decision.target_domain == OrchestrationDomain.ORDER:
                if not new_state.order_state:
                    new_state.order_state = OrderState()
                new_state.order_state.domain_status = DomainWorkflowStatus.IN_PROGRESS
            elif decision.target_domain == OrchestrationDomain.PAYMENT:
                if not new_state.payment_state:
                    new_state.payment_state = PaymentState()
                new_state.payment_state.domain_status = DomainWorkflowStatus.IN_PROGRESS

        elif decision.action == SupervisorAction.RESUME:
            res_domain = decision.transition_metadata.resumed_domain
            # Remove from suspended_domains
            if res_domain in new_state.suspended_domains:
                new_state.suspended_domains.remove(res_domain)

            # Set resumed domain to IN_PROGRESS
            if res_domain == OrchestrationDomain.PRODUCT and new_state.product_state:
                new_state.product_state.domain_status = DomainWorkflowStatus.IN_PROGRESS
            elif res_domain == OrchestrationDomain.ORDER and new_state.order_state:
                new_state.order_state.domain_status = DomainWorkflowStatus.IN_PROGRESS
            elif res_domain == OrchestrationDomain.PAYMENT and new_state.payment_state:
                new_state.payment_state.domain_status = DomainWorkflowStatus.IN_PROGRESS

            new_state.active_domain = decision.target_domain.value
            new_state.global_status = GlobalWorkflowStatus.IN_PROGRESS

        elif decision.action == SupervisorAction.CONTINUE:
            pass

        elif decision.action == SupervisorAction.ESCALATE:
            new_state.global_status = GlobalWorkflowStatus.ESCALATED
            new_state.active_domain = decision.target_domain.value
            if new_state.active_domain == OrchestrationDomain.PRODUCT and new_state.product_state:
                new_state.product_state.domain_status = DomainWorkflowStatus.ESCALATED
            elif new_state.active_domain == OrchestrationDomain.ORDER and new_state.order_state:
                new_state.order_state.domain_status = DomainWorkflowStatus.ESCALATED
            elif new_state.active_domain == OrchestrationDomain.PAYMENT and new_state.payment_state:
                new_state.payment_state.domain_status = DomainWorkflowStatus.ESCALATED

        elif decision.action == SupervisorAction.REQUEST_CLARIFICATION:
            new_state.active_domain = decision.target_domain.value

        return new_state
