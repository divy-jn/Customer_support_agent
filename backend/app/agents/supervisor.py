from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional
from app.models import WorkflowState, WorkflowStatus

class OrchestrationDomain(str, Enum):
    PRODUCT = "product"
    ORDER = "order"
    PAYMENT = "payment"
    GENERAL = "general"
    ESCALATION = "escalation"

class SupervisorAction(str, Enum):
    CONTINUE = "continue"
    SUSPEND_AND_SWITCH = "suspend_and_switch"
    START_NEW = "start_new"
    RESUME = "resume"
    ESCALATE = "escalate"
    REQUEST_CLARIFICATION = "request_clarification"
    # Note: PROCEED_APPROVAL is intentionally deferred to Phase F.8

class TransitionMetadata(BaseModel):
    """Bounded typed representation of transition context."""
    suspended_domain: Optional[OrchestrationDomain] = None
    resumed_domain: Optional[OrchestrationDomain] = None

class SupervisorDecision(BaseModel):
    action: SupervisorAction
    target_domain: OrchestrationDomain
    resulting_workflow_status: WorkflowStatus
    transition_reason: str
    transition_metadata: Optional[TransitionMetadata] = None

class Supervisor:
    """
    Deterministic Python Workflow Orchestrator.
    Evaluates semantic facts against existing WorkflowState to determine next action.
    F.1 Boundary: Does not use LLMs, network calls, tool execution, or F.2 multi-domain structures.
    """

    @staticmethod
    def _is_valid_domain(domain_str: str) -> bool:
        try:
            OrchestrationDomain(domain_str)
            return True
        except ValueError:
            return False

    @staticmethod
    def decide(semantic_domain: str, semantic_intent: str, state: WorkflowState, message: str) -> SupervisorDecision:
        # 0. Validate existing state.active_domain
        if state.active_domain is not None and not Supervisor._is_valid_domain(state.active_domain):
            return SupervisorDecision(
                action=SupervisorAction.REQUEST_CLARIFICATION,
                target_domain=OrchestrationDomain.GENERAL,
                resulting_workflow_status=WorkflowStatus.IDLE,
                transition_reason=f"Invalid active_domain in state: '{state.active_domain}'"
            )
            
        # 1. Invalid, missing, or unknown semantic domain -> Clarification
        if not semantic_domain or not Supervisor._is_valid_domain(semantic_domain):
            return SupervisorDecision(
                action=SupervisorAction.REQUEST_CLARIFICATION,
                target_domain=OrchestrationDomain(state.active_domain) if state.active_domain else OrchestrationDomain.GENERAL,
                resulting_workflow_status=state.workflow_status if state.active_domain else WorkflowStatus.IDLE,
                transition_reason=f"Ambiguous, missing, or unknown semantic domain: '{semantic_domain}'"
            )
            
        target_domain = OrchestrationDomain(semantic_domain)

        # 2. Terminal State: ESCALATED
        if state.workflow_status == WorkflowStatus.ESCALATED:
            return SupervisorDecision(
                action=SupervisorAction.ESCALATE,
                target_domain=OrchestrationDomain(state.active_domain) if state.active_domain else target_domain,
                resulting_workflow_status=WorkflowStatus.ESCALATED,
                transition_reason="Workflow is escalated, terminal state."
            )
            
        # 3. Explicit Escalation Intent
        if target_domain == OrchestrationDomain.ESCALATION or semantic_intent == "escalation":
            return SupervisorDecision(
                action=SupervisorAction.ESCALATE,
                target_domain=OrchestrationDomain(state.active_domain) if state.active_domain else OrchestrationDomain.GENERAL,
                resulting_workflow_status=WorkflowStatus.ESCALATED,
                transition_reason="Semantic escalation requested."
            )

        # 4. Completed Workflow
        if state.workflow_status == WorkflowStatus.COMPLETED:
            # Does not automatically resume. Treat as a new event.
            return SupervisorDecision(
                action=SupervisorAction.START_NEW,
                target_domain=target_domain,
                resulting_workflow_status=WorkflowStatus.IN_PROGRESS,
                transition_reason="Completed workflow followed by new message."
            )

        # 5. IDLE or FAILED or No active domain
        if state.workflow_status in (WorkflowStatus.IDLE, WorkflowStatus.FAILED) or not state.active_domain:
            return SupervisorDecision(
                action=SupervisorAction.START_NEW,
                target_domain=target_domain,
                resulting_workflow_status=WorkflowStatus.IN_PROGRESS,
                transition_reason="Starting new workflow from idle/failed state."
            )

        # 6. Active Workflow (IN_PROGRESS or AWAITING_INPUT)
        if state.workflow_status in (WorkflowStatus.IN_PROGRESS, WorkflowStatus.AWAITING_INPUT):
            if target_domain.value == state.active_domain or target_domain == OrchestrationDomain.GENERAL:
                return SupervisorDecision(
                    action=SupervisorAction.CONTINUE,
                    target_domain=OrchestrationDomain(state.active_domain),
                    resulting_workflow_status=state.workflow_status,
                    transition_reason="Domain matches active workflow."
                )
            else:
                # Domain Switch
                return SupervisorDecision(
                    action=SupervisorAction.SUSPEND_AND_SWITCH,
                    target_domain=target_domain,
                    resulting_workflow_status=WorkflowStatus.IN_PROGRESS,
                    transition_reason="Switching to new domain.",
                    transition_metadata=TransitionMetadata(suspended_domain=OrchestrationDomain(state.active_domain))
                )

        # 7. Suspended workflow logic
        if state.workflow_status == WorkflowStatus.SUSPENDED:
            if target_domain.value == state.active_domain or target_domain == OrchestrationDomain.GENERAL:
                return SupervisorDecision(
                    action=SupervisorAction.RESUME,
                    target_domain=OrchestrationDomain(state.active_domain),
                    resulting_workflow_status=WorkflowStatus.RESUMED,
                    transition_reason="Resuming current suspended domain.",
                    transition_metadata=TransitionMetadata(resumed_domain=OrchestrationDomain(state.active_domain))
                )
            else:
                # Already suspended, switching to something else
                return SupervisorDecision(
                    action=SupervisorAction.START_NEW,
                    target_domain=target_domain,
                    resulting_workflow_status=WorkflowStatus.IN_PROGRESS,
                    transition_reason="Starting new domain from an already suspended state."
                )
                
        # 8. RESUMED -> IN_PROGRESS
        if state.workflow_status == WorkflowStatus.RESUMED:
             if target_domain.value == state.active_domain or target_domain == OrchestrationDomain.GENERAL:
                 return SupervisorDecision(
                     action=SupervisorAction.CONTINUE,
                     target_domain=OrchestrationDomain(state.active_domain),
                     resulting_workflow_status=WorkflowStatus.IN_PROGRESS,
                     transition_reason="Advancing resumed workflow."
                 )
             else:
                 return SupervisorDecision(
                     action=SupervisorAction.SUSPEND_AND_SWITCH,
                     target_domain=target_domain,
                     resulting_workflow_status=WorkflowStatus.IN_PROGRESS,
                     transition_reason="Switching immediately after resuming.",
                     transition_metadata=TransitionMetadata(suspended_domain=OrchestrationDomain(state.active_domain))
                 )

        # Fallback for unknown states
        return SupervisorDecision(
            action=SupervisorAction.REQUEST_CLARIFICATION,
            target_domain=OrchestrationDomain(state.active_domain) if state.active_domain else OrchestrationDomain.GENERAL,
            resulting_workflow_status=WorkflowStatus.IDLE,
            transition_reason="Unknown workflow state."
        )

    @staticmethod
    def apply_decision(state: WorkflowState, decision: SupervisorDecision) -> WorkflowState:
        """
        Pure function to apply a SupervisorDecision to a WorkflowState.
        Mutation Boundary: Only workflow metadata (active_domain, workflow_status) is modified.
        F.2 will handle multi-domain state updates.
        """
        new_state = state.model_copy(deep=True)
        
        if decision.action in (SupervisorAction.SUSPEND_AND_SWITCH, SupervisorAction.START_NEW, SupervisorAction.RESUME, SupervisorAction.CONTINUE):
            new_state.active_domain = decision.target_domain.value
            new_state.workflow_status = decision.resulting_workflow_status
            
        elif decision.action == SupervisorAction.ESCALATE:
            new_state.workflow_status = WorkflowStatus.ESCALATED
            
        return new_state
