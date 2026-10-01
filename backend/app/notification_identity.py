"""
Slice 3.1: Notification Event Identity Contract.

This module centralizes the durable identity contract for email side-effects.
It explicitly distinguishes:
1. Client Request Identity (caller intent)
2. Source Event Identity (business event)
3. Logical Notification Identity (for deduplication)
4. Outbox Row Identity (database PK, documented only)
"""

import hashlib
import json
from enum import Enum
from pydantic import BaseModel, Field

class NotificationType(str, Enum):
    TICKET_CREATED = "TICKET_CREATED"
    TICKET_RESOLVED = "TICKET_RESOLVED"
    ORDER_UPDATED = "ORDER_UPDATED"
    ESCALATION_TEAM = "ESCALATION_TEAM"
    ESCALATION_CUSTOMER = "ESCALATION_CUSTOMER"
    CUSTOM_EMAIL = "CUSTOM_EMAIL"

class ClientRequestIdentity(BaseModel):
    """
    Identifies the caller's request intent. 
    Preserves the existing idempotency scope constraint (customer_id, client_request_id).
    """
    customer_id: int
    client_request_id: str

class SourceEventIdentity(BaseModel):
    """
    The durable source event identifier per notification path.
    """
    namespace: str = "v1"
    
    # Unambiguous string representing the durable business state mutation or intent.
    source_event_id: str

    @classmethod
    def from_ticket_creation(cls, customer_id: int, client_request_id: str) -> "SourceEventIdentity":
        return cls(source_event_id=f"customer:{customer_id}|req:{client_request_id}")

    @classmethod
    def from_ticket_resolution(cls, customer_id: int, client_request_id: str) -> "SourceEventIdentity":
        return cls(source_event_id=f"customer:{customer_id}|req:{client_request_id}")

    @classmethod
    def from_order_update(cls, customer_id: int, client_request_id: str) -> "SourceEventIdentity":
        return cls(source_event_id=f"customer:{customer_id}|req:{client_request_id}")

    @classmethod
    def from_custom_email(cls, customer_id: int, client_request_id: str) -> "SourceEventIdentity":
        """
        Custom email relies on an idempotency record generated via client_request_id,
        since there is no core business entity mutation (like a ticket).
        """
        return cls(source_event_id=f"customer:{customer_id}|req:{client_request_id}")

    @classmethod
    def from_escalation(cls, escalation_event_id: str) -> "SourceEventIdentity":
        """
        Future contract: Relies on a durable escalation event record (not yet implemented).
        The escalation_event_id represents a uniquely committed record in an upcoming
        escalation_events table, rather than relying on a boolean session state.
        """
        return cls(source_event_id=f"escalation:{escalation_event_id}")

class RecipientIdentity(BaseModel):
    """
    Stable logical principal receiving the email.
    Distinct from the delivery address (which is stored in the payload).
    """
    principal: str

    @classmethod
    def customer(cls, customer_id: int) -> "RecipientIdentity":
        return cls(principal=f"customer:{customer_id}")

    @classmethod
    def support_team(cls, alias: str = "default") -> "RecipientIdentity":
        return cls(principal=f"support-team:{alias}")

class LogicalNotificationIdentity(BaseModel):
    """
    One centralized deterministic representation for the notification deduplication identity.
    """
    source_event: SourceEventIdentity
    notification_type: NotificationType
    recipient: RecipientIdentity

    def canonicalize(self) -> str:
        """
        Deterministic, unambiguous serialization of the identity tuple.
        Returns a JSON array of strings to prevent ambiguous concatenation
        (e.g., "A" + "BC" vs "AB" + "C").
        """
        tuple_list = [
            self.source_event.namespace,
            self.source_event.source_event_id,
            self.notification_type.value.strip().upper(),
            self.recipient.principal.strip().lower()
        ]
        return json.dumps(tuple_list, separators=(',', ':'))

    def get_hash(self) -> str:
        """
        Produces a deterministic SHA-256 identity based on the canonical representation.
        """
        return hashlib.sha256(self.canonicalize().encode('utf-8')).hexdigest()

# ──────────────────────────────────────────────
#  Outbox Row Identity
# ──────────────────────────────────────────────
#
# event_id = UUID primary key
#
# Note: The `event_id` is merely the physical storage identity for the row
# in the outbox table. It does NOT represent the logical notification identity.
# The logical deduplication is enforced by a unique constraint on the
# `logical_identity_hash` produced by `LogicalNotificationIdentity.get_hash()`.
