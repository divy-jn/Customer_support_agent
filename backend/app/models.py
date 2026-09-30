"""
Pydantic schemas for API request/response validation.
"""

from pydantic import BaseModel, Field, model_validator
from datetime import datetime
from enum import Enum
from typing import Any


# ──────────────────────────────────────────────
#  Enums
# ──────────────────────────────────────────────

class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    PENDING_CUSTOMER = "pending_customer"
    ESCALATED = "escalated"
    CLOSED = "closed"


class TicketPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TicketType(str, Enum):
    TECHNICAL_ISSUE = "technical_issue"
    BILLING = "billing"
    REFUND = "refund"
    CANCELLATION = "cancellation"
    INQUIRY = "inquiry"


class OrderStatus(str, Enum):
    ACTIVE = "active"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class Urgency(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WarrantyStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    MISSING_DATA = "missing_data"
    INVALID_CUSTOMER = "invalid_customer"
    INVALID_REQUEST = "invalid_request"
    DB_ERROR = "db_error"


# ──────────────────────────────────────────────
#  Chat Schemas
# ──────────────────────────────────────────────

class ChatMessage(BaseModel):
    """A single message in the chat."""
    role: str            # "customer" or "agent" or "system"
    content: str
    timestamp: datetime | None = None


class ChatRequest(BaseModel):
    """Incoming WebSocket message from the customer."""
    message: str
    customer_id: int | None = None
    session_id: str | None = None


class ChatResponse(BaseModel):
    """Outgoing WebSocket message to the customer."""
    message: str
    intent: str | None = None
    sentiment: str | None = None
    urgency: str | None = None
    escalated: bool = False
    agent_name: str = "Adi"
    timestamp: datetime | None = None


# ──────────────────────────────────────────────
#  Customer Schemas
# ──────────────────────────────────────────────

class CustomerProfile(BaseModel):
    id: int
    name: str
    email: str
    age: int | None = None
    gender: str | None = None
    created_at: datetime | None = None
    total_orders: int = 0
    open_tickets: int = 0
    avg_satisfaction: float | None = None


# ──────────────────────────────────────────────
#  Warranty Schemas
# ──────────────────────────────────────────────

class WarrantyStatusResult(BaseModel):
    status: WarrantyStatus
    eligible_purchase: bool | None = None
    customer_id: int | None = None
    order_id: int | None = None
    product_id: int | None = None
    product_name: str | None = None
    purchase_date: datetime | None = None
    warranty_period: str | None = None
    warranty_expiry: datetime | None = None
    reason: str | None = None


# ──────────────────────────────────────────────
#  Ticket Schemas
# ──────────────────────────────────────────────

class TicketCreate(BaseModel):
    customer_id: int
    order_id: int | None = None
    subject: str
    description: str
    type: TicketType = TicketType.INQUIRY
    priority: TicketPriority = TicketPriority.MEDIUM
    channel: str = "chat"


class TicketUpdate(BaseModel):
    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    resolution: str | None = None
    assigned_agent: str | None = None
    satisfaction_rating: int | None = None


class TicketResponse(BaseModel):
    id: int
    customer_id: int
    customer_name: str | None = None
    order_id: int | None = None
    subject: str | None = None
    description: str | None = None
    type: str | None = None
    status: str | None = None
    priority: str | None = None
    channel: str | None = None
    assigned_agent: str | None = None
    resolution: str | None = None
    satisfaction_rating: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# ──────────────────────────────────────────────
#  Dashboard Schemas (Agent Co-Pilot)
# ──────────────────────────────────────────────

class EscalationAlert(BaseModel):
    """Sent to the Agent Dashboard when a customer is escalated."""
    session_id: str
    customer_id: int | None = None
    customer_name: str | None = None
    sentiment: str
    urgency: str
    last_message: str
    suggested_response: str | None = None
    timestamp: datetime | None = None


class DashboardStats(BaseModel):
    """Aggregated stats for the analytics dashboard."""
    total_tickets: int = 0
    open_tickets: int = 0
    escalated_tickets: int = 0
    avg_resolution_time_hours: float | None = None
    avg_satisfaction: float | None = None
    tickets_by_priority: dict = {}
    tickets_by_type: dict = {}
    tickets_by_status: dict = {}


# ──────────────────────────────────────────────
#  Semantic Router Contracts (Phase B)
# ──────────────────────────────────────────────

class RouterFailureType(str, Enum):
    SUCCESS = "success"
    TRANSPORT_ERROR = "transport_error"
    AUTHENTICATION_ERROR = "authentication_error"
    RATE_LIMIT_ERROR = "rate_limit_error"
    TIMEOUT_ERROR = "timeout_error"
    PARSER_ERROR = "parser_error"
    SCHEMA_VALIDATION_ERROR = "schema_validation_error"
    INTERNAL_ERROR = "internal_error"

class RouterDiagnostics(BaseModel):
    failure_type: RouterFailureType
    error_message: str | None = None
    latency_ms: int = 0
    raw_response: str | None = None

class SemanticRouteResult(BaseModel):
    domain: str
    intent: str
    sentiment: str
    urgency: str
    confidence: float
    is_continuation: bool = False
    entities: dict = {}
    diagnostics: RouterDiagnostics

class ExtractionSource(str, Enum):
    USER_EXPLICIT = "user_explicit"
    MODEL_INFERENCE = "model_inference"

class ExtractedEntity(BaseModel):
    value: Any | None
    source: ExtractionSource

class ToolResultEnvelope(BaseModel):
    tool_name: str
    entity_reference: str | int | None = None
    result: dict | str | None = None



# ──────────────────────────────────────────────
#  Workflow State Contract (Phase B)
# ──────────────────────────────────────────────

class WorkflowStatus(str, Enum):
    IDLE = "idle"
    IN_PROGRESS = "in_progress"
    AWAITING_INPUT = "awaiting_input"
    SUSPENDED = "suspended"
    RESUMED = "resumed"
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"

from typing import TypedDict

class TargetAgentState(TypedDict):
    # Identity
    session_id: str
    customer_id: int | None
    
    # Semantic Context
    current_domain: str | None
    current_intent: str | None
    sentiment: str | None
    urgency: str | None
    
    # Workflow Lifecycle
    active_workflow: str | None
    workflow_status: WorkflowStatus | None
    pending_action: dict | None
    
    # Structured Data
    collected_entities: dict
    escalation_status: str | None
    turn_metadata: dict
    
    # Results
    last_meaningful_result: str | None
    router_diagnostics: dict | None


# ──────────────────────────────────────────────
#  Multi-Domain Workflow State Models (Phase F.2)
# ──────────────────────────────────────────────

class OrchestrationDomain(str, Enum):
    PRODUCT = "product"
    ORDER = "order"
    PAYMENT = "payment"
    GENERAL = "general"
    ESCALATION = "escalation"

class TicketContext(BaseModel):
    """
    Transient, strictly typed payload connecting domain workflows to the central
    ticket lifecycle service (Phase F.2.5).

    This context does NOT persist ticket state.
    TicketState ownership remains with the respective domain workflow
    (e.g., ProductState.active_ticket_id).
    """
    customer_id: int = Field(..., gt=0)
    domain: OrchestrationDomain
    intent: str = Field(..., min_length=1, max_length=255)
    message: str = Field(..., min_length=1, max_length=10000)
    urgency: Urgency = Urgency.MEDIUM
    sentiment: Sentiment = Sentiment.NEUTRAL
    
    # Domain Facts (Transient context for lifecycle matching, NOT state mutation)
    order_id: int | None = Field(default=None, gt=0)
    product_name: str | None = Field(default=None, max_length=255)
    
    # Transient reference to the active ticket, provided for deduplication logic,
    # NOT representing ownership.
    active_ticket_id: int | None = Field(default=None, gt=0)

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
    ESCALATED = "escalated"

class ProductState(BaseModel):
    domain_status: DomainWorkflowStatus = DomainWorkflowStatus.IN_PROGRESS
    active_ticket_id: int | None = None
    product_id: int | None = None
    product_name: str | None = Field(None, max_length=255)
    manufacturer: str | None = Field(None, max_length=255)
    last_tool: str | None = Field(None, max_length=255)
    last_tool_result: ToolResultEnvelope | None = None

class OrderState(BaseModel):
    domain_status: DomainWorkflowStatus = DomainWorkflowStatus.IN_PROGRESS
    active_ticket_id: int | None = None
    order_id: str | None = Field(None, max_length=255)
    tracking_number: str | None = Field(None, max_length=255)
    last_tool: str | None = Field(None, max_length=255)
    last_tool_result: ToolResultEnvelope | None = None

class PaymentState(BaseModel):
    domain_status: DomainWorkflowStatus = DomainWorkflowStatus.IN_PROGRESS
    active_ticket_id: int | None = None
    transaction_id: str | None = Field(None, max_length=255)
    payment_method: str | None = Field(None, max_length=255)
    last_tool: str | None = Field(None, max_length=255)
    last_tool_result: ToolResultEnvelope | None = None


class WorkflowState(BaseModel):
    """
    Explicit, strongly typed multi-turn workflow orchestration state.
    Serves as the memory of the conversation bounded to essential fields.
    """
    # ─── F.2 Global Identity & Versioning ───
    schema_version: int = 1
    state_revision: int = 1
    session_id: str = Field(..., max_length=255)
    customer_id: int | None = None
    
    # ─── F.2 Global Orchestration ───
    global_status: GlobalWorkflowStatus = GlobalWorkflowStatus.IDLE
    active_domain: str | None = None
    suspended_domains: list[OrchestrationDomain] = Field(default_factory=list, max_length=3)
    
    # ─── F.2 Domain States ───
    product_state: ProductState | None = None
    order_state: OrderState | None = None
    payment_state: PaymentState | None = None

    # ─── Legacy Semantic Context (Retained for Compatibility) ───
    semantic_intent: str | None = None
    skill_name: str | None = None
    skill_version: str | None = None
    
    # ─── Legacy Extracted Entities (Retained for Compatibility) ───
    product_id: int | None = None
    product_name: str | None = Field(None, max_length=255)
    order_id: int | None = None
    manufacturer: str | None = Field(None, max_length=255)
    
    # ─── Legacy Ticketing (Retained for Compatibility) ───
    active_ticket_id: int | None = None
    
    # ─── Legacy Execution Tracking (Retained for Compatibility) ───
    last_tool: str | None = Field(None, max_length=255)
    last_tool_result: ToolResultEnvelope | None = None
    
    # ─── Shared Lifecycle ───
    workflow_status: WorkflowStatus = WorkflowStatus.IDLE  # Legacy
    pending_input: str | None = Field(None, max_length=1024)
    turn_count: int = 0
    updated_at: datetime | None = None

    @classmethod
    def _map_legacy_workflow_status(cls, status: str | WorkflowStatus) -> DomainWorkflowStatus:
        if isinstance(status, str):
            try:
                status = WorkflowStatus(status)
            except ValueError:
                raise ValueError(f"Unknown workflow status: {status}")
                
        mapping = {
            WorkflowStatus.COMPLETED: DomainWorkflowStatus.COMPLETED,
            WorkflowStatus.SUSPENDED: DomainWorkflowStatus.SUSPENDED,
            WorkflowStatus.ESCALATED: DomainWorkflowStatus.ESCALATED,
            WorkflowStatus.IDLE: DomainWorkflowStatus.IN_PROGRESS,
            WorkflowStatus.IN_PROGRESS: DomainWorkflowStatus.IN_PROGRESS,
            WorkflowStatus.AWAITING_INPUT: DomainWorkflowStatus.AWAITING_INPUT,
            WorkflowStatus.RESUMED: DomainWorkflowStatus.IN_PROGRESS,
            WorkflowStatus.FAILED: DomainWorkflowStatus.FAILED
        }
        if status not in mapping:
            raise ValueError(f"Unknown workflow status enum: {status}")
        return mapping[status]

    @classmethod
    def _map_legacy_global_status(cls, status: str | WorkflowStatus, has_active_domain: bool) -> GlobalWorkflowStatus:
        if isinstance(status, str):
            try:
                status = WorkflowStatus(status)
            except ValueError:
                raise ValueError(f"Unknown workflow status: {status}")

        if status == WorkflowStatus.ESCALATED:
            return GlobalWorkflowStatus.ESCALATED
        elif status == WorkflowStatus.IDLE and not has_active_domain:
            return GlobalWorkflowStatus.IDLE
        else:
            return GlobalWorkflowStatus.IN_PROGRESS

    @classmethod
    def from_legacy(cls, legacy_dict: dict) -> 'WorkflowState':
        """Explicitly documented legacy constructor (F.2.3)."""
        # F.2.6 Persistence Boundary Adapter:
        # If the payload is already an F.2 dumped JSON containing domain objects,
        # use Pydantic's model_validate to deserialize it natively instead of wiping it.
        if legacy_dict.get("product_state") is not None or \
           legacy_dict.get("order_state") is not None or \
           legacy_dict.get("payment_state") is not None:
            return cls.model_validate(legacy_dict)

        session_id = legacy_dict.get("session_id")
        if not session_id:
            raise ValueError("session_id is required")
            
        legacy_version = legacy_dict.get("schema_version", 1)
        if legacy_version != 1:
            raise ValueError(f"Unsupported legacy schema_version: {legacy_version}")

        has_prod = bool(legacy_dict.get("product_id") or legacy_dict.get("product_name") or legacy_dict.get("manufacturer"))
        has_order = bool(legacy_dict.get("order_id"))
        
        if has_prod and has_order:
            raise ValueError("Contradictory cross-domain facts: both product and order facts present in legacy state.")

        # 1. Determine domain
        domain_enum = None
        active = legacy_dict.get("active_domain")
        if active:
            try:
                domain_enum = OrchestrationDomain(active)
            except ValueError:
                raise ValueError(f"Invalid active_domain: {active}")
        else:
            intent = legacy_dict.get("semantic_intent")
            intent_map = {
                "product_inquiry": OrchestrationDomain.PRODUCT,
                "tech_support": OrchestrationDomain.PRODUCT,
                "warranty_check": OrchestrationDomain.PRODUCT,
                "order_status": OrchestrationDomain.ORDER,
                "cancel_order": OrchestrationDomain.ORDER,
                "return_order": OrchestrationDomain.ORDER,
                "billing_inquiry": OrchestrationDomain.PAYMENT,
                "refund_request": OrchestrationDomain.PAYMENT,
                "payment_issue": OrchestrationDomain.PAYMENT,
                "escalation": OrchestrationDomain.ESCALATION
            }
            if intent in intent_map:
                domain_enum = intent_map[intent]
            else:
                skill = legacy_dict.get("skill_name")
                skill_map = {
                    "product_lookup": OrchestrationDomain.PRODUCT,
                    "warranty_lookup": OrchestrationDomain.PRODUCT,
                    "troubleshoot_device": OrchestrationDomain.PRODUCT,
                    "order_lookup": OrchestrationDomain.ORDER,
                    "cancel_order_action": OrchestrationDomain.ORDER,
                    "process_refund": OrchestrationDomain.PAYMENT
                }
                if skill in skill_map:
                    domain_enum = skill_map[skill]
                else:
                    if has_prod:
                        domain_enum = OrchestrationDomain.PRODUCT
                    elif has_order:
                        domain_enum = OrchestrationDomain.ORDER

        if domain_enum == OrchestrationDomain.PRODUCT and has_order:
            raise ValueError("Contradictory cross-domain facts: PRODUCT domain with order facts.")
        if domain_enum == OrchestrationDomain.ORDER and has_prod:
            raise ValueError("Contradictory cross-domain facts: ORDER domain with product facts.")
        if domain_enum == OrchestrationDomain.PAYMENT and (has_prod or has_order):
            raise ValueError("Contradictory cross-domain facts: PAYMENT domain with product/order facts.")
        if domain_enum in (OrchestrationDomain.GENERAL, OrchestrationDomain.ESCALATION):
            if has_prod or has_order:
                raise ValueError(f"Contradictory cross-domain facts: {domain_enum.value} domain with product/order facts.")
            if legacy_dict.get("active_ticket_id") is not None:
                raise ValueError(f"Contradictory cross-domain facts: {domain_enum.value} domain with active_ticket_id.")

        # 2. Map domain states
        active_ticket_id = legacy_dict.get("active_ticket_id")
        domain_status = cls._map_legacy_workflow_status(legacy_dict.get("workflow_status", WorkflowStatus.IDLE))

        product_state = None
        order_state = None
        payment_state = None

        last_tool = legacy_dict.get("last_tool")
        last_tool_result = legacy_dict.get("last_tool_result")

        if domain_enum == OrchestrationDomain.PRODUCT:
            product_state = ProductState(
                domain_status=domain_status,
                active_ticket_id=active_ticket_id,
                product_id=legacy_dict.get("product_id"),
                product_name=legacy_dict.get("product_name"),
                manufacturer=legacy_dict.get("manufacturer"),
                last_tool=last_tool,
                last_tool_result=last_tool_result
            )
        elif domain_enum == OrchestrationDomain.ORDER:
            order_state = OrderState(
                domain_status=domain_status,
                active_ticket_id=active_ticket_id,
                order_id=str(legacy_dict.get("order_id")) if legacy_dict.get("order_id") is not None else None,
                last_tool=last_tool,
                last_tool_result=last_tool_result
            )
        elif domain_enum == OrchestrationDomain.PAYMENT:
            payment_state = PaymentState(
                domain_status=domain_status,
                active_ticket_id=active_ticket_id,
                last_tool=last_tool,
                last_tool_result=last_tool_result
            )
        elif active_ticket_id is not None:
            # Cross-domain ticket leakage or ticket without domain context
            raise ValueError("active_ticket_id present but domain is ambiguous or general.")

        suspended_domains = []
        final_active_domain = domain_enum.value if domain_enum else None
        
        # F.2.7.3C.1 explicit migration for legacy suspended workflows
        if domain_status == DomainWorkflowStatus.SUSPENDED and domain_enum in (OrchestrationDomain.PRODUCT, OrchestrationDomain.ORDER, OrchestrationDomain.PAYMENT):
            suspended_domains.append(domain_enum)
            final_active_domain = OrchestrationDomain.GENERAL.value

        # Initialize base state, translating domains and erasing unsupported/migrated legacy root fields
        state = cls(
            session_id=session_id,
            schema_version=1,
            state_revision=legacy_dict.get("state_revision", 1),
            customer_id=legacy_dict.get("customer_id"),
            active_domain=final_active_domain,
            suspended_domains=suspended_domains,
            product_state=product_state,
            order_state=order_state,
            payment_state=payment_state,
            semantic_intent=None,
            skill_name=None,
            skill_version=None,
            product_id=None,
            product_name=None,
            order_id=None,
            manufacturer=None,
            active_ticket_id=None,
            last_tool=None,
            last_tool_result=None,
            global_status=cls._map_legacy_global_status(legacy_dict.get("workflow_status", WorkflowStatus.IDLE), bool(domain_enum)),
            workflow_status=legacy_dict.get("workflow_status", WorkflowStatus.IDLE),
            pending_input=None,
            turn_count=legacy_dict.get("turn_count", 0),
            updated_at=legacy_dict.get("updated_at")
        )

        return state



    @model_validator(mode='after')
    def _validate_f2_schema_invariants(self) -> 'WorkflowState':
        """F.2.2 structural schema validation."""
        # 1. Version bounds
        if self.schema_version != 1:
            raise ValueError(f"Unsupported schema_version {self.schema_version}. Only version 1 is currently supported.")
        if self.state_revision < 0:
            raise ValueError("Negative state_revision")

        # Legacy bypass removed: all constructions run F.2 validation.
        # "WorkflowState(session_id='x') cannot silently bypass F.2 validation"
            
        # 3. Validate active domain bounds and enums
        if self.active_domain is not None:
            try:
                OrchestrationDomain(self.active_domain)
            except ValueError:
                raise ValueError(f"Invalid enum value for active_domain: {self.active_domain}")
                
        if self.active_domain == OrchestrationDomain.PRODUCT and not self.product_state:
            raise ValueError("PRODUCT domain is active but product_state is None.")
        if self.active_domain == OrchestrationDomain.ORDER and not self.order_state:
            raise ValueError("ORDER domain is active but order_state is None.")
        if self.active_domain == OrchestrationDomain.PAYMENT and not self.payment_state:
            raise ValueError("PAYMENT domain is active but payment_state is None.")
            
        # Validate suspended domain bounds
        if len(self.suspended_domains) > 3:
            raise ValueError("suspended_domains cannot exceed 3 items.")
            
        for d in self.suspended_domains:
            if d == OrchestrationDomain.PRODUCT:
                if not self.product_state:
                    raise ValueError("PRODUCT is suspended but state is missing.")
                if self.product_state.domain_status != DomainWorkflowStatus.SUSPENDED:
                    raise ValueError("PRODUCT is suspended but domain_status is not SUSPENDED.")
            if d == OrchestrationDomain.ORDER:
                if not self.order_state:
                    raise ValueError("ORDER is suspended but state is missing.")
                if self.order_state.domain_status != DomainWorkflowStatus.SUSPENDED:
                    raise ValueError("ORDER is suspended but domain_status is not SUSPENDED.")
            if d == OrchestrationDomain.PAYMENT:
                if not self.payment_state:
                    raise ValueError("PAYMENT is suspended but state is missing.")
                if self.payment_state.domain_status != DomainWorkflowStatus.SUSPENDED:
                    raise ValueError("PAYMENT is suspended but domain_status is not SUSPENDED.")
            
            if self.active_domain == d.value:
                raise ValueError(f"Domain {d.value} cannot be both active and suspended.")
                
        # Validate duplicates in suspended domains
        if len(self.suspended_domains) != len(set(self.suspended_domains)):
            raise ValueError("Duplicate suspended domains detected.")
            
        # Ensure active typed domains are not SUSPENDED
        if self.active_domain == OrchestrationDomain.PRODUCT.value and self.product_state and self.product_state.domain_status == DomainWorkflowStatus.SUSPENDED:
            raise ValueError("PRODUCT domain cannot be active while its status is SUSPENDED.")
        if self.active_domain == OrchestrationDomain.ORDER.value and self.order_state and self.order_state.domain_status == DomainWorkflowStatus.SUSPENDED:
            raise ValueError("ORDER domain cannot be active while its status is SUSPENDED.")
        if self.active_domain == OrchestrationDomain.PAYMENT.value and self.payment_state and self.payment_state.domain_status == DomainWorkflowStatus.SUSPENDED:
            raise ValueError("PAYMENT domain cannot be active while its status is SUSPENDED.")
            
        # 5. F.2.6 Persistence Contradiction Hardening
        if self.active_domain in (OrchestrationDomain.GENERAL.value, OrchestrationDomain.ESCALATION.value):
            if self.product_state and OrchestrationDomain.PRODUCT not in self.suspended_domains:
                raise ValueError(f"Domain {self.active_domain} cannot own active typed domain state (PRODUCT)")
            if self.order_state and OrchestrationDomain.ORDER not in self.suspended_domains:
                raise ValueError(f"Domain {self.active_domain} cannot own active typed domain state (ORDER)")
            if self.payment_state and OrchestrationDomain.PAYMENT not in self.suspended_domains:
                raise ValueError(f"Domain {self.active_domain} cannot own active typed domain state (PAYMENT)")

        has_root_prod = bool(self.product_id or self.product_name or self.manufacturer)
        has_root_order = bool(self.order_id)

        if self.active_domain == OrchestrationDomain.PRODUCT.value and has_root_order:
            raise ValueError("active_domain=product contradicts order ownership facts")
        if self.active_domain == OrchestrationDomain.ORDER.value and has_root_prod:
            raise ValueError("active_domain=order contradicts product ownership facts")
        if self.active_domain == OrchestrationDomain.PAYMENT.value and (has_root_prod or has_root_order):
            raise ValueError("active_domain=payment contradicts product/order ownership facts")

        if self.active_ticket_id is not None:
            if self.active_domain == OrchestrationDomain.PRODUCT.value and self.product_state:
                if self.product_state.active_ticket_id is not None and self.active_ticket_id != self.product_state.active_ticket_id:
                    raise ValueError("Conflicting legacy root ticket identity with ProductState")
            if self.active_domain == OrchestrationDomain.ORDER.value and self.order_state:
                if self.order_state.active_ticket_id is not None and self.active_ticket_id != self.order_state.active_ticket_id:
                    raise ValueError("Conflicting legacy root ticket identity with OrderState")
            if self.active_domain == OrchestrationDomain.PAYMENT.value and self.payment_state:
                if self.payment_state.active_ticket_id is not None and self.active_ticket_id != self.payment_state.active_ticket_id:
                    raise ValueError("Conflicting legacy root ticket identity with PaymentState")
            
        return self


# ──────────────────────────────────────────────
#  Skill Runtime Contracts (Phase C)
# ──────────────────────────────────────────────

class RiskLevel(str, Enum):
    READ_ONLY = "read_only"
    LOW_RISK_MUTATION = "low_risk_mutation"
    HIGH_RISK_MUTATION = "high_risk_mutation"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"


class SkillExecutionStatus(str, Enum):
    SUCCESS = "success"
    MISSING_REQUIRED_INPUT = "missing_required_input"
    INVALID_INPUT = "invalid_input"
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    POLICY_DENIED = "policy_denied"
    CONFIRMATION_REQUIRED = "confirmation_required"
    TOOL_FAILURE = "tool_failure"
    WORKFLOW_FAILURE = "workflow_failure"


class SkillExecutionResult(BaseModel):
    skill_name: str
    skill_version: str
    status: SkillExecutionStatus
    structured_output: dict = {}
    failure_code: str | None = None
    message: str | None = None
