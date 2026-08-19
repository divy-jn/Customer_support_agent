/**
 * LoadingSkeleton — shimmer placeholders that match the shape of content.
 * 
 * Usage:
 *   <Skeleton width="100%" height={20} />
 *   <SkeletonCard />
 *   <SkeletonTable rows={5} />
 */

export function Skeleton({ width = "100%", height = 16, borderRadius = 8, className = "" }) {
  return (
    <div
      className={`skeleton ${className}`}
      style={{ width, height, borderRadius, flexShrink: 0 }}
    />
  );
}

export function SkeletonCard() {
  return (
    <div className="glass-card p-5 space-y-3 animate-fade-in">
      <div className="flex items-start justify-between">
        <div className="space-y-2 flex-1">
          <Skeleton width="40%" height={12} />
          <Skeleton width="60%" height={28} />
        </div>
        <Skeleton width={40} height={40} borderRadius={12} />
      </div>
      <Skeleton width="30%" height={12} />
    </div>
  );
}

export function SkeletonTable({ rows = 5, cols = 4 }) {
  return (
    <div className="glass-card overflow-hidden animate-fade-in">
      {/* Header */}
      <div className="px-6 py-3 border-b border-[var(--border)] flex gap-4">
        {Array.from({ length: cols }).map((_, i) => (
          <Skeleton key={i} width={`${100 / cols}%`} height={12} />
        ))}
      </div>
      {/* Rows */}
      {Array.from({ length: rows }).map((_, r) => (
        <div
          key={r}
          className="px-6 py-4 border-b border-[var(--border)]/30 flex gap-4"
        >
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton
              key={c}
              width={`${100 / cols}%`}
              height={14}
              borderRadius={6}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

export function SkeletonChat({ messages = 4 }) {
  const SKELETON_WIDTHS = [55, 42, 68, 50, 60, 45, 63, 48];
  
  return (
    <div className="space-y-4 p-4 animate-fade-in">
      {Array.from({ length: messages }).map((_, i) => {
        const isRight = i % 3 === 0;
        return (
          <div key={i} className={`flex ${isRight ? "justify-end" : "justify-start"}`}>
            <div style={{ width: `${SKELETON_WIDTHS[i % SKELETON_WIDTHS.length]}%` }}>
              <Skeleton height={isRight ? 48 : 64} borderRadius={16} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
