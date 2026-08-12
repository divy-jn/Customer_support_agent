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
