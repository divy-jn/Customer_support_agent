from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional
from app.models import (
    WorkflowState, WorkflowStatus, OrchestrationDomain,
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

class Supervisor:
    """
    Deterministic Python Workflow Orchestrator.
    Evaluates semantic facts against existing WorkflowState to determine next action.
    F.1 Boundary: Does not use LLMs, network calls, tool execution, or F.2 multi-domain structures.
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
    def _get_active_domain_status(state: WorkflowState) -> DomainWorkflowStatus | None:
        if not state.active_domain:
            return None
        if state.active_domain == OrchestrationDomain.PRODUCT and state.product_state:
            return state.product_state.domain_status
        if state.active_domain == OrchestrationDomain.ORDER and state.order_state:
            return state.order_state.domain_status
        if state.active_domain == OrchestrationDomain.PAYMENT and state.payment_state:
            return state.payment_state.domain_status
        return None

    @staticmethod
    def decide(semantic_domain: str, semantic_intent: str, state: WorkflowState, message: str) -> SupervisorDecision:
        if state.active_domain is not None and not Supervisor._is_valid_domain(state.active_domain):
            return SupervisorDecision(
                action=SupervisorAction.REQUEST_CLARIFICATION,
                target_domain=OrchestrationDomain.GENERAL,
                transition_reason=f"Invalid active_domain in state: '{state.active_domain}'"
            )
            
        if state.global_status not in list(GlobalWorkflowStatus):
            return SupervisorDecision(
                action=SupervisorAction.REQUEST_CLARIFICATION,
                target_domain=OrchestrationDomain(state.active_domain) if state.active_domain else OrchestrationDomain.GENERAL,
                transition_reason=f"Unknown global_status: '{state.global_status}'"
            )
            
        if not semantic_domain or not Supervisor._is_valid_domain(semantic_domain):
            return SupervisorDecision(
                action=SupervisorAction.REQUEST_CLARIFICATION,
                target_domain=OrchestrationDomain(state.active_domain) if state.active_domain else OrchestrationDomain.GENERAL,
                transition_reason=f"Ambiguous, missing, or unknown semantic domain: '{semantic_domain}'"
            )
            
        target_domain = OrchestrationDomain(semantic_domain)

        if state.global_status == GlobalWorkflowStatus.ESCALATED:
            return SupervisorDecision(
                action=SupervisorAction.ESCALATE,
                target_domain=OrchestrationDomain(state.active_domain) if state.active_domain else target_domain,
                transition_reason="Workflow is escalated, terminal state."
            )
            
        if target_domain == OrchestrationDomain.ESCALATION or semantic_intent == "escalation":
            return SupervisorDecision(
                action=SupervisorAction.ESCALATE,
                target_domain=OrchestrationDomain(state.active_domain) if state.active_domain else OrchestrationDomain.GENERAL,
                transition_reason="Semantic escalation requested."
            )

        domain_status = Supervisor._get_active_domain_status(state)
        is_active_general = (state.active_domain == OrchestrationDomain.GENERAL.value and state.global_status == GlobalWorkflowStatus.IN_PROGRESS)

        if domain_status == DomainWorkflowStatus.COMPLETED:
            return SupervisorDecision(
                action=SupervisorAction.START_NEW,
                target_domain=target_domain,
                transition_reason="Completed workflow followed by new message."
            )

        if state.global_status == GlobalWorkflowStatus.IDLE or domain_status == DomainWorkflowStatus.FAILED or not state.active_domain:
            return SupervisorDecision(
                action=SupervisorAction.START_NEW,
                target_domain=target_domain,
                transition_reason="Starting new workflow from idle/failed state."
            )

        if domain_status in (DomainWorkflowStatus.IN_PROGRESS, DomainWorkflowStatus.AWAITING_INPUT) or is_active_general:
            if target_domain.value == state.active_domain:
                return SupervisorDecision(
                    action=SupervisorAction.CONTINUE,
                    target_domain=OrchestrationDomain(state.active_domain),
                    transition_reason="Domain matches active workflow."
                )
            elif target_domain == OrchestrationDomain.GENERAL:
                if semantic_intent in Supervisor.CONVERSATIONAL_INTENTS:
                    return SupervisorDecision(
                        action=SupervisorAction.CONTINUE,
                        target_domain=OrchestrationDomain(state.active_domain),
                        transition_reason="Conversational intent continues active workflow."
                    )
                else:
                    return SupervisorDecision(
                        action=SupervisorAction.SUSPEND_AND_SWITCH,
                        target_domain=OrchestrationDomain.GENERAL,
                        transition_reason="Unrelated general request suspends active workflow.",
                        transition_metadata=TransitionMetadata(suspended_domain=OrchestrationDomain(state.active_domain))
                    )
            else:
                return SupervisorDecision(
                    action=SupervisorAction.SUSPEND_AND_SWITCH,
                    target_domain=target_domain,
                    transition_reason="Switching to new domain.",
                    transition_metadata=TransitionMetadata(suspended_domain=OrchestrationDomain(state.active_domain))
                )

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
    def _apply_legacy_status_projection(state: WorkflowState):
        """Retain projection so unmigrated tests/consumers don't break."""
        if state.global_status == GlobalWorkflowStatus.ESCALATED:
            state.workflow_status = WorkflowStatus.ESCALATED
            return
        elif state.global_status == GlobalWorkflowStatus.IDLE:
            state.workflow_status = WorkflowStatus.IDLE
            return
            
        domain_status = Supervisor._get_active_domain_status(state)
        if not domain_status:
            state.workflow_status = WorkflowStatus.IN_PROGRESS
            return
            
        mapping = {
            DomainWorkflowStatus.IN_PROGRESS: WorkflowStatus.IN_PROGRESS,
            DomainWorkflowStatus.AWAITING_INPUT: WorkflowStatus.AWAITING_INPUT,
            DomainWorkflowStatus.SUSPENDED: WorkflowStatus.SUSPENDED,
            DomainWorkflowStatus.COMPLETED: WorkflowStatus.COMPLETED,
            DomainWorkflowStatus.FAILED: WorkflowStatus.FAILED,
            DomainWorkflowStatus.ESCALATED: WorkflowStatus.ESCALATED
        }
        state.workflow_status = mapping.get(domain_status, WorkflowStatus.IN_PROGRESS)

    @staticmethod
    def apply_decision(state: WorkflowState, decision: SupervisorDecision) -> WorkflowState:
        """
        Pure function to apply a SupervisorDecision to a WorkflowState.
        Mutation Boundary: Only active_domain, global_status, suspended_domains, and domain_status are modified.
        F.2 will handle multi-domain state updates.
        """
        new_state = state.model_copy(deep=True)
        
        if decision.action == SupervisorAction.START_NEW:
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
            if decision.transition_metadata and decision.transition_metadata.suspended_domain:
                sus_domain = decision.transition_metadata.suspended_domain
                if sus_domain == OrchestrationDomain.PRODUCT and new_state.product_state:
                    new_state.product_state.domain_status = DomainWorkflowStatus.SUSPENDED
                elif sus_domain == OrchestrationDomain.ORDER and new_state.order_state:
                    new_state.order_state.domain_status = DomainWorkflowStatus.SUSPENDED
                elif sus_domain == OrchestrationDomain.PAYMENT and new_state.payment_state:
                    new_state.payment_state.domain_status = DomainWorkflowStatus.SUSPENDED
                    
                if sus_domain not in new_state.suspended_domains:
                    new_state.suspended_domains.append(sus_domain)

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

        elif decision.action == SupervisorAction.RESUME:
            if decision.transition_metadata and decision.transition_metadata.resumed_domain:
                res_domain = decision.transition_metadata.resumed_domain
                if res_domain in new_state.suspended_domains:
                    new_state.suspended_domains.remove(res_domain)
                    
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
            
        Supervisor._apply_legacy_status_projection(new_state)
            
        return new_state
