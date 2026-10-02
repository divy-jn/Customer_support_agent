"""
Durable escalation-state persistence (Phase F.2.8.3 Slice 3.5.1).

Provides concurrency-safe initialization and authoritative reads for the
per-session escalation lifecycle. Postgres is the authoritative source.
"""
from typing import Optional
import logging
from app.models import EscalationLifecycleStatus
from app.tools import supabase, _supabase_retry

logger = logging.getLogger(__name__)

@_supabase_retry
def ensure_durable_escalation_state(session_id: str, customer_id: int) -> EscalationLifecycleStatus:
    """
    Ensures that exactly one durable state record exists for the given session.
    If it does not exist, creates it with status 'NONE' (concurrency-safe).
    Returns the authoritative current status from Postgres.
    """
    if not session_id or not customer_id:
        raise ValueError("session_id and customer_id are required")
        
    try:
        # Attempt to insert the initial state. 
        # If it fails due to a unique constraint (concurrent request), we catch the exception.
        supabase.table("session_escalation_state").insert({
            "session_id": session_id,
            "customer_id": customer_id,
            "status": EscalationLifecycleStatus.NONE.value
        }).execute()
        return EscalationLifecycleStatus.NONE
    except Exception as e:
        logger.debug(f"Insert failed for session {session_id}, likely concurrent creation or exists. Fetching existing...")
        # Fall through to fetch the existing state
        pass
            
    res = supabase.table("session_escalation_state").select("status, customer_id").eq("session_id", session_id).execute()
    if res.data:
        if res.data[0]["customer_id"] != customer_id:
            raise ValueError(f"Session {session_id} belongs to a different customer.")
        return EscalationLifecycleStatus(res.data[0]["status"])
        
    raise RuntimeError(f"Failed to ensure durable escalation state for session {session_id}")
    
@_supabase_retry
def get_durable_escalation_state(session_id: str, customer_id: int) -> Optional[EscalationLifecycleStatus]:
    """
    Retrieves the authoritative current status from Postgres.
    Returns None if the record does not exist.
    """
    res = supabase.table("session_escalation_state").select("status, customer_id").eq("session_id", session_id).execute()
    if res.data:
        if res.data[0]["customer_id"] != customer_id:
            raise ValueError(f"Session {session_id} belongs to a different customer.")
        return EscalationLifecycleStatus(res.data[0]["status"])
    return None
