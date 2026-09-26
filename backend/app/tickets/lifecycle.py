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
    def _is_compatible_identity(cls, identity: IssueIdentity, ticket: dict) -> bool:
        """
        Validates if an open ticket is compatible with the new issue identity.
        """
        # Type must match EXACTLY.
        if ticket.get("type") != identity.ticket_type:
            return False
            
        # If the context is explicitly for a specific order, it must match.
        if identity.order_id is not None:
            ticket_order = ticket.get("order_id")
            if ticket_order is not None and ticket_order != identity.order_id:
                return False
                
        # If the context is explicitly for a product, and the ticket is tracking one, it shouldn't conflict.
        # However, product_name is mostly stored in the subject right now, so we do a softer check.
        if identity.product_name and identity.order_id is None:
            # We don't have a rigid product_id column, so we check if the ticket subject mentions a different product.
            subject = ticket.get("subject", "").lower()
            if identity.product_name.lower() not in subject and "-" in subject:
                # Basic heuristic to avoid crossing distinct products if order_id is missing
                # e.g., "Issue: Technical Issue - Phone" vs "Issue: Technical Issue - Laptop"
                pass # This is best-effort. If they are completely different, we might accidentally merge. 
                # To be strict, if product_name is provided, require it in the subject if subject has a hyphen
                parts = subject.split("-")
                if len(parts) > 1:
                    existing_product = parts[-1].strip()
                    if existing_product and existing_product != identity.product_name.lower():
                        return False
                        
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
            # Lookup failure MUST bubble up to FAILED, not fall through to None (which causes CREATE).
            raise RuntimeError("Database lookup failed.")
            
        open_tickets = resp.data or []
        if not open_tickets:
            return None
            
        # We only match tickets created/updated recently (e.g. within 30 days) if needed, 
        # but 'status != closed' is our primary defense.
            
        identity = cls._get_issue_identity(context)
        candidates = []
        
        for t in open_tickets:
            if cls._is_compatible_identity(identity, t):
                candidates.append(t)
                
        if not candidates:
            return None
            
        # 1. Exact active_ticket_id + compatible issue identity
        if active_ticket_id:
            for t in candidates:
                if t["id"] == active_ticket_id:
                    return t
                    
        # 2. Exact issue identity + exact order_id
        if identity.order_id:
            exact_order_candidates = [t for t in candidates if t.get("order_id") == identity.order_id]
            if exact_order_candidates:
                # Sort by updated_at desc
                exact_order_candidates.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
                return exact_order_candidates[0]
                
        # 3. Exact issue identity + exact product identity
        if identity.product_name:
            exact_product_candidates = []
            for t in candidates:
                subject = t.get("subject", "").lower()
                if identity.product_name.lower() in subject:
                    exact_product_candidates.append(t)
            if exact_product_candidates:
                exact_product_candidates.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
                return exact_product_candidates[0]
                
        # 4. If we have multiple candidates but no strong anchor, we pick the most recently updated compatible one
        candidates.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return candidates[0]

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
                
                resp_str = update_ticket(
                    ticket_id=ticket_id, 
                    customer_id=context.customer_id, 
                    priority=priority_update,
                    description_append=context.message
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
