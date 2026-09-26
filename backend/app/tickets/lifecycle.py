import json
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel

from app.tools import supabase, _supabase_retry, create_ticket, update_ticket
from app.models import TicketType, TicketPriority, TicketStatus

class IssueContext(BaseModel):
    customer_id: int
    domain: str
    intent: str
    message: str
    order_id: int | None = None
    product_name: str | None = None
    urgency: str = "medium"
    sentiment: str = "neutral"

class TicketLifecycleResult(BaseModel):
    action: Literal["CREATED", "UPDATED", "IGNORED", "FAILED"]
    ticket_id: int | None
    customer_id: int
    order_id: int | None
    issue_type: str | None
    status: str | None
    matched_existing: bool
    reason: str
    timestamp: datetime

class IssueIdentity(BaseModel):
    domain: str
    intent: str
    ticket_type: str
    order_id: int | None = None
    product_name: str | None = None

class TicketLifecycleService:
    """
    Centralized policy boundary for converting customer issues into tickets.
    Enforces deterministic deduplication and customer isolation.
    """
    
    # Intents that conceptually represent a ticketable issue
    ISSUE_INTENTS = {
        "technical_support",
        "warranty_claim",
        "product_warranty",
        "billing_issue",
        "order_issue",
        "escalation",
        "refund_request",
        "cancel_order",
        "complaint"
    }
    
    @classmethod
    def _map_to_ticket_type(cls, intent: str) -> str:
        if intent in ("technical_support", "warranty_claim", "product_warranty"):
            return TicketType.TECHNICAL_ISSUE.value
        if intent == "billing_issue":
            return TicketType.BILLING.value
        if intent == "refund_request":
            return TicketType.REFUND.value
        if intent == "cancel_order":
            return TicketType.CANCELLATION.value
        return TicketType.INQUIRY.value

    @classmethod
    def _map_to_priority(cls, urgency: str, sentiment: str) -> str:
        if urgency == "critical" or sentiment == "negative":
            return TicketPriority.HIGH.value
        if urgency == "high":
            return TicketPriority.HIGH.value
        if urgency == "low":
            return TicketPriority.LOW.value
        return TicketPriority.MEDIUM.value

    @classmethod
    def _get_issue_identity(cls, context: IssueContext) -> IssueIdentity:
        return IssueIdentity(
            domain=context.domain,
            intent=context.intent,
            ticket_type=cls._map_to_ticket_type(context.intent),
            order_id=context.order_id,
            product_name=context.product_name
        )

    @classmethod
    def _is_compatible_identity(cls, identity: IssueIdentity, ticket: dict, is_active: bool = False) -> bool:
        """
        Validates if an open ticket is compatible with the new issue identity.
        is_active is True if this ticket is the active_ticket_id, allowing stronger continuation overrides.
        """
        # Type must match EXACTLY.
        if ticket.get("type") != identity.ticket_type:
            return False
            
        ticket_order = ticket.get("order_id")
        
        # Blocker 2: Order Identity
        if identity.order_id is not None:
            if ticket_order is not None:
                if ticket_order != identity.order_id:
                    return False
            else:
                # ticket has null order_id, new issue has order_id
                if not is_active:
                    return False # Do not absorb new order into generic ticket unless it's active
        elif ticket_order is not None:
            # New issue lacks order_id, ticket has one. This is fine to continue if active or same product.
            pass
            
        # Blocker 3: Product Identity
        ticket_subject = ticket.get("subject", "").lower()
        ticket_product = None
        if "-" in ticket_subject:
            parts = ticket_subject.split("-")
            if len(parts) > 1:
                ticket_product = parts[-1].strip()
                
        if identity.product_name:
            if ticket_product:
                if ticket_product != identity.product_name.lower():
                    return False
            else:
                # Ticket lacks reliable product identity
                if not is_active:
                    return False
        elif ticket_product and not is_active:
            # Ticket has product, new issue lacks product. 
            pass
            
        return True

    @classmethod
    @_supabase_retry
    def _find_matching_ticket(cls, context: IssueContext, active_ticket_id: int | None) -> dict | None:
        """
        Deterministic candidate matching policy.
        """
        # Always enforce customer isolation
        query = supabase.table("tickets").select("*").eq("customer_id", context.customer_id).neq("status", "closed")
        
        try:
            resp = query.execute()
        except Exception:
            raise RuntimeError("Database lookup failed.")
            
        open_tickets = resp.data or []
        if not open_tickets:
            return None
            
        identity = cls._get_issue_identity(context)
        
        # 1. Active ticket check
        if active_ticket_id:
            active_ticket = next((t for t in open_tickets if t["id"] == active_ticket_id), None)
            if active_ticket and cls._is_compatible_identity(identity, active_ticket, is_active=True):
                return active_ticket
                
        # Non-active candidates
        candidates = [t for t in open_tickets if cls._is_compatible_identity(identity, t, is_active=False)]
        if not candidates:
            return None
            
        # 2. Exact order_id match
        if identity.order_id:
            exact_order = [t for t in candidates if t.get("order_id") == identity.order_id]
            if len(exact_order) == 1:
                return exact_order[0]
            elif len(exact_order) > 1:
                return None # Ambiguous
                
        # 3. Exact product match (no order_id)
        if identity.product_name:
            exact_product = []
            for t in candidates:
                ticket_subject = t.get("subject", "").lower()
                if "-" in ticket_subject:
                    ticket_product = ticket_subject.split("-")[-1].strip()
                    if ticket_product == identity.product_name.lower():
                        exact_product.append(t)
            if len(exact_product) == 1:
                return exact_product[0]
            elif len(exact_product) > 1:
                return None # Ambiguous
                
        # 4. Ambiguity policy -> CREATE
        # If we have reached here, there is no exact order_id match and no exact product match.
        # Even if len(candidates) == 1, a ticket type alone is NOT enough to prove issue identity.
        return None

    @classmethod
    def process_issue(cls, context: IssueContext, active_ticket_id: int | None = None) -> TicketLifecycleResult:
        """
        Main entry point. Evaluates context, determines if a ticket is needed,
        and creates/updates accordingly.
        """
        # 1. Should we create a ticket?
        if context.intent not in cls.ISSUE_INTENTS:
            return TicketLifecycleResult(
                action="IGNORED",
                ticket_id=active_ticket_id,
                customer_id=context.customer_id,
                order_id=context.order_id,
                issue_type=None,
                status=None,
                matched_existing=False,
                reason=f"Intent '{context.intent}' does not require a ticket.",
                timestamp=datetime.now(timezone.utc)
            )
            
        try:
            # 2. Find existing ticket
            existing = cls._find_matching_ticket(context, active_ticket_id)
            
            if existing:
                # UPDATE existing
                ticket_id = existing["id"]
                new_priority = cls._map_to_priority(context.urgency, context.sentiment)
                
                # Actual mutation logic for continuation
                priority_update = new_priority if existing.get("priority") != new_priority and existing.get("priority") not in ("high", "critical") else None
                
                order_update = context.order_id if context.order_id is not None and existing.get("order_id") != context.order_id else None
                
                resp_str = update_ticket(
                    ticket_id=ticket_id, 
                    customer_id=context.customer_id, 
                    priority=priority_update,
                    description_append=context.message,
                    order_id=order_update
                )
                
                resp_json = json.loads(resp_str)
                if "error" in resp_json:
                     raise Exception(resp_json["error"])
                     
                return TicketLifecycleResult(
                    action="UPDATED",
                    ticket_id=ticket_id,
                    customer_id=context.customer_id,
                    order_id=existing.get("order_id"),
                    issue_type=existing.get("type"),
                    status=existing.get("status"),
                    matched_existing=True,
                    reason="Matched and updated existing open ticket.",
                    timestamp=datetime.now(timezone.utc)
                )
            else:
                # CREATE new
                subject = f"Issue: {context.intent.replace('_', ' ').title()}"
                if context.product_name:
                    subject += f" - {context.product_name}"
                    
                ticket_type = cls._map_to_ticket_type(context.intent)
                priority = cls._map_to_priority(context.urgency, context.sentiment)
                
                resp_str = create_ticket(
                    customer_id=context.customer_id,
                    subject=subject,
                    description=context.message,
                    ticket_type=ticket_type,
                    priority=priority,
                    channel="chat",
                    order_id=context.order_id
                )
                
                resp_json = json.loads(resp_str)
                if "error" in resp_json:
                    raise Exception(resp_json["error"])
                    
                new_ticket_id = resp_json["ticket_id"]
                
                return TicketLifecycleResult(
                    action="CREATED",
                    ticket_id=new_ticket_id,
                    customer_id=context.customer_id,
                    order_id=context.order_id,
                    issue_type=ticket_type,
                    status=TicketStatus.OPEN.value,
                    matched_existing=False,
                    reason="No matching open ticket found. Created new.",
                    timestamp=datetime.now(timezone.utc)
                )
                
        except Exception as e:
            # Sanitize failure reason
            return TicketLifecycleResult(
                action="FAILED",
                ticket_id=None,
                customer_id=context.customer_id,
                order_id=context.order_id,
                issue_type=None,
                status=None,
                matched_existing=False,
                reason="Ticket lifecycle operation failed.",
                timestamp=datetime.now(timezone.utc)
            )
