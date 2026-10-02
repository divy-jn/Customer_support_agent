import uuid
import json
import hashlib
from typing import Optional
from pydantic import BaseModel
from app.tools import supabase
from app.notification_identity import SourceEventIdentity, LogicalNotificationIdentity, NotificationType, RecipientIdentity

class EscalationRequest(BaseModel):
    customer_id: int
    session_id: str
    client_request_id: uuid.UUID
    ticket_id: Optional[int] = None
    sentiment: Optional[str] = None
    urgency: Optional[str] = None
    last_message: Optional[str] = None
    customer_notification_required: bool = False

class EscalationResult(BaseModel):
    status: str
    message: str
    session_id: str
    escalation_event_id: Optional[uuid.UUID] = None
    error: Optional[str] = None
    
    @property
    def is_success(self) -> bool:
        return self.status == "success"

class ReleaseRequest(BaseModel):
    customer_id: int
    session_id: str
    client_request_id: uuid.UUID

class ReleaseResult(BaseModel):
    status: str
    message: str
    session_id: str
    error: Optional[str] = None
    
    @property
    def is_success(self) -> bool:
        return self.status == "success"

class IdempotencyConflict(Exception):
    def __init__(self, message: str, previous_result: dict):
        super().__init__(message)
        self.previous_result = previous_result

class EscalationService:
    @staticmethod
    def escalate_session(request: EscalationRequest) -> EscalationResult:
        """
        Executes a transactional escalation lifecycle transition using Postgres RPC.
        Validates idempotency, locks the state row, applies business rules for NONE/RELEASED vs ACTIVE,
        and manages the outbox.
        """
        payload = {
            "ticket_id": request.ticket_id,
            "sentiment": request.sentiment,
            "urgency": request.urgency,
            "last_message": request.last_message,
            "customer_notification_required": request.customer_notification_required
        }
        
        # Ensure the fingerprint represents ALL semantically relevant operation inputs
        # including the target session and any fields that change the result
        canonical_fingerprint = payload.copy()
        canonical_fingerprint["session_id"] = request.session_id
        
        # Calculate payload hash
        payload_hash = hashlib.sha256(json.dumps(canonical_fingerprint, sort_keys=True).encode()).hexdigest()
        
        # Pre-generate IDs to establish logical notification identities in Python
        escalation_event_id = str(uuid.uuid4())
        source_event = SourceEventIdentity.from_escalation(escalation_event_id)
        
        support_identity = LogicalNotificationIdentity(
            source_event=source_event,
            notification_type=NotificationType.ESCALATION_TEAM,
            recipient=RecipientIdentity.support_team()
        )
        
        customer_hash = None
        if request.customer_notification_required:
            customer_identity = LogicalNotificationIdentity(
                source_event=source_event,
                notification_type=NotificationType.ESCALATION_CUSTOMER,
                recipient=RecipientIdentity.customer(request.customer_id)
            )
            customer_hash = customer_identity.get_hash()
        
        # Call the RPC
        res = supabase.rpc(
            "execute_escalation_transition",
            {
                "p_customer_id": request.customer_id,
                "p_client_request_id": str(request.client_request_id),
                "p_session_id": request.session_id,
                "p_payload_hash": payload_hash,
                "p_payload": payload,
                "p_escalation_event_id": escalation_event_id
            }
        ).execute()
        
        data = res.data
        if "error" in data:
            if data["error"] == "IdempotencyConflict":
                raise IdempotencyConflict(data.get("message", "Conflict"), data.get("previous_result", {}))
            return EscalationResult(
                status="failed",
                message=data["error"],
                session_id=request.session_id,
                error=data["error"]
            )
            
        return EscalationResult(
            status=data["status"],
            message=data.get("message", ""),
            session_id=data.get("session_id", request.session_id),
            escalation_event_id=uuid.UUID(data["escalation_event_id"]) if data.get("escalation_event_id") else None
        )

    @staticmethod
    def release_session(request: ReleaseRequest) -> ReleaseResult:
        """
        Executes a transactional release lifecycle transition using Postgres RPC.
        Validates idempotency, locks the state row, applies business rules (ACTIVE -> RELEASED).
        """
        canonical_fingerprint = {"session_id": request.session_id}
        payload_hash = hashlib.sha256(json.dumps(canonical_fingerprint, sort_keys=True).encode()).hexdigest()
        
        res = supabase.rpc(
            "execute_release_transition",
            {
                "p_customer_id": request.customer_id,
                "p_client_request_id": str(request.client_request_id),
                "p_session_id": request.session_id,
                "p_payload_hash": payload_hash,
                "p_payload": {}
            }
        ).execute()
        
        data = res.data
        if "error" in data:
            if data["error"] == "IdempotencyConflict":
                raise IdempotencyConflict(data.get("message", "Conflict"), data.get("previous_result", {}))
            return ReleaseResult(
                status="failed",
                message=data["error"],
                session_id=request.session_id,
                error=data["error"]
            )
            
        return ReleaseResult(
            status=data["status"],
            message=data.get("message", ""),
            session_id=data.get("session_id", request.session_id)
        )
