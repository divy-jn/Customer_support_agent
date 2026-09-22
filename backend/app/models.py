"""
Pydantic schemas for API request/response validation.
"""

from pydantic import BaseModel
from datetime import datetime
from enum import Enum


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


# ──────────────────────────────────────────────
#  Workflow State Contract (Phase B)
# ──────────────────────────────────────────────

class WorkflowStatus(str, Enum):
    IDLE = "idle"
    IN_PROGRESS = "in_progress"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"

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
