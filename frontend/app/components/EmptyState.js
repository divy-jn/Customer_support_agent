/**
 * EmptyState — shown when a list/table/section has no data.
 *
 * Usage:
 *   <EmptyState
 *     icon="🎫"
 *     title="No tickets yet"
 *     description="Tickets will appear here when customers create them."
 *     action={{ label: "Create Ticket", onClick: () => {} }}
 *   />
 */

export default function EmptyState({
  icon = "📭",
  title = "Nothing here yet",
  description = "",
  action = null,
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 animate-fade-in">
      <div
        className="w-20 h-20 rounded-2xl bg-[var(--bg-input)] border border-[var(--border)] flex items-center justify-center text-3xl mb-5"
        style={{ opacity: 0.7 }}
      >
        {icon}
      </div>
      <h3 className="text-base font-semibold text-[var(--text-primary)] mb-1">
        {title}
      </h3>
      {description && (
        <p className="text-sm text-[var(--text-muted)] max-w-xs text-center leading-relaxed">
          {description}
        </p>
      )}
      {action && (
        <button
          onClick={action.onClick}
          className="mt-5 btn-glow px-5 py-2.5 text-sm"
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
