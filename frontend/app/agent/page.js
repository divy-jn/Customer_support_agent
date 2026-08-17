"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { SkeletonCard } from "../components/LoadingSkeleton";
import EmptyState from "../components/EmptyState";
import { API_BASE } from "../lib/api";

// ── Animated Counter Hook ──
function useAnimatedCounter(target, duration = 800) {
  const [value, setValue] = useState(0);
  const frameRef = useRef(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (target == null || target === 0) { setValue(0); return; }
    const start = performance.now();
    const from = 0;
    const animate = (now) => {
      const elapsed = now - start;
      const progress = Math.min(elapsed / duration, 1);
      // Ease out cubic
      const eased = 1 - Math.pow(1 - progress, 3);
      setValue(Math.round(from + (target - from) * eased));
      if (progress < 1) {
        frameRef.current = requestAnimationFrame(animate);
      }
    };
    frameRef.current = requestAnimationFrame(animate);
    return () => cancelAnimationFrame(frameRef.current);
  }, [target, duration]);

  return value;
}

function StatCard({ title, value, icon, color, subtext }) {
  const animatedValue = useAnimatedCounter(typeof value === "number" ? value : null);
  const displayValue = typeof value === "number" ? animatedValue : value;

  return (
    <div className="glass-card glass-card-hover p-5 animate-fade-in">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs text-[var(--text-muted)] uppercase tracking-wider font-medium">
            {title}
          </p>
          <p className="text-3xl font-bold mt-2 count-up" style={{ color }}>
            {displayValue ?? "—"}
          </p>
          {subtext && (
            <p className="text-xs text-[var(--text-muted)] mt-1">{subtext}</p>
          )}
        </div>
        <div
          className="w-10 h-10 rounded-xl flex items-center justify-center text-lg"
          style={{ background: `${color}15`, color }}
        >
          {icon}
        </div>
      </div>
    </div>
  );
}

function PriorityBar({ label, count, total, color }) {
  const pct = total > 0 ? (count / total) * 100 : 0;
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-[var(--text-secondary)] w-16 capitalize">
        {label}
      </span>
      <div className="flex-1 h-2 bg-[var(--bg-input)] rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
      <span className="text-xs text-[var(--text-muted)] w-8 text-right">
        {count}
      </span>
    </div>
  );
}

export default function DashboardPage() {
  const [stats, setStats] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState(null);

  const fetchData = useCallback(async (isManual = false) => {
    if (isManual) setRefreshing(true);
    try {
      const [statsRes, sessionsRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/dashboard/stats`),
        fetch(`${API_BASE}/api/v1/dashboard/sessions`),
      ]);
      const statsData = await statsRes.json();
      const sessionsData = await sessionsRes.json();
      setStats(statsData);
      setSessions(sessionsData.active_sessions || []);
      setLastRefreshed(new Date());
    } catch (err) {
      console.error("Failed to fetch dashboard data:", err);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    (async () => {
      await fetchData();
    })();
    const interval = setInterval(() => fetchData(), 15000);
    return () => clearInterval(interval);
  }, [fetchData]);

  if (loading) {
    return (
      <div className="p-6 space-y-6 page-enter">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {[...Array(4)].map((_, i) => <SkeletonCard key={i} />)}
        </div>
      </div>
    );
  }

  const priorityColors = {
    critical: "#ef4444",
    high: "#f59e0b",
    medium: "#3b82f6",
    low: "#22c55e",
  };

  const typeColors = {
    technical_issue: "#8b5cf6",
    billing: "#f59e0b",
    refund: "#ef4444",
    cancellation: "#f97316",
    inquiry: "#3b82f6",
  };

  return (
    <div className="p-6 space-y-6 page-enter">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold">Analytics Dashboard</h2>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            Real-time overview of your customer support operations
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastRefreshed && (
            <span className="text-[10px] text-[var(--text-muted)] font-mono">
              Updated {lastRefreshed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
            </span>
          )}
          <button
            onClick={() => fetchData(true)}
            disabled={refreshing}
            className="btn-secondary px-3 py-2 text-xs flex items-center gap-1.5"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className={`w-3.5 h-3.5 ${refreshing ? "animate-spin" : ""}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      {/* Stat Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Total Tickets"
          value={stats?.total_tickets}
          icon="🎫"
          color="#6366f1"
        />
        <StatCard
          title="Open Tickets"
          value={stats?.open_tickets}
          icon="📬"
          color="#3b82f6"
          subtext={`${stats?.total_tickets ? Math.round((stats.open_tickets / stats.total_tickets) * 100) : 0}% of total`}
        />
        <StatCard
          title="Escalated"
          value={stats?.escalated_tickets}
          icon="🚨"
          color="#ef4444"
        />
        <StatCard
          title="Avg Satisfaction"
          value={stats?.avg_satisfaction ? `${stats.avg_satisfaction}/5` : "N/A"}
          icon="⭐"
          color="#f59e0b"
        />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Priority Distribution */}
        <div className="glass-card p-6">
          <h3 className="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-4">
            Tickets by Priority
          </h3>
          <div className="space-y-3">
            {Object.entries(stats?.by_priority || {}).map(([label, count]) => (
              <PriorityBar
                key={label}
                label={label}
                count={count}
                total={stats?.total_tickets || 1}
                color={priorityColors[label] || "#6366f1"}
              />
            ))}
          </div>
        </div>

        {/* Type Distribution */}
        <div className="glass-card p-6">
          <h3 className="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-4">
            Tickets by Type
          </h3>
          <div className="space-y-3">
            {Object.entries(stats?.by_type || {}).map(([label, count]) => (
              <PriorityBar
                key={label}
                label={label.replace("_", " ")}
                count={count}
                total={stats?.total_tickets || 1}
                color={typeColors[label] || "#6366f1"}
              />
            ))}
          </div>
        </div>
      </div>

      {/* Status Distribution */}
      <div className="glass-card p-6">
        <h3 className="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-4">
          Tickets by Status
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {Object.entries(stats?.by_status || {}).map(([status, count]) => (
            <div
              key={status}
              className="bg-[var(--bg-input)] rounded-xl p-4 text-center hover:border-[var(--accent)]/30 border border-transparent transition-colors"
            >
              <p className="text-2xl font-bold text-[var(--text-primary)]">
                {count}
              </p>
              <p className="text-xs text-[var(--text-muted)] mt-1 capitalize">
                {status.replace("_", " ")}
              </p>
            </div>
          ))}
        </div>
      </div>

      {/* Active Sessions */}
      <div className="glass-card p-6">
        <h3 className="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-4">
          Active Chat Sessions
        </h3>
        {sessions.length === 0 ? (
          <EmptyState
            icon="💬"
            title="No active sessions"
            description="Live chat sessions will appear here when customers connect."
          />
        ) : (
          <div className="space-y-2">
            {sessions.map((s) => (
              <div
                key={s.session_id}
                className="flex items-center justify-between bg-[var(--bg-input)] rounded-xl px-4 py-3 hover:border-[var(--accent)]/20 border border-transparent transition-colors"
              >
                <div>
                  <p className="text-sm font-medium text-[var(--text-primary)]">
                    Session {s.session_id.slice(0, 8)}...
                  </p>
                  <p className="text-xs text-[var(--text-muted)] truncate max-w-[300px]">
                    {s.last_message || "No messages yet"}
                  </p>
                </div>
                <div className="flex items-center gap-3">
                  <span className="text-xs text-[var(--text-muted)]">
                    {s.message_count} msgs
                  </span>
                  <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
