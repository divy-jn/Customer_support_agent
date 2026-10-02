import logging
import time
from datetime import datetime
from typing import List, Optional
from enum import Enum
from pydantic import BaseModel
import psycopg2

from app.outbox.reconciliation_planner import ReconciliationPlanner, ReconciliationStatus, PlannedNotification
from app.outbox.repair_service import OutboxRepairService, RepairResultStatus
from app.notification_identity import NotificationType

logger = logging.getLogger("outbox.coordinator")

class CoordinatorResultStatus(str, Enum):
    SCANNED = "SCANNED"
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    REPAIRED = "REPAIRED"
    ALREADY_PRESENT = "ALREADY_PRESENT"
    NOT_REQUIRED = "NOT_REQUIRED"
    NOT_RECONSTRUCTIBLE = "NOT_RECONSTRUCTIBLE"
    SOURCE_GONE = "SOURCE_GONE"
    CONFLICT = "CONFLICT"
    ERROR = "ERROR"

class ReconciliationResult(BaseModel):
    source_event_id: str
    notification_type: NotificationType
    status: CoordinatorResultStatus
    duration_ms: float
    error_class: Optional[str] = None

class BatchCoordinatorResult(BaseModel):
    results: List[ReconciliationResult]

REPAIRABLE_TYPES = {
    NotificationType.ESCALATION_TEAM
}

class ReconciliationCoordinator:
    """
    Thin coordinator that invokes the planner and delegates to the repair service.
    It enforces the boundary that only specific notifications are currently reconstructible.
    """
    
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.planner = ReconciliationPlanner(dsn)
        self.repair_service = OutboxRepairService(dsn)

    def reconcile_escalation_event(self, escalation_event_id: str) -> List[ReconciliationResult]:
        """Reconcile a single escalation event."""
        # 1. Fetch customer_id for this escalation from authoritative source
        customer_id = None
        with psycopg2.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT customer_id FROM escalation_events WHERE escalation_event_id = %s", (escalation_event_id,))
                row = cur.fetchone()
                if row:
                    customer_id = row[0]
        
        if not customer_id:
            # Source gone before planning
            return [ReconciliationResult(
                source_event_id=f"escalation:{escalation_event_id}",
                notification_type=NotificationType.ESCALATION_TEAM,
                status=CoordinatorResultStatus.SOURCE_GONE,
                duration_ms=0.0
            )]

        # 2. Invoke planner (read-only)
        planned_list = self.planner.plan_for_escalation_event(escalation_event_id)
        
        # 3. Execute plans
        return self._execute_plans(planned_list, escalation_event_id=escalation_event_id, customer_id=customer_id)

    def batch_reconcile_escalations(self, escalation_event_ids: List[str]) -> BatchCoordinatorResult:
        """Batch reconcile explicitly supplied source IDs. Bounded batch size."""
        if len(escalation_event_ids) > 100:
            raise ValueError("Batch size exceeds maximum limit of 100")
        
        batch = escalation_event_ids
        results = []
        for eid in batch:
            try:
                # Per-item result isolation; one failure does not abort the batch
                results.extend(self.reconcile_escalation_event(eid))
            except Exception as e:
                logger.error(f"Top-level failure for escalation {eid}: {e}")
        return BatchCoordinatorResult(results=results)

    def _execute_plans(self, planned_list: List[PlannedNotification], escalation_event_id: Optional[str] = None, customer_id: Optional[int] = None) -> List[ReconciliationResult]:
        results = []
        
        for plan in planned_list:
            start_time = time.time()
            status = CoordinatorResultStatus.ERROR
            error_class = None
            
            try:
                if plan.status == ReconciliationStatus.PRESENT:
                    status = CoordinatorResultStatus.PRESENT
                elif plan.status == ReconciliationStatus.NOT_REQUIRED:
                    status = CoordinatorResultStatus.NOT_REQUIRED
                elif plan.status == ReconciliationStatus.NOT_RECONSTRUCTIBLE:
                    status = CoordinatorResultStatus.NOT_RECONSTRUCTIBLE
                elif plan.status == ReconciliationStatus.MISSING:
                    # Select only repairable missing results
                    if plan.notification_type not in REPAIRABLE_TYPES:
                        status = CoordinatorResultStatus.NOT_RECONSTRUCTIBLE
                    else:
                        # Invoke RepairService
                        if plan.notification_type == NotificationType.ESCALATION_TEAM:
                            repair_res = self.repair_service.repair_escalation_event(escalation_event_id, customer_id)
                            team_status = repair_res.get(NotificationType.ESCALATION_TEAM)
                            
                            if team_status == RepairResultStatus.ENQUEUED:
                                status = CoordinatorResultStatus.REPAIRED
                            elif team_status == RepairResultStatus.ALREADY_PRESENT:
                                status = CoordinatorResultStatus.ALREADY_PRESENT
                            elif team_status == RepairResultStatus.SOURCE_GONE:
                                status = CoordinatorResultStatus.SOURCE_GONE
                            elif team_status == RepairResultStatus.CONFLICT:
                                status = CoordinatorResultStatus.CONFLICT
                            elif team_status == RepairResultStatus.NOT_RECONSTRUCTIBLE:
                                status = CoordinatorResultStatus.NOT_RECONSTRUCTIBLE
                            elif team_status == RepairResultStatus.NOT_REQUIRED:
                                status = CoordinatorResultStatus.NOT_REQUIRED
                            else:
                                status = CoordinatorResultStatus.ERROR
                        else:
                            status = CoordinatorResultStatus.NOT_RECONSTRUCTIBLE
            except Exception as e:
                status = CoordinatorResultStatus.ERROR
                error_class = e.__class__.__name__

            duration = (time.time() - start_time) * 1000
            
            results.append(ReconciliationResult(
                source_event_id=plan.source_event_id,
                notification_type=plan.notification_type,
                status=status,
                duration_ms=duration,
                error_class=error_class
            ))
            
        return results

    def discover_and_reconcile_escalations(
        self,
        horizon_days: float = 7.0,
        batch_size: int = 100,
        last_created_at: Optional[datetime] = None,
        last_event_id: Optional[str] = None,
        sweep_started_at: Optional[datetime] = None
    ) -> tuple[BatchCoordinatorResult, Optional[datetime], Optional[str]]:
        """
        Discover up to `batch_size` escalation events within `horizon_days` and reconcile them.
        Uses keyset pagination via (created_at, escalation_event_id).
        """
        if batch_size > 100:
            raise ValueError("Batch size exceeds maximum limit of 100")
            
        events = []
        if not sweep_started_at:
            sweep_started_at = datetime.now()
            
        with psycopg2.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                # Base query
                query = """
                    SELECT escalation_event_id, created_at 
                    FROM escalation_events 
                    WHERE created_at <= %s AND created_at >= %s - INTERVAL '%s days'
                """
                params = [sweep_started_at, sweep_started_at, horizon_days]
                
                if last_created_at and last_event_id:
                    query += " AND (created_at, escalation_event_id) < (%s, %s)"
                    params.extend([last_created_at, str(last_event_id)])
                
                query += " ORDER BY created_at DESC, escalation_event_id DESC LIMIT %s"
                params.append(batch_size)
                
                cur.execute(query, tuple(params))
                events = cur.fetchall()
        
        if not events:
            return BatchCoordinatorResult(results=[]), None, None
            
        event_ids = [str(e[0]) for e in events]
        next_created_at = events[-1][1]
        next_event_id = str(events[-1][0])
        
        batch_result = self.batch_reconcile_escalations(event_ids)
        
        return batch_result, next_created_at, next_event_id
