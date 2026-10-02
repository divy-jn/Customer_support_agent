import logging
from enum import Enum
import json
from typing import Dict, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from app.notification_identity import (
    NotificationType,
    SourceEventIdentity,
    RecipientIdentity,
    LogicalNotificationIdentity
)

logger = logging.getLogger("outbox.repair")

class RepairResultStatus(str, Enum):
    ENQUEUED = "ENQUEUED"
    ALREADY_PRESENT = "ALREADY_PRESENT"
    SOURCE_GONE = "SOURCE_GONE"
    NOT_REQUIRED = "NOT_REQUIRED"
    NOT_RECONSTRUCTIBLE = "NOT_RECONSTRUCTIBLE"
    CONFLICT = "CONFLICT"

class OutboxRepairService:
    """
    Executes repairs for missing outbox intents by verifying the authoritative source
    and conditionally inserting an outbox_events row within a single database transaction.
    """

    def __init__(self, dsn: str):
        self.dsn = dsn

    def repair_escalation_event(self, escalation_event_id: str, customer_id: int) -> Dict[NotificationType, RepairResultStatus]:
        """
        Conditionally repairs ESCALATION_TEAM and ESCALATION_CUSTOMER notifications.
        Requires exact revalidation of the authoritative source inside the transaction.
        """
        results: Dict[NotificationType, RepairResultStatus] = {}
        
        with psycopg2.connect(self.dsn) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Lock/read authoritative source
                cur.execute("""
                    SELECT e.customer_id, e.session_id, e.sentiment, e.urgency, e.ticket_id, 
                           e.customer_notification_required, c.email, c.name as customer_name
                    FROM escalation_events e
                    JOIN customers c ON e.customer_id = c.id
                    WHERE e.escalation_event_id = %s
                    FOR SHARE
                """, (escalation_event_id,))
                
                row = cur.fetchone()
                if not row:
                    results[NotificationType.ESCALATION_TEAM] = RepairResultStatus.SOURCE_GONE
                    results[NotificationType.ESCALATION_CUSTOMER] = RepairResultStatus.SOURCE_GONE
                    return results

                # Authorization Validation
                if row["customer_id"] != customer_id:
                    results[NotificationType.ESCALATION_TEAM] = RepairResultStatus.CONFLICT
                    results[NotificationType.ESCALATION_CUSTOMER] = RepairResultStatus.CONFLICT
                    return results

                # Helper to perform the atomic insertion
                def attempt_repair(notification_type: NotificationType, recipient: RecipientIdentity, email: str, payload: dict) -> RepairResultStatus:
                    source = SourceEventIdentity.from_escalation(escalation_event_id)
                    logical = LogicalNotificationIdentity(
                        source_event=source,
                        notification_type=notification_type,
                        recipient=recipient
                    )
                    ident_hash = logical.get_hash()
                    
                    cur.execute("""
                        INSERT INTO outbox_events (
                            logical_identity_hash, source_event_id, notification_type,
                            recipient_identity, recipient_address, payload, status
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, 'PENDING'
                        ) ON CONFLICT (logical_identity_hash) DO NOTHING
                        RETURNING event_id
                    """, (
                        ident_hash, source.source_event_id, notification_type.value,
                        recipient.principal, email, json.dumps(payload)
                    ))
                    
                    inserted = cur.fetchone()
                    return RepairResultStatus.ENQUEUED if inserted else RepairResultStatus.ALREADY_PRESENT

                # E1: Team
                team_payload = {
                    "session_id": row["session_id"],
                    "sentiment": row["sentiment"],
                    "urgency": row["urgency"],
                    "ticket_id": row["ticket_id"],
                    "customer_id": row["customer_id"]
                }
                results[NotificationType.ESCALATION_TEAM] = attempt_repair(
                    NotificationType.ESCALATION_TEAM, 
                    RecipientIdentity.support_team(), 
                    "support@example.com", 
                    team_payload
                )

                # E2: Customer
                if not row["customer_notification_required"]:
                    results[NotificationType.ESCALATION_CUSTOMER] = RepairResultStatus.NOT_REQUIRED
                else:
                    # Historical payload for ESCALATION_CUSTOMER depends on `customer_name` 
                    # which is mutable. We cannot safely reconstruct the historical notification payload.
                    results[NotificationType.ESCALATION_CUSTOMER] = RepairResultStatus.NOT_RECONSTRUCTIBLE

            conn.commit()
            
        return results

    def repair_ticket_creation(self, client_request_id: str, customer_id: int) -> RepairResultStatus:
        """
        Conditionally repairs TICKET_CREATED notification by revalidating idempotency_records
        and tickets tables to reconstruct the payload.
        """
        with psycopg2.connect(self.dsn) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # 1. Revalidate idempotency record
                cur.execute("""
                    SELECT operation_type, terminal_result
                    FROM idempotency_records
                    WHERE client_request_id = %s AND customer_id = %s
                    FOR SHARE
                """, (client_request_id, customer_id))
                
                idem_row = cur.fetchone()
                if not idem_row:
                    return RepairResultStatus.SOURCE_GONE
                
                if idem_row["operation_type"] != "create_ticket":
                    return RepairResultStatus.CONFLICT
                
                terminal_result = idem_row["terminal_result"]
                if not terminal_result or "ticket_id" not in terminal_result:
                    return RepairResultStatus.NOT_RECONSTRUCTIBLE
                
                ticket_id = terminal_result["ticket_id"]

                # 2. Historical creation data (subject, description, priority) is NOT durably stored 
                # in idempotency_records and the tickets table is mutable. We cannot reconstruct
                # the exact historical creation payload safely.
                return RepairResultStatus.NOT_RECONSTRUCTIBLE

            conn.commit()
            
        return result
