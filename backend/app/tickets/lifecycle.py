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
    def _map_to_ticket_type(cls, intent: str) -> TicketType:
        if intent in ("technical_support", "warranty_claim", "product_warranty"):
            return TicketType.TECHNICAL_ISSUE
        if intent == "billing_issue":
            return TicketType.BILLING
        if intent == "refund_request":
            return TicketType.REFUND
        if intent == "cancel_order":
            return TicketType.CANCELLATION
        return TicketType.INQUIRY

    @classmethod
    def _map_to_priority(cls, urgency: str, sentiment: str) -> TicketPriority:
        if urgency == "critical" or sentiment == "negative":
            return TicketPriority.HIGH
        if urgency == "high":
            return TicketPriority.HIGH
        if urgency == "low":
            return TicketPriority.LOW
        return TicketPriority.MEDIUM

    @classmethod
    @_supabase_retry
    def _find_matching_ticket(cls, context: IssueContext, active_ticket_id: int | None) -> dict | None:
        """
        Deterministic matching policy to find an existing open ticket for this issue.
        """
        # Always enforce customer isolation
        query = supabase.table("tickets").select("*").eq("customer_id", context.customer_id).neq("status", "closed")
        
        resp = query.execute()
        open_tickets = resp.data or []
        
        if not open_tickets:
            return None
            
        # 1. Same active ticket ID and matching core identity (order_id)
        if active_ticket_id:
            for t in open_tickets:
                if t["id"] == active_ticket_id:
                    # If order_id changed significantly, it's a new issue!
                    if context.order_id and t.get("order_id") and context.order_id != t["order_id"]:
                        continue
                    return t
                    
        # 2. Matching canonical issue category and order_id/product
        target_type = cls._map_to_ticket_type(context.intent)
        
        for t in open_tickets:
            # If the user is discussing a specific order, match it
            if context.order_id and t.get("order_id") == context.order_id:
                return t
            
            # If the types match and we have product info in subject/desc
            if t.get("type") == target_type.value:
                if context.product_name and context.product_name.lower() in t.get("subject", "").lower():
                    return t
                
                # If neither order nor product is explicitly tracked yet, but it's the exact same type and still open
                if not context.order_id and not context.product_name:
                    return t
                    
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
                
                # Check if we need to update order_id (using direct DB since update_ticket doesn't support order_id yet, wait, update_ticket only supports status, priority, resolution, etc)
                # If the context found a new order ID that wasn't in the ticket, we should ideally save it. 
                # For now, append to description or rely on the fact that same ticket_id is preserved.
                
                # We can call the underlying update_ticket tool for standard updates if priority needs a bump
                new_priority = cls._map_to_priority(context.urgency, context.sentiment).value
                if existing.get("priority") != new_priority and existing.get("priority") not in ("high", "critical"):
                     update_ticket(
                         ticket_id=ticket_id, 
                         customer_id=context.customer_id, 
                         priority=new_priority
                     )
                     
                return TicketLifecycleResult(
                    action="UPDATED",
                    ticket_id=ticket_id,
                    customer_id=context.customer_id,
                    order_id=existing.get("order_id") or context.order_id,
                    issue_type=existing.get("type"),
                    status=existing.get("status"),
                    matched_existing=True,
                    reason="Matched existing open ticket.",
                    timestamp=datetime.now(timezone.utc)
                )
            else:
                # CREATE new
                subject = f"Issue: {context.intent.replace('_', ' ').title()}"
                if context.product_name:
                    subject += f" - {context.product_name}"
                    
                ticket_type = cls._map_to_ticket_type(context.intent).value
                priority = cls._map_to_priority(context.urgency, context.sentiment).value
                
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
            return TicketLifecycleResult(
                action="FAILED",
                ticket_id=None,
                customer_id=context.customer_id,
                order_id=context.order_id,
                issue_type=None,
                status=None,
                matched_existing=False,
                reason=f"Lifecycle error: {str(e)}",
                timestamp=datetime.now(timezone.utc)
            )
