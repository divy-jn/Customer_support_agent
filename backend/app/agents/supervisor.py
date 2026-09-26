from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional
from app.models import WorkflowState, WorkflowStatus

class SupervisorAction(str, Enum):
    CONTINUE = "continue"
    SUSPEND_AND_SWITCH = "suspend_and_switch"
    START_NEW = "start_new"
    RESUME = "resume"
    ESCALATE = "escalate"
    REQUEST_CLARIFICATION = "request_clarification"
    PROCEED_APPROVAL = "proceed_approval"

class SupervisorDecision(BaseModel):
    action: SupervisorAction
    target_domain: str
    resulting_workflow_status: WorkflowStatus
    transition_reason: str
    transition_metadata: dict = Field(default_factory=dict)

class Supervisor:
    """
    Deterministic Python Workflow Orchestrator.
    Evaluates semantic facts against existing WorkflowState to determine next action.
    Does not use LLMs, network calls, or tool execution.
    """

    @staticmethod
    def decide(semantic_domain: str, semantic_intent: str, state: WorkflowState, message: str) -> SupervisorDecision:
        # 1. Invalid or missing semantic domain -> Clarification
        if not semantic_domain:
            return SupervisorDecision(
                action=SupervisorAction.REQUEST_CLARIFICATION,
                target_domain=state.active_domain or "general",
                resulting_workflow_status=state.workflow_status if state.active_domain else WorkflowStatus.IDLE,
                transition_reason="Ambiguous or missing semantic domain."
            )
            
        # 2. Terminal State: ESCALATED
        if state.workflow_status == WorkflowStatus.ESCALATED:
            return SupervisorDecision(
                action=SupervisorAction.ESCALATE,
                target_domain=state.active_domain or semantic_domain,
                resulting_workflow_status=WorkflowStatus.ESCALATED,
                transition_reason="Workflow is escalated, terminal state."
            )
            
        # 3. Explicit Escalation Intent
        if semantic_domain == "escalation" or semantic_intent == "escalation":
            return SupervisorDecision(
                action=SupervisorAction.ESCALATE,
                target_domain=state.active_domain or "general",
                resulting_workflow_status=WorkflowStatus.ESCALATED,
                transition_reason="Semantic escalation requested."
            )

        # 4. Completed Workflow
        if state.workflow_status == WorkflowStatus.COMPLETED:
            # Does not automatically resume. Treat as a new event.
            return SupervisorDecision(
                action=SupervisorAction.START_NEW,
                target_domain=semantic_domain,
                resulting_workflow_status=WorkflowStatus.IN_PROGRESS,
                transition_reason="Completed workflow followed by new message."
            )

        # 5. IDLE, FAILED, or no active domain
        if state.workflow_status in (WorkflowStatus.IDLE, WorkflowStatus.FAILED) or not state.active_domain:
            return SupervisorDecision(
                action=SupervisorAction.START_NEW,
                target_domain=semantic_domain,
                resulting_workflow_status=WorkflowStatus.IN_PROGRESS,
                transition_reason="Starting new workflow from idle/failed state."
            )

        # 6. Active Workflow (IN_PROGRESS or AWAITING_INPUT)
        if state.workflow_status in (WorkflowStatus.IN_PROGRESS, WorkflowStatus.AWAITING_INPUT):
            if semantic_domain == state.active_domain or semantic_domain == "general":
                # Check pending approval pseudo-resume
                if state.workflow_status == WorkflowStatus.AWAITING_INPUT and state.pending_approval:
                    # In F.1, transport intercept is decoupled, so supervisor might PROCEED_APPROVAL
                    # if semantic_intent indicates confirmation, but for now we just CONTINUE.
                    pass
                    
                return SupervisorDecision(
                    action=SupervisorAction.CONTINUE,
                    target_domain=state.active_domain,
                    resulting_workflow_status=state.workflow_status,
                    transition_reason="Domain matches active workflow."
                )
            else:
                # Domain Switch!
                if semantic_domain in state.suspended_domains:
                    return SupervisorDecision(
                        action=SupervisorAction.RESUME,
                        target_domain=semantic_domain,
                        resulting_workflow_status=WorkflowStatus.RESUMED,
                        transition_reason="Resuming previously suspended domain.",
                        transition_metadata={"suspended_domain": state.active_domain}
                    )
                else:
                    return SupervisorDecision(
                        action=SupervisorAction.SUSPEND_AND_SWITCH,
                        target_domain=semantic_domain,
                        resulting_workflow_status=WorkflowStatus.IN_PROGRESS,
                        transition_reason="Switching to new domain.",
                        transition_metadata={"suspended_domain": state.active_domain}
                    )

        # 7. Suspended workflow logic (If somehow the active domain itself is in SUSPENDED state)
        if state.workflow_status == WorkflowStatus.SUSPENDED:
            if semantic_domain == state.active_domain:
                return SupervisorDecision(
                    action=SupervisorAction.RESUME,
                    target_domain=state.active_domain,
                    resulting_workflow_status=WorkflowStatus.RESUMED,
                    transition_reason="Resuming current suspended domain."
                )
            elif semantic_domain in state.suspended_domains:
                return SupervisorDecision(
                    action=SupervisorAction.RESUME,
                    target_domain=semantic_domain,
                    resulting_workflow_status=WorkflowStatus.RESUMED,
                    transition_reason="Resuming another suspended domain.",
                    transition_metadata={"suspended_domain": state.active_domain}
                )
            else:
                return SupervisorDecision(
                    action=SupervisorAction.START_NEW,
                    target_domain=semantic_domain,
                    resulting_workflow_status=WorkflowStatus.IN_PROGRESS,
                    transition_reason="Starting new domain from suspended state.",
                    transition_metadata={"suspended_domain": state.active_domain}
                )
                
        # 8. RESUMED -> IN_PROGRESS happens transparently if continue is requested
        if state.workflow_status == WorkflowStatus.RESUMED:
             if semantic_domain == state.active_domain or semantic_domain == "general":
                 return SupervisorDecision(
                     action=SupervisorAction.CONTINUE,
                     target_domain=state.active_domain,
                     resulting_workflow_status=WorkflowStatus.IN_PROGRESS, # progresses to IN_PROGRESS
                     transition_reason="Advancing resumed workflow."
                 )
             else:
                 return SupervisorDecision(
                     action=SupervisorAction.SUSPEND_AND_SWITCH,
                     target_domain=semantic_domain,
                     resulting_workflow_status=WorkflowStatus.IN_PROGRESS,
                     transition_reason="Switching immediately after resuming.",
                     transition_metadata={"suspended_domain": state.active_domain}
                 )

        # Fallback for unknown states
        return SupervisorDecision(
            action=SupervisorAction.REQUEST_CLARIFICATION,
            target_domain=state.active_domain or "general",
            resulting_workflow_status=WorkflowStatus.IDLE,
            transition_reason="Unknown workflow state."
        )

    @staticmethod
    def apply_decision(state: WorkflowState, decision: SupervisorDecision) -> WorkflowState:
        """
        Pure function to apply a SupervisorDecision to a WorkflowState.
        Returns a new WorkflowState instance.
        Mutation Boundary: Only metadata is modified.
        """
        new_state = state.model_copy(deep=True)
        
        if decision.action == SupervisorAction.SUSPEND_AND_SWITCH:
            if new_state.active_domain and new_state.active_domain not in new_state.suspended_domains:
                new_state.suspended_domains.append(new_state.active_domain)
            new_state.active_domain = decision.target_domain
            new_state.workflow_status = decision.resulting_workflow_status
            
        elif decision.action == SupervisorAction.RESUME:
            if new_state.active_domain and new_state.active_domain != decision.target_domain:
                 if new_state.active_domain not in new_state.suspended_domains:
                     new_state.suspended_domains.append(new_state.active_domain)
            if decision.target_domain in new_state.suspended_domains:
                new_state.suspended_domains.remove(decision.target_domain)
            new_state.active_domain = decision.target_domain
            new_state.workflow_status = decision.resulting_workflow_status
            
        elif decision.action == SupervisorAction.START_NEW:
            # Overwrite active domain completely
            new_state.active_domain = decision.target_domain
            new_state.workflow_status = decision.resulting_workflow_status
            
        elif decision.action == SupervisorAction.ESCALATE:
            new_state.workflow_status = WorkflowStatus.ESCALATED
            
        elif decision.action == SupervisorAction.CONTINUE:
            new_state.workflow_status = decision.resulting_workflow_status
            
        new_state.transition_metadata = decision.transition_metadata
        return new_state
