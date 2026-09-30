# WORKFLOW STATE SCHEMA (Phase F.2 Proposed)

> **NOTE:** This schema represents the implemented F.2 multi-domain workflow state.

## Legacy Compatibility (Inbound Adapter)

To safely bridge the existing legacy runtime (which passes a flat, untyped `dict` around) with the F.2 `WorkflowState`, an explicit inbound adapter is implemented in `models.py`:
- `WorkflowState.from_legacy(legacy_dict)`

There is NO outbound legacy projection. Normal persistence uses the typed `WorkflowState` serialization, and there is no supported `WorkflowState` -> legacy flat dictionary adapter.

### Fail-Closed Behavior & Intentional Omissions
The adapter strictly enforces domain isolation:
- **Unknown Status:** Any unsupported legacy `workflow_status` (e.g., `"garbage"`) fails closed immediately, rejecting silent normalization.
- **Contradictory Domain Facts:** If legacy state presents conflicting facts across boundaries (e.g., `active_domain="product"` alongside an `order_id`), it is firmly rejected. The adapter never silently discards meaningful domain facts; inputs must be clean.
- **GENERAL & ESCALATION Isolation:** The `GENERAL` and `ESCALATION` domains are explicitly prevented from inheriting `product_id`, `order_id`, or `active_ticket_id` via legacy dictionaries, guaranteeing they do not unsafely hold cross-domain metadata where no domain state can structurally represent it.

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
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    # RESUMED is an action, not a persistent status.

# ---------------------------------------------------------
# DOMAIN MODELS (Strongly Typed, Bounded)
# ---------------------------------------------------------
# Invariant: Each session may have at most ONE live workflow instance for each supported domain.

class ProductState(BaseModel):
    domain_status: DomainWorkflowStatus = DomainWorkflowStatus.IN_PROGRESS
    active_ticket_id: Optional[int] = None
    product_id: Optional[int] = None
    product_name: Optional[str] = Field(None, max_length=255)
    manufacturer: Optional[str] = Field(None, max_length=255)
    last_tool: Optional[str] = Field(None, max_length=255)
    last_tool_result: Optional[str] = Field(None, max_length=1024)

class OrderState(BaseModel):
    domain_status: DomainWorkflowStatus = DomainWorkflowStatus.IN_PROGRESS
    active_ticket_id: Optional[int] = None
    order_id: Optional[str] = Field(None, max_length=255)
    tracking_number: Optional[str] = Field(None, max_length=255)
    last_tool: Optional[str] = Field(None, max_length=255)
    last_tool_result: Optional[str] = Field(None, max_length=1024)

class PaymentState(BaseModel):
    domain_status: DomainWorkflowStatus = DomainWorkflowStatus.IN_PROGRESS
    active_ticket_id: Optional[int] = None
    transaction_id: Optional[str] = Field(None, max_length=255)
    payment_method: Optional[str] = Field(None, max_length=255)
    last_tool: Optional[str] = Field(None, max_length=255)
    last_tool_result: Optional[str] = Field(None, max_length=1024)

# ---------------------------------------------------------
# GLOBAL STATE
# ---------------------------------------------------------

class WorkflowState(BaseModel):
    # Versioning & Concurrency
    schema_version: int = 1
    state_revision: int = 1
    
    # Global Identity
    session_id: str
    customer_id: int
    
    # Global Orchestration (Owned by Supervisor)
    global_status: GlobalWorkflowStatus = GlobalWorkflowStatus.IDLE
    active_domain: Optional[OrchestrationDomain] = None
    suspended_domains: list[OrchestrationDomain] = Field(default_factory=list, max_length=3)
    # NOTE: PendingAction/Approval dict has been REMOVED. F.8 will handle strongly typed approvals.

    # Domain Fact Storage (At most 1 per domain, Owned by Domain Agents)
    product_state: Optional[ProductState] = None
    order_state: Optional[OrderState] = None
    payment_state: Optional[PaymentState] = None
    
    # Generic Metadata
    turn_count: int = 0
    updated_at: Optional[datetime] = None
    pending_input: Optional[str] = None

    # Migration adapters
    @classmethod
    def from_legacy(cls, legacy_dict: dict) -> 'WorkflowState':
        """Explicitly inflate flat legacy state into typed DomainStates. (F.2.3)"""
        pass

    # Fail-closed validators
    @model_validator(mode='after')
    def validate_impossible_states(self) -> 'WorkflowState':
        # 1. Enforce active_domain has instantiated state
        if self.active_domain == OrchestrationDomain.PRODUCT and not self.product_state:
            raise ValueError("PRODUCT domain is active but product_state is None.")
        if self.active_domain == OrchestrationDomain.ORDER and not self.order_state:
            raise ValueError("ORDER domain is active but order_state is None.")
        if self.active_domain == OrchestrationDomain.PAYMENT and not self.payment_state:
            raise ValueError("PAYMENT domain is active but payment_state is None.")
            
        # 2. Suspended domains must have instantiated state
        for d in self.suspended_domains:
            if d == OrchestrationDomain.PRODUCT and not self.product_state:
                raise ValueError("PRODUCT is suspended but state is missing.")
            if d == OrchestrationDomain.ORDER and not self.order_state:
                raise ValueError("ORDER is suspended but state is missing.")
            if d == OrchestrationDomain.PAYMENT and not self.payment_state:
                raise ValueError("PAYMENT is suspended but state is missing.")
                
        # 3. Duplicate suspended domains are rejected
        if len(self.suspended_domains) != len(set(self.suspended_domains)):
            raise ValueError("Duplicate suspended domains detected.")
            
        # 4. Active domain cannot be COMPLETED or FAILED without explicit recovery handling
        if self.active_domain == OrchestrationDomain.PRODUCT and self.product_state.domain_status in (DomainWorkflowStatus.COMPLETED, DomainWorkflowStatus.FAILED):
            raise ValueError("Active domain PRODUCT cannot have COMPLETED or FAILED status.")
        if self.active_domain == OrchestrationDomain.ORDER and self.order_state.domain_status in (DomainWorkflowStatus.COMPLETED, DomainWorkflowStatus.FAILED):
            raise ValueError("Active domain ORDER cannot have COMPLETED or FAILED status.")
        if self.active_domain == OrchestrationDomain.PAYMENT and self.payment_state.domain_status in (DomainWorkflowStatus.COMPLETED, DomainWorkflowStatus.FAILED):
            raise ValueError("Active domain PAYMENT cannot have COMPLETED or FAILED status.")

        # 5. Schema constraints
        if self.schema_version < 1 or self.state_revision < 1:
            raise ValueError("Invalid schema_version or state_revision.")
            
        return self
```
