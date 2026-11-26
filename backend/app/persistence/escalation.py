"""
Durable escalation-state persistence (Phase F.2.8.3 Slice 3.5.1).

Provides concurrency-safe initialization and authoritative reads for the
per-session escalation lifecycle. Postgres is the authoritative source.
"""
from typing import Optional
from app.models import EscalationLifecycleStatus
from app.tools import supabase, _supabase_retry


class EscalationStateUnavailable(RuntimeError):
    """The authoritative escalation state could not be retrieved."""


def _read_state(session_id: str, customer_id: int) -> Optional[EscalationLifecycleStatus]:
    try:
        result = (
            supabase.table("session_escalation_state")
            .select("status, customer_id")
            .eq("session_id", session_id)
            .execute()
        )
        rows = result.data
    except Exception as exc:
        raise EscalationStateUnavailable("Escalation state lookup failed") from exc
    if not rows:
        return None
    if rows[0]["customer_id"] != customer_id:
        raise ValueError(f"Session {session_id} belongs to a different customer.")
    return EscalationLifecycleStatus(rows[0]["status"])


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
    except Exception as exc:
        state = _read_state(session_id, customer_id)
        if state is not None:
            return state
        raise EscalationStateUnavailable("Escalation state initialization failed") from exc


@_supabase_retry
def get_durable_escalation_state(session_id: str, customer_id: int) -> Optional[EscalationLifecycleStatus]:
    """Return the authoritative status, or None if the session has no row."""
    return _read_state(session_id, customer_id)
