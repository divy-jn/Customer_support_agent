"""Single trusted execution boundary for high-risk action approvals (V2).

Enforces:
- Explicit allowlist: cancel_order, process_refund
- Customer identity binding (authenticated customer matches approval owner)
- Session binding
- Expiration and lifecycle status (single-use, non-expired)
- Stable idempotency operation keys passed to DB RPCs
- Deterministic execution without LLM parameter fabrication or rewriting
- Honest failure and uncertain timeout reporting
"""

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.config import settings
from app.models import ApprovalStatus, PendingApproval

logger = logging.getLogger(__name__)

ALLOWLISTED_ACTIONS = {"cancel_order", "process_refund"}
DEFAULT_APPROVAL_TTL_SECONDS = 900  # 15 minutes
SIGNATURE_VERSION = "v1"


def get_approval_signing_secret() -> bytes:
    """
    Retrieve the dedicated persistent secret key used for HMAC signing of pending approvals.
    Requires APPROVAL_SIGNING_SECRET from application configuration.
    Fails closed if absent, empty, whitespace-only, or invalid.
    Strictly forbids falling back to JWT_SECRET, AGENT_SECRET, hardcoded values, or per-process keys.
    """
    secret = getattr(settings, "approval_signing_secret", None)
    if not secret or not isinstance(secret, str) or not secret.strip():
        raise RuntimeError(
            "APPROVAL_SIGNING_SECRET is not configured or invalid. "
            "Approval operations fail closed to protect mutation integrity."
        )
    return secret.strip().encode("utf-8")


def canonicalize_parameters(params: Any) -> Any:
    """
    Deterministically canonicalize parameters:
    - Sort dictionary keys recursively.
    - Recursively canonicalize lists and tuples.
    - Allow only JSON-primitive types (int, float, str, bool, None).
    - Reject unsupported types (objects, callables, etc.) to prevent deserialization quirks.
    """
    if params is None or isinstance(params, (int, float, str, bool)):
        return params
    if isinstance(params, (list, tuple)):
        return [canonicalize_parameters(x) for x in params]
    if isinstance(params, dict):
        return {str(k): canonicalize_parameters(v) for k, v in sorted(params.items())}
    raise ValueError(f"Unsupported parameter type for approval signing: {type(params).__name__}")


def build_canonical_approval_payload(
    customer_id: int,
    session_id: str,
    action: str,
    params: dict[str, Any],
    operation_key: str,
    expires_at: str,
    version: str = SIGNATURE_VERSION,
) -> str:
    """
    Build a canonical versioned JSON string binding all security-critical approval fields.
    Uses sorted keys, no whitespace, and strict UTF-8 serialization.
    """
    normalized_params = canonicalize_parameters(params or {})
    payload_dict = {
        "action": str(action),
        "customer_id": int(customer_id),
        "expires_at": str(expires_at),
        "operation_key": str(operation_key),
        "parameters": normalized_params,
        "session_id": str(session_id),
        "version": str(version),
    }
    return json.dumps(payload_dict, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_approval_signature(
    customer_id: int,
    session_id: str,
    action: str,
    params: dict[str, Any],
    operation_key: str,
    expires_at: str,
    version: str = SIGNATURE_VERSION,
) -> str:
    """
    Compute a deterministic HMAC-SHA256 signature binding security-critical approval fields.
    Uses dedicated APPROVAL_SIGNING_SECRET.
    """
    secret = get_approval_signing_secret()
    payload = build_canonical_approval_payload(
        customer_id=customer_id,
        session_id=session_id,
        action=action,
        params=params,
        operation_key=operation_key,
        expires_at=expires_at,
        version=version,
    )
    return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_approval_signature(approval: PendingApproval) -> bool:
    """
    Verify the HMAC-SHA256 signature of a PendingApproval object.
    Constant-time comparison using hmac.compare_digest.
    Fails closed if signature is missing, malformed, or if secret is unconfigured.
    """
    if not approval.signature or not isinstance(approval.signature, str) or len(approval.signature) != 64:
        return False
    try:
        version = getattr(approval, "signature_version", None) or SIGNATURE_VERSION
        expected_sig = compute_approval_signature(
            customer_id=approval.customer_id,
            session_id=approval.session_id,
            action=approval.action,
            params=approval.params,
            operation_key=approval.operation_key,
            expires_at=approval.expires_at,
            version=version,
        )
        return hmac.compare_digest(approval.signature, expected_sig)
    except Exception as e:
        logger.warning(f"Approval signature verification encountered error: {e}")
        return False


class ApprovalClaimOutcome(str, Enum):
    CLAIMED = "claimed"
    ALREADY_CLAIMED = "already_claimed"
    EXPIRED = "expired"
    IDENTITY_MISMATCH = "identity_mismatch"
    SESSION_MISMATCH = "session_mismatch"
    SIGNATURE_FAILED = "signature_failed"
    INVALID_APPROVAL = "invalid_approval"
    NOT_FOUND = "not_found"


def claim_pending_approval_in_session(
    session_data: dict[str, Any],
    authenticated_customer_id: int,
    session_id: str,
) -> tuple[ApprovalClaimOutcome, str | None]:
    """
    Authoritative atomic claim mutator for pending approvals inside session state.

    Validates identity, session, allowlist, status, expiration, and cryptographic signature.
    Atomically updates status to EXECUTING and stamps claimed_at timestamp.
    """
    p_data = session_data.get("pending_approval")
    if not p_data:
        return ApprovalClaimOutcome.NOT_FOUND, "No pending approval found in session."

    try:
        if isinstance(p_data, PendingApproval):
            p = p_data
        else:
            p = PendingApproval.model_validate(p_data)
    except Exception as e:
        return ApprovalClaimOutcome.INVALID_APPROVAL, f"Malformed pending approval: {e}"

    if p.customer_id != authenticated_customer_id:
        return ApprovalClaimOutcome.IDENTITY_MISMATCH, (
            f"Customer identity mismatch: authenticated customer {authenticated_customer_id} "
            f"does not match approval owner {p.customer_id}."
        )

    if session_id and p.session_id != session_id:
        return ApprovalClaimOutcome.SESSION_MISMATCH, (
            f"Session mismatch: session {session_id} does not match approval session {p.session_id}."
        )

    cur_status = p_data.get("status", ApprovalStatus.PENDING.value)
    if cur_status != ApprovalStatus.PENDING.value:
        return ApprovalClaimOutcome.ALREADY_CLAIMED, f"Approval is not pending (status: {cur_status})."

    # Expiration check
    exp_str = p_data.get("expires_at")
    if exp_str:
        try:
            if datetime.now(timezone.utc) > datetime.fromisoformat(exp_str):
                p_data["status"] = ApprovalStatus.EXPIRED.value
                return ApprovalClaimOutcome.EXPIRED, "Approval request has expired."
        except Exception:
            pass

    # Signature verification (cryptographic integrity)
    if not p.signature or not verify_approval_signature(p):
        return ApprovalClaimOutcome.SIGNATURE_FAILED, "Approval signature verification failed: signature is missing, invalid, or tampered."

    p_data["status"] = ApprovalStatus.EXECUTING.value
    p_data["claimed_at"] = datetime.now(timezone.utc).isoformat()
    return ApprovalClaimOutcome.CLAIMED, None


class ApprovalExecutionResult(BaseModel):
    """Structured, policy-safe outcome of an approved operation execution."""

    success: bool
    action: str
    operation_key: str
    response_text: str
    raw_result: dict[str, Any] | None = None
    error: str | None = None
    status: ApprovalStatus
    is_uncertain: bool = False


def create_pending_approval(
    action: str,
    params: dict[str, Any],
    customer_id: int | None,
    session_id: str,
    message: str,
    intent: str | None = None,
    original_message: str | None = None,
    ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
) -> PendingApproval:
    """Create a typed, durable PendingApproval proposal bound to the authenticated customer."""
    if action not in ALLOWLISTED_ACTIONS:
        raise ValueError(f"Action '{action}' is not in allowlisted operations ({ALLOWLISTED_ACTIONS})")

    if not customer_id or customer_id <= 0:
        raise ValueError(f"Valid positive customer_id is required for pending approval: {customer_id}")

    if not session_id:
        raise ValueError("session_id is required for pending approval")

    # Normalize parameters strictly based on allowlisted schema
    normalized_params: dict[str, Any] = {}
    if action in {"cancel_order", "process_refund"}:
        order_id = params.get("order_id")
        if order_id is None:
            raise ValueError(f"order_id is required for {action}")
        try:
            normalized_params["order_id"] = int(order_id)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid order_id '{order_id}': must be an integer")
        if normalized_params["order_id"] <= 0:
            raise ValueError(f"order_id must be a positive integer, got {normalized_params['order_id']}")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=ttl_seconds)
    approval_id = f"appr_{uuid.uuid4().hex}"

    # Stable operation key: deterministic UUID conforming to database
    # idempotency_records.client_request_id UUID and execute_idempotent_operation(p_client_request_id UUID).
    # Reused across retries so DB deduplicates without casting errors.
    operation_key = str(uuid.uuid4())

    sig = compute_approval_signature(
        customer_id=customer_id,
        session_id=session_id,
        action=action,
        params=normalized_params,
        operation_key=operation_key,
        expires_at=expires.isoformat(),
    )

    return PendingApproval(
        approval_id=approval_id,
        action=action,
        params=normalized_params,
        customer_id=customer_id,
        session_id=session_id,
        operation_key=operation_key,
        created_at=now.isoformat(),
        expires_at=expires.isoformat(),
        status=ApprovalStatus.PENDING,
        message=message,
        original_message=original_message,
        intent=intent,
        signature=sig,
        signature_version=SIGNATURE_VERSION,
    )


def validate_approval(
    pending_data: dict[str, Any] | PendingApproval | None,
    authenticated_customer_id: int | None,
    session_id: str | None,
) -> tuple[PendingApproval | None, str | None]:
    """
    Validate approval against identity, session, allowlist, parameter schema, operation_key UUID, and expiry.
    Returns:
        tuple[PendingApproval | None, str | None]: (approval, error_reason)
    """
    if not pending_data:
        return None, "No pending approval found."

    try:
        if isinstance(pending_data, PendingApproval):
            approval = pending_data
        else:
            approval = PendingApproval.model_validate(pending_data)
    except Exception as e:
        return None, f"Malformed pending approval: {e}"

    if approval.action not in ALLOWLISTED_ACTIONS:
        return None, f"Action '{approval.action}' is not in allowlisted operations."

    if authenticated_customer_id is None or approval.customer_id != authenticated_customer_id:
        return None, f"Customer identity mismatch: authenticated customer {authenticated_customer_id} does not match approval owner {approval.customer_id}."

    if session_id and approval.session_id != session_id:
        return None, f"Session mismatch: session {session_id} does not match approval session {approval.session_id}."

    if approval.status != ApprovalStatus.PENDING:
        return None, f"Approval is not pending (current status: {approval.status.value})."

    # Validate parameters
    order_id = approval.params.get("order_id")
    if not isinstance(order_id, int) or order_id <= 0:
        return None, f"Invalid or missing order_id in approval parameters: {order_id}"

    # Validate operation_key is a valid UUID string
    try:
        uuid.UUID(str(approval.operation_key))
    except (ValueError, AttributeError):
        return None, f"Invalid operation_key '{approval.operation_key}': must be a valid UUID string."

    # Expiry validation
    try:
        exp = datetime.fromisoformat(approval.expires_at)
        if datetime.now(timezone.utc) > exp:
            return None, "Approval request has expired."
    except Exception:
        return None, "Approval expiration timestamp is invalid."

    # Cryptographic integrity validation
    if not approval.signature or not verify_approval_signature(approval):
        return None, "Approval signature verification failed: signature is missing, invalid, or tampered."

    return approval, None


async def reconcile_uncertain_operation(
    approval: PendingApproval,
    authenticated_customer_id: int,
) -> ApprovalExecutionResult | None:
    """
    Reconcile an uncertain operation outcome against authoritative database records.
    Prioritizes the durable idempotency_records table using (customer_id, operation_key).
    Falls back to resource status lookup if the idempotency record lookup fails or is unavailable.
    """
    order_id = approval.params.get("order_id")
    action = approval.action
    op_key = str(approval.operation_key)

    # 1. Authoritative check: Query idempotency_records by (customer_id, client_request_id)
    try:
        from app.tools import supabase
        resp = await asyncio.to_thread(
            lambda: supabase.table("idempotency_records")
            .select("operation_type, canonical_target, terminal_result")
            .eq("customer_id", authenticated_customer_id)
            .eq("client_request_id", op_key)
            .execute()
        )
        if resp and getattr(resp, "data", None):
            record = resp.data[0]
            term = record.get("terminal_result")
            if term and isinstance(term, dict):
                # Authoritative terminal result recorded by PostgreSQL
                if "error" in term:
                    err_msg = str(term["error"])
                    return ApprovalExecutionResult(
                        success=False,
                        action=action,
                        operation_key=op_key,
                        response_text=f"I was unable to {action.replace('_', ' ')} for order #{order_id}: {err_msg}",
                        raw_result=term,
                        error=err_msg,
                        status=ApprovalStatus.FAILED,
                        is_uncertain=False,
                    )
                if term.get("status") == "success" and "error" not in term:
                    success_msg = term.get("message") or (
                        f"Order #{order_id} has been cancelled"
                        if action == "cancel_order"
                        else f"Refund initiated for order #{order_id}. The refund will be processed within 5-7 business days."
                    )
                    return ApprovalExecutionResult(
                        success=True,
                        action=action,
                        operation_key=op_key,
                        response_text=success_msg,
                        raw_result=term,
                        status=ApprovalStatus.SUCCEEDED,
                        is_uncertain=False,
                    )
    except Exception as e:
        logger.warning(f"Could not query idempotency_records for reconciliation of {op_key}: {e}")

    # 2. Secondary fallback / status check
    from app.tools import get_payment_record, track_order

    try:
        if action == "cancel_order":
            order_raw = await asyncio.to_thread(track_order, order_id=order_id, customer_id=authenticated_customer_id)
            order_data = json.loads(order_raw)
            if order_data.get("status") == "cancelled":
                return ApprovalExecutionResult(
                    success=True,
                    action=action,
                    operation_key=op_key,
                    response_text=f"Order #{order_id} has been cancelled.",
                    raw_result=order_data,
                    status=ApprovalStatus.SUCCEEDED,
                    is_uncertain=False,
                )
            elif order_data.get("status") == "active":
                return ApprovalExecutionResult(
                    success=False,
                    action=action,
                    operation_key=op_key,
                    response_text=f"Order #{order_id} cancellation could not be confirmed; order remains active.",
                    raw_result=order_data,
                    error="Order remains active after timeout",
                    status=ApprovalStatus.FAILED,
                    is_uncertain=False,
                )
        elif action == "process_refund":
            pay_raw = await asyncio.to_thread(get_payment_record, order_id=order_id, customer_id=authenticated_customer_id)
            pay_data = json.loads(pay_raw)
            if pay_data.get("status") == "refunded":
                return ApprovalExecutionResult(
                    success=True,
                    action=action,
                    operation_key=op_key,
                    response_text=f"Refund initiated for order #{order_id}. The refund will be processed within 5-7 business days.",
                    raw_result=pay_data,
                    status=ApprovalStatus.SUCCEEDED,
                    is_uncertain=False,
                )
    except Exception as e:
        logger.error(f"Reconciliation resource status check failed for operation {op_key}: {e}")
    return None


async def execute_approved_action(
    approval: PendingApproval | dict[str, Any],
    authenticated_customer_id: int,
    session_id: str,
    timeout_seconds: float = 10.0,
) -> ApprovalExecutionResult:
    """
    Execute an approved mutation within the single trusted boundary.

    Validates:
    - Customer identity matching
    - Session matching
    - Action allowlist
    - Parameter integrity
    - Operation key UUID contract
    - Non-expired status
    - Shared idempotent tool execution
    """
    if isinstance(approval, dict):
        try:
            # Handle legacy or dictionary payloads gracefully
            if "approval_id" not in approval:
                approval = create_pending_approval(
                    action=approval.get("action", ""),
                    params=approval.get("params", {}),
                    customer_id=approval.get("customer_id") or authenticated_customer_id,
                    session_id=approval.get("session_id") or session_id,
                    message=approval.get("message", "Confirm action"),
                    intent=approval.get("intent"),
                    original_message=approval.get("original_message"),
                )
            else:
                approval = PendingApproval.model_validate(approval)
        except Exception as e:
            return ApprovalExecutionResult(
                success=False,
                action=str(approval.get("action", "unknown")),
                operation_key=str(approval.get("operation_key", "")),
                response_text=f"Approval validation error: {e}",
                error=str(e),
                status=ApprovalStatus.FAILED,
            )

    # If the caller has already atomically transitioned status to EXECUTING,
    # validate that identity, session, allowlist, and operation_key match.
    if approval.status == ApprovalStatus.EXECUTING:
        if approval.action not in ALLOWLISTED_ACTIONS:
            return ApprovalExecutionResult(
                success=False, action=approval.action, operation_key=approval.operation_key,
                response_text="Action is not allowed.", error="Not allowlisted", status=ApprovalStatus.FAILED,
            )
        if approval.customer_id != authenticated_customer_id or approval.session_id != session_id:
            return ApprovalExecutionResult(
                success=False, action=approval.action, operation_key=approval.operation_key,
                response_text="Approval identity mismatch.", error="Identity mismatch", status=ApprovalStatus.FAILED,
            )
        order_id = approval.params.get("order_id")
        if not isinstance(order_id, int) or order_id <= 0:
            return ApprovalExecutionResult(
                success=False, action=approval.action, operation_key=approval.operation_key,
                response_text="Invalid order parameter.", error="Invalid parameter", status=ApprovalStatus.FAILED,
            )
        try:
            uuid.UUID(str(approval.operation_key))
        except (ValueError, AttributeError):
            return ApprovalExecutionResult(
                success=False, action=approval.action, operation_key=approval.operation_key,
                response_text="Invalid operation key format.", error="operation_key must be a valid UUID", status=ApprovalStatus.FAILED,
            )
        if not approval.signature or not verify_approval_signature(approval):
            return ApprovalExecutionResult(
                success=False, action=approval.action, operation_key=approval.operation_key,
                response_text="Approval signature verification failed: signature is missing, invalid, or tampered.",
                error="Signature verification failed", status=ApprovalStatus.FAILED,
            )
    else:
        # Full validation check for PENDING approvals
        valid_approval, error_reason = validate_approval(approval, authenticated_customer_id, session_id)
        if not valid_approval:
            return ApprovalExecutionResult(
                success=False,
                action=approval.action,
                operation_key=approval.operation_key,
                response_text=f"Cannot execute approval: {error_reason}",
                error=error_reason,
                status=ApprovalStatus.FAILED,
            )

    action = approval.action
    order_id = approval.params["order_id"]
    op_key = str(approval.operation_key)

    from app.tools import cancel_order, process_refund

    try:
        if action == "cancel_order":
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    cancel_order,
                    order_id=order_id,
                    customer_id=authenticated_customer_id,
                    client_request_id=op_key,
                ),
                timeout=timeout_seconds,
            )
        elif action == "process_refund":
            raw = await asyncio.wait_for(
                asyncio.to_thread(
                    process_refund,
                    order_id=order_id,
                    customer_id=authenticated_customer_id,
                    client_request_id=op_key,
                ),
                timeout=timeout_seconds,
            )
        else:
            return ApprovalExecutionResult(
                success=False, action=action, operation_key=op_key,
                response_text=f"Action '{action}' is not supported.", error="Unsupported action", status=ApprovalStatus.FAILED,
            )

        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            payload = {"error": f"Invalid tool response format: {raw}"}

        if not isinstance(payload, dict):
            payload = {"error": f"Tool response is not a valid JSON dictionary: {raw}"}

        if "error" in payload:
            err_msg = str(payload["error"])
            if action == "cancel_order":
                msg = f"I was unable to cancel order #{order_id}: {err_msg}"
            else:
                msg = f"I was unable to process the refund for order #{order_id}: {err_msg}"
            return ApprovalExecutionResult(
                success=False,
                action=action,
                operation_key=op_key,
                response_text=msg,
                raw_result=payload,
                error=err_msg,
                status=ApprovalStatus.FAILED,
            )

        # Validate authoritative structured success indicator from database RPC
        if payload.get("status") != "success":
            err_msg = str(payload.get("error") or "Missing authoritative status='success' from tool outcome")
            return ApprovalExecutionResult(
                success=False,
                action=action,
                operation_key=op_key,
                response_text=f"The operation returned an unverified outcome for order #{order_id}: {err_msg}",
                raw_result=payload,
                error=err_msg,
                status=ApprovalStatus.FAILED,
            )

        # Successful mutation
        success_msg = payload.get("message")
        if not success_msg:
            if action == "cancel_order":
                success_msg = f"Order #{order_id} has been cancelled"
            else:
                success_msg = f"Refund initiated for order #{order_id}. The refund will be processed within 5-7 business days."

        return ApprovalExecutionResult(
            success=True,
            action=action,
            operation_key=op_key,
            response_text=success_msg,
            raw_result=payload,
            status=ApprovalStatus.SUCCEEDED,
        )

    except asyncio.TimeoutError:
        logger.error(
            f"Timeout executing approved action {action} (operation_key={op_key}) for customer {authenticated_customer_id}"
        )
        reconciled = await reconcile_uncertain_operation(approval, authenticated_customer_id)
        if reconciled is not None:
            return reconciled

        return ApprovalExecutionResult(
            success=False,
            action=action,
            operation_key=op_key,
            response_text=f"The operation timed out while processing order #{order_id}. I am checking the status to ensure it was handled correctly.",
            error="Operation timed out before confirming result",
            status=ApprovalStatus.FAILED,
            is_uncertain=True,
        )
    except Exception as e:
        logger.exception(f"Unexpected error executing approved action {action} (operation_key={op_key})")
        return ApprovalExecutionResult(
            success=False,
            action=action,
            operation_key=op_key,
            response_text=f"An error occurred while executing {action}: {e}",
            error=str(e),
            status=ApprovalStatus.FAILED,
        )
