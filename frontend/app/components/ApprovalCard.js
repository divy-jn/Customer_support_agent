"use client";

import React from "react";

/**
 * Accessible, structured Customer Approval Card.
 *
 * Enforces:
 * - Clear distinction of all 8 lifecycle states (pending, executing, succeeded, failed, uncertain, expired, cancelled, ambiguous).
 * - Authoritative success only when backend explicitly confirms with authoritative=true.
 * - Stale UI events cannot execute mutations (disabled when not latest).
 * - High-contrast accessible buttons with ARIA attributes and keyboard focus.
 */
export default function ApprovalCard({ approval, onAction, isLatest = true }) {
  if (!approval) return null;

  const {
    approval_id,
    action,
    params = {},
    status = "pending",
    error = null,
    operation_key = null,
    authoritative = false,
  } = approval;

  const orderId = params.order_id || params.orderId || "";
  const actionTitle =
    action === "cancel_order"
      ? "Order Cancellation"
      : action === "process_refund"
      ? "Refund Request"
      : action || "High-Risk Action";

  return (
    <div
      role="region"
      aria-label={`Approval request for ${actionTitle}`}
      className="mt-3 pt-3 border-t border-[var(--border)] text-xs space-y-2.5 animate-fade-in"
      data-testid="approval-card"
      data-approval-status={status}
      data-approval-action={action}
      data-approval-order={orderId}
    >
      {/* ── 1. PENDING PROPOSAL ── */}
      {status === "pending" && (
        <div className="bg-indigo-950/40 border border-indigo-500/30 rounded-xl p-3.5 space-y-2.5">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-indigo-300 flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-indigo-400 animate-pulse" />
              Confirmation Required: {actionTitle}
            </span>
            {orderId && (
              <span className="px-2 py-0.5 rounded bg-indigo-500/20 text-indigo-300 font-mono text-[11px]">
                Order #{orderId}
              </span>
            )}
          </div>
          <p className="text-[11px] text-[var(--text-muted)] leading-relaxed">
            This action modifies your order record. Please review and provide explicit consent to proceed.
          </p>

          <div className="flex gap-2 pt-1">
            <button
              type="button"
              disabled={!isLatest}
              onClick={() => onAction && onAction(action, orderId, true)}
              aria-label={`Confirm ${actionTitle} for Order ${orderId}`}
              className="flex-1 py-2 px-3 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-medium text-xs transition-all shadow-md shadow-indigo-600/20 disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-indigo-400"
            >
              Confirm {actionTitle}
            </button>
            <button
              type="button"
              disabled={!isLatest}
              onClick={() => onAction && onAction(action, orderId, false)}
              aria-label={`Cancel ${actionTitle}`}
              className="py-2 px-3 rounded-lg bg-[var(--bg-input)] hover:bg-red-500/10 text-[var(--text-muted)] hover:text-red-300 border border-[var(--border)] hover:border-red-500/30 font-medium text-xs transition-all disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-red-400"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* ── 2. AMBIGUOUS AFFIRMATION (CLARIFICATION) ── */}
      {status === "ambiguous" && (
        <div className="bg-amber-950/40 border border-amber-500/30 rounded-xl p-3.5 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-semibold text-amber-300 flex items-center gap-1.5">
              <span>⚠️</span> Ambiguous Request — Clarification Needed
            </span>
            {orderId && (
              <span className="px-2 py-0.5 rounded bg-amber-500/20 text-amber-300 font-mono text-[11px]">
                Order #{orderId}
              </span>
            )}
          </div>
          <p className="text-[11px] text-amber-200/80 leading-relaxed">
            Your recent response was ambiguous because of a context switch. Did you intend to confirm {actionTitle} for Order #{orderId}?
          </p>
          <div className="flex gap-2 pt-1">
            <button
              type="button"
              disabled={!isLatest}
              onClick={() => onAction && onAction(action, orderId, true)}
              aria-label={`Explicitly confirm ${actionTitle} for Order ${orderId}`}
              className="flex-1 py-2 px-3 rounded-lg bg-amber-600 hover:bg-amber-500 text-white font-medium text-xs transition-all shadow-md shadow-amber-600/20 disabled:opacity-40 disabled:cursor-not-allowed focus:outline-none focus:ring-2 focus:ring-amber-400"
            >
              Yes, Confirm {actionTitle}
            </button>
            <button
              type="button"
              disabled={!isLatest}
              onClick={() => onAction && onAction(action, orderId, false)}
              aria-label={`Cancel ${actionTitle}`}
              className="py-2 px-3 rounded-lg bg-[var(--bg-input)] hover:bg-neutral-800 text-[var(--text-muted)] border border-[var(--border)] font-medium text-xs transition-all disabled:opacity-40 disabled:cursor-not-allowed"
            >
              No, Cancel
            </button>
          </div>
        </div>
      )}

      {/* ── 3. EXECUTING WITH AUTHORITATIVE BACKEND ── */}
      {status === "executing" && (
        <div className="bg-blue-950/40 border border-blue-500/30 rounded-xl p-3 flex items-center gap-2.5 text-blue-200">
          <span className="w-4 h-4 border-2 border-blue-400/40 border-t-blue-400 rounded-full animate-spin shrink-0" />
          <span className="text-[11px] font-medium">
            Processing {actionTitle} with authoritative ledger...
          </span>
        </div>
      )}

      {/* ── 4. AUTHORITATIVE SUCCESS ── */}
      {status === "succeeded" && authoritative && (
        <div className="bg-emerald-950/40 border border-emerald-500/30 rounded-xl p-3 space-y-1 text-emerald-200">
          <div className="flex items-center gap-1.5 font-semibold text-emerald-300">
            <span>✓</span> Authoritative Success: {actionTitle} Succeeded
          </div>
          {orderId && (
            <p className="text-[11px] text-emerald-200/80">
              Order #{orderId} has been successfully updated in the official database.
            </p>
          )}
          {operation_key && (
            <div className="pt-1 text-[10px] text-emerald-400/60 font-mono">
              Receipt ID: {operation_key}
            </div>
          )}
        </div>
      )}

      {/* ── 5. FAILURE ── */}
      {status === "failed" && (
        <div className="bg-red-950/40 border border-red-500/30 rounded-xl p-3 space-y-1 text-red-200">
          <div className="flex items-center gap-1.5 font-semibold text-red-300">
            <span>✕</span> {actionTitle} Failed
          </div>
          <p className="text-[11px] text-red-200/80">
            {error || "The operation could not be completed by the authoritative ledger."}
          </p>
        </div>
      )}

      {/* ── 6. UNCERTAIN OUTCOME ── */}
      {status === "uncertain" && (
        <div className="bg-amber-950/40 border border-amber-500/30 rounded-xl p-3 space-y-1 text-amber-200">
          <div className="flex items-center gap-1.5 font-semibold text-amber-300">
            <span>⚠️</span> Outcome Uncertain — Verification Active
          </div>
          <p className="text-[11px] text-amber-200/80 leading-relaxed">
            The operation timed out before confirmation was received. Idempotent background reconciliation is active. Please do not retry immediately.
          </p>
        </div>
      )}

      {/* ── 7. EXPIRED ── */}
      {status === "expired" && (
        <div className="bg-neutral-900/60 border border-neutral-700/40 rounded-xl p-2.5 text-[11px] text-neutral-400 flex items-center gap-2">
          <span>⏳</span> Approval request expired. Please ask again if still required.
        </div>
      )}

      {/* ── 8. CANCELLED / REJECTED ── */}
      {status === "cancelled" && (
        <div className="bg-neutral-900/60 border border-neutral-700/40 rounded-xl p-2.5 text-[11px] text-neutral-400 flex items-center gap-2">
          <span>✕</span> Action cancelled by user.
        </div>
      )}

      {/* ── 9. ALREADY CLAIMED ── */}
      {status === "already_claimed" && (
        <div className="bg-neutral-900/60 border border-neutral-700/40 rounded-xl p-2.5 text-[11px] text-neutral-400 flex items-center gap-2">
          <span>ℹ</span> Action already claimed or completed.
        </div>
      )}
    </div>
  );
}
