import logging
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel
import psycopg2
from psycopg2.extras import RealDictCursor

from app.notification_identity import (
    NotificationType,
    SourceEventIdentity,
    RecipientIdentity,
    LogicalNotificationIdentity
)

logger = logging.getLogger("outbox.reconciliation")


class ReconciliationStatus(str, Enum):
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    NOT_REQUIRED = "NOT_REQUIRED"
    NOT_RECONSTRUCTIBLE = "NOT_RECONSTRUCTIBLE"


class PlannedNotification(BaseModel):
    source_event_id: str
    notification_type: NotificationType
    recipient_identity: str
    recipient_address: str
    logical_identity_hash: str
    status: ReconciliationStatus


class ReconciliationPlanner:
    """
    Read-only planner that reconstructs expected notification intents from authoritative
    durable source records and evaluates whether they exist in the outbox.

    It does NOT create or mutate any records.
    """

    def __init__(self, dsn: str):
        self.dsn = dsn

    def plan_for_escalation_event(self, escalation_event_id: str) -> List[PlannedNotification]:
        """
        Reconstruct notification intents for an escalation event.
        - E1 (Team): Always required.
        - E2 (Customer): Required only if customer_notification_required = true.
        """
        planned: List[PlannedNotification] = []

        with psycopg2.connect(self.dsn) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Fetch authoritative source
                cur.execute("""
                    SELECT e.customer_id, e.customer_notification_required, c.email
                    FROM escalation_events e
                    JOIN customers c ON e.customer_id = c.id
                    WHERE e.escalation_event_id = %s
                """, (escalation_event_id,))
                
                row = cur.fetchone()
                if not row:
                    return planned

                customer_id = row["customer_id"]
                customer_email = row["email"]
                customer_notif_req = row["customer_notification_required"]

                # 2. Reconstruct E1 (Team)
                e1_source = SourceEventIdentity.from_escalation(escalation_event_id)
                e1_recipient = RecipientIdentity.support_team()
                e1_logical = LogicalNotificationIdentity(
                    source_event=e1_source,
                    notification_type=NotificationType.ESCALATION_TEAM,
                    recipient=e1_recipient
                )
                e1_hash = e1_logical.get_hash()

                # Check outbox
                cur.execute("SELECT 1 FROM outbox_events WHERE logical_identity_hash = %s", (e1_hash,))
                e1_present = cur.fetchone() is not None

                planned.append(PlannedNotification(
                    source_event_id=e1_source.source_event_id,
                    notification_type=NotificationType.ESCALATION_TEAM,
                    recipient_identity=e1_recipient.principal,
                    recipient_address="support@example.com",  # Default system team address
                    logical_identity_hash=e1_hash,
                    status=ReconciliationStatus.PRESENT if e1_present else ReconciliationStatus.MISSING
                ))

                # 3. Reconstruct E2 (Customer)
                e2_source = SourceEventIdentity.from_escalation(escalation_event_id)
                e2_recipient = RecipientIdentity.customer(customer_id)
                e2_logical = LogicalNotificationIdentity(
                    source_event=e2_source,
                    notification_type=NotificationType.ESCALATION_CUSTOMER,
                    recipient=e2_recipient
                )
                e2_hash = e2_logical.get_hash()

                if not customer_notif_req:
                    status = ReconciliationStatus.NOT_REQUIRED
                else:
                    cur.execute("SELECT 1 FROM outbox_events WHERE logical_identity_hash = %s", (e2_hash,))
                    status = ReconciliationStatus.PRESENT if cur.fetchone() is not None else ReconciliationStatus.MISSING

                planned.append(PlannedNotification(
                    source_event_id=e2_source.source_event_id,
                    notification_type=NotificationType.ESCALATION_CUSTOMER,
                    recipient_identity=e2_recipient.principal,
                    recipient_address=customer_email,
                    logical_identity_hash=e2_hash,
                    status=status
                ))

        return planned

    def plan_for_ticket(self, client_request_id: str, customer_id: int) -> List[PlannedNotification]:
        """
        Reconstruct notification intents for a ticket mutation (CREATE or RESOLVE).
        The idempotency_records table links the request ID to the mutation type.
        """
        planned: List[PlannedNotification] = []

        with psycopg2.connect(self.dsn) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Fetch authoritative source
                cur.execute("""
                    SELECT i.operation_type, i.terminal_result, c.email
                    FROM idempotency_records i
                    JOIN customers c ON i.customer_id = c.id
                    WHERE i.client_request_id = %s AND i.customer_id = %s
                """, (client_request_id, customer_id))
                
                row = cur.fetchone()
                if not row:
                    return planned

                op_type = row["operation_type"]
                customer_email = row["email"]

                if op_type not in ("create_ticket", "update_ticket"):
                    return planned

                if op_type == "create_ticket":
                    notif_type = NotificationType.TICKET_CREATED
                    source_ident = SourceEventIdentity.from_ticket_creation(customer_id, client_request_id)
                else:
                    # update_ticket does NOT durably prove in idempotency_records whether it was
                    # specifically a resolution operation vs a generic update.
                    # Therefore, TICKET_RESOLVED is NOT_RECONSTRUCTIBLE from idempotency_records alone
                    # and must not be manufactured here by assumption.
                    return planned

                recipient = RecipientIdentity.customer(customer_id)
                logical = LogicalNotificationIdentity(
                    source_event=source_ident,
                    notification_type=notif_type,
                    recipient=recipient
                )
                ident_hash = logical.get_hash()

                cur.execute("SELECT 1 FROM outbox_events WHERE logical_identity_hash = %s", (ident_hash,))
                present = cur.fetchone() is not None

                planned.append(PlannedNotification(
                    source_event_id=source_ident.source_event_id,
                    notification_type=notif_type,
                    recipient_identity=recipient.principal,
                    recipient_address=customer_email,
                    logical_identity_hash=ident_hash,
                    status=ReconciliationStatus.PRESENT if present else ReconciliationStatus.MISSING
                ))

        return planned

    def plan_for_custom_email(self, client_request_id: str, customer_id: int) -> List[PlannedNotification]:
        """
        Custom email relies on idempotency_records, but the LLM payload (subject/body)
        is not stored anywhere except the outbox payload.
        Therefore, if the outbox row is missing, it is NOT_RECONSTRUCTIBLE.
        """
        planned: List[PlannedNotification] = []

        with psycopg2.connect(self.dsn) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT c.email
                    FROM customers c
                    WHERE c.id = %s
                """, (customer_id,))
                
                row = cur.fetchone()
                if not row:
                    return planned
                
                customer_email = row["email"]

                source_ident = SourceEventIdentity.from_custom_email(customer_id, client_request_id)
                recipient = RecipientIdentity.customer(customer_id)
                logical = LogicalNotificationIdentity(
                    source_event=source_ident,
                    notification_type=NotificationType.CUSTOM_EMAIL,
                    recipient=recipient
                )
                ident_hash = logical.get_hash()

                cur.execute("SELECT 1 FROM outbox_events WHERE logical_identity_hash = %s", (ident_hash,))
                present = cur.fetchone() is not None

                planned.append(PlannedNotification(
                    source_event_id=source_ident.source_event_id,
                    notification_type=NotificationType.CUSTOM_EMAIL,
                    recipient_identity=recipient.principal,
                    recipient_address=customer_email,
                    logical_identity_hash=ident_hash,
                    status=ReconciliationStatus.PRESENT if present else ReconciliationStatus.NOT_RECONSTRUCTIBLE
                ))

        return planned
