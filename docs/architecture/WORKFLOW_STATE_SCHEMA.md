# WORKFLOW STATE SCHEMA (Phase F.2 Proposed)

> **NOTE:** This is a Python/Pydantic pseudocode proposal only. Do not implement this in code yet.

```python
from datetime import datetime
from pydantic import BaseModel, Field, model_validator
from enum import Enum
from typing import Optional

# ---------------------------------------------------------
# ENUMS
# ---------------------------------------------------------

class OrchestrationDomain(str, Enum):
    PRODUCT = "product"
    ORDER = "order"
    PAYMENT = "payment"
    GENERAL = "general"
    ESCALATION = "escalation"

class GlobalWorkflowStatus(str, Enum):
    IDLE = "idle"
    IN_PROGRESS = "in_progress"
    ESCALATED = "escalated"

class DomainWorkflowStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"

# ---------------------------------------------------------
# DOMAIN MODELS (Strongly Typed, Bounded)
# ---------------------------------------------------------

class ProductState(BaseModel):
    domain_status: DomainWorkflowStatus = DomainWorkflowStatus.IN_PROGRESS
    active_ticket_id: Optional[int] = None
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    manufacturer: Optional[str] = None
    last_tool: Optional[str] = None
    last_tool_result: Optional[str] = None
    # Add bounded fields as required by ProductAgent

class OrderState(BaseModel):
    domain_status: DomainWorkflowStatus = DomainWorkflowStatus.IN_PROGRESS
    active_ticket_id: Optional[int] = None
    order_id: Optional[str] = None
    tracking_number: Optional[str] = None
    last_tool: Optional[str] = None
    last_tool_result: Optional[str] = None
    # Add bounded fields as required by OrderAgent

class PaymentState(BaseModel):
    domain_status: DomainWorkflowStatus = DomainWorkflowStatus.IN_PROGRESS
    active_ticket_id: Optional[int] = None
    transaction_id: Optional[str] = None
    payment_method: Optional[str] = None
    last_tool: Optional[str] = None
    last_tool_result: Optional[str] = None
    # Add bounded fields as required by PaymentAgent

# ---------------------------------------------------------
# GLOBAL STATE
# ---------------------------------------------------------

class PendingAction(BaseModel):
    """(For F.8) Bounded approval structure"""
    action_type: str
    target_domain: OrchestrationDomain
    payload: dict  # Bounded in future specific models if needed

class WorkflowState(BaseModel):
    # Global Identity
    session_id: str
    customer_id: int
    
    # Global Orchestration (Owned by Supervisor)
    global_status: GlobalWorkflowStatus = GlobalWorkflowStatus.IDLE
    active_domain: Optional[OrchestrationDomain] = None
    suspended_domains: list[OrchestrationDomain] = Field(default_factory=list, max_length=3)
    pending_approval: Optional[PendingAction] = None

    # Domain Fact Storage (Owned by Domain Agents)
    product_state: Optional[ProductState] = None
    order_state: Optional[OrderState] = None
    payment_state: Optional[PaymentState] = None
    
    # Generic Metadata
    turn_count: int = 0
    updated_at: Optional[datetime] = None
    pending_input: Optional[str] = None

    # Validators to enforce impossible states
    @model_validator(mode='after')
    def validate_impossible_states(self) -> 'WorkflowState':
        # 1. Enforce active_domain has instantiated state
        if self.active_domain == OrchestrationDomain.PRODUCT and not self.product_state:
            raise ValueError("PRODUCT domain is active but product_state is None.")
        if self.active_domain == OrchestrationDomain.ORDER and not self.order_state:
            raise ValueError("ORDER domain is active but order_state is None.")
        if self.active_domain == OrchestrationDomain.PAYMENT and not self.payment_state:
            raise ValueError("PAYMENT domain is active but payment_state is None.")
        
        # 2. Enforce ESCALATED terminal state bounds (if any specific rules apply)
        if self.global_status == GlobalWorkflowStatus.ESCALATED and self.active_domain is None:
            # Escalations generally preserve the active_domain at the time of failure
            pass
            
        return self
```
