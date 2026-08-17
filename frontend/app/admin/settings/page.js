"use client";

import { useState, useEffect } from "react";
import { SkeletonCard } from "../../components/LoadingSkeleton";
import { DynamicModelSettings } from "../../components/DynamicModelSettings";
import { useToast } from "../../components/Toast";
import { API_BASE } from "../../lib/api";

function ConfigRow({ label, description, value, valueColor, badge }) {
  return (
    <div className="flex items-center justify-between py-3 border-b border-[var(--border)]/50 last:border-b-0">
      <div className="pr-4">
        <p className="font-medium text-sm">{label}</p>
        {description && <p className="text-xs text-[var(--text-muted)] mt-0.5">{description}</p>}
      </div>
      {badge ? (
        <span className={`text-xs font-bold px-2.5 py-1 rounded-full ${
          badge === "enabled" || badge === "configured"
            ? "bg-emerald-500/20 text-emerald-400"
            : badge === "disabled" || badge === "not configured"
            ? "bg-gray-500/20 text-gray-400"
            : "bg-amber-500/20 text-amber-400"
        }`}>
          {badge.toUpperCase()}
        </span>
      ) : (
        <span className={`font-mono text-xs bg-[var(--bg-input)] px-3 py-1.5 rounded border border-[var(--border)] ${valueColor || ""}`}>
          {value || "—"}
        </span>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const [info, setInfo] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [infoRes, metricsRes] = await Promise.all([
          fetch(`${API_BASE}/api/v1/admin/system-info`),
          fetch(`${API_BASE}/api/v1/admin/metrics`).catch(() => null),
        ]);
        setInfo(await infoRes.json());
        if (metricsRes?.ok) setMetrics(await metricsRes.json());
      } catch (err) {
        console.error("Failed to fetch system info:", err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  if (loading) {
    return (
      <div className="p-8 max-w-4xl mx-auto space-y-6 page-enter">
        {[...Array(4)].map((_, i) => <SkeletonCard key={i} />)}
      </div>
    );
  }

  return (
    <div className="p-8 max-w-4xl mx-auto page-enter">
      <div className="mb-8">
        <h1 className="text-2xl font-bold">System Settings</h1>
        <p className="text-[var(--text-muted)] mt-1">
          View environment configuration and system parameters
        </p>
      </div>

      <div className="space-y-6">

        {/* App Info */}
        <div className="glass-card p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-4">Application</h2>
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-[var(--bg-input)] p-4 rounded-lg border border-[var(--border)]">
              <p className="text-xs text-[var(--text-muted)] mb-1">Version</p>
              <p className="font-mono text-lg font-bold text-[var(--accent)]">{info?.app_version}</p>
            </div>
            <div className="bg-[var(--bg-input)] p-4 rounded-lg border border-[var(--border)]">
              <p className="text-xs text-[var(--text-muted)] mb-1">Python</p>
              <p className="font-mono text-lg font-bold">{info?.python_version}</p>
            </div>
            <div className="bg-[var(--bg-input)] p-4 rounded-lg border border-[var(--border)] col-span-2">
              <p className="text-xs text-[var(--text-muted)] mb-1">Platform</p>
              <p className="font-mono text-sm truncate">{info?.platform}</p>
            </div>
          </div>
        </div>

        {/* LLM Config (Dynamic) */}
        <DynamicModelSettings />

        {/* Logging & Debug */}
        <div className="glass-card p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-4">
            Logging & Debug
          </h2>
          <div className="space-y-0">
            <ConfigRow
              label="Log Level"
              description="Controls verbosity of application logs"
              value={info?.log_level}
              valueColor={info?.log_level === "DEBUG" ? "text-gray-400" : info?.log_level === "WARNING" ? "text-amber-400" : "text-blue-400"}
            />
            <ConfigRow
              label="Debug Mode"
              description="Extra verbose output and development features"
              badge={info?.debug_mode ? "enabled" : "disabled"}
            />
          </div>
        </div>

        {/* Observability Config */}
        <div className="glass-card p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-4">Observability</h2>
          <div className="space-y-0">
            <ConfigRow
              label="LangSmith Tracing"
              description="Export LangGraph traces to LangSmith dashboard"
              badge={info?.langsmith_tracing === "true" ? "enabled" : "disabled"}
            />
            <ConfigRow
              label="LangSmith Project"
              value={info?.langsmith_project || "None"}
            />
          </div>
        </div>

        {/* Integrations */}
        <div className="glass-card p-6">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-4">Integrations</h2>
          <div className="space-y-0">
            <ConfigRow
              label="Supabase (PostgreSQL)"
              description="Primary database for customers, tickets, and orders"
              value={info?.supabase_url}
              valueColor="text-emerald-400"
            />
            <ConfigRow
              label="Pinecone (Vector Store)"
              description="RAG knowledge base vector embeddings"
              value={info?.pinecone_index_name || "knowledge-base"}
            />
            <ConfigRow
              label="Email Service (Gmail)"
              description="Customer & alert notifications"
              badge={info?.email_configured ? "configured" : "not configured"}
            />
            <ConfigRow
              label="API Server"
              description="FastAPI host and port"
              value={`${info?.api_host}:${info?.api_port}`}
            />
          </div>
        </div>

        {/* Runtime Metrics */}
        {metrics && (
          <div className="glass-card p-6">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-4">
              Runtime Metrics (Since Boot)
            </h2>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              <div className="bg-[var(--bg-input)] p-4 rounded-lg text-center border border-[var(--border)]">
                <p className="text-2xl font-bold text-[var(--accent)]">{metrics.llm?.total_calls || 0}</p>
                <p className="text-[10px] text-[var(--text-muted)] mt-1 uppercase">LLM Calls</p>
              </div>
              <div className="bg-[var(--bg-input)] p-4 rounded-lg text-center border border-[var(--border)]">
                <p className="text-2xl font-bold text-emerald-400">{metrics.llm?.avg_latency_ms || 0}ms</p>
                <p className="text-[10px] text-[var(--text-muted)] mt-1 uppercase">Avg LLM Latency</p>
              </div>
              <div className="bg-[var(--bg-input)] p-4 rounded-lg text-center border border-[var(--border)]">
                <p className="text-2xl font-bold text-amber-400">{metrics.tools?.total_calls || 0}</p>
                <p className="text-[10px] text-[var(--text-muted)] mt-1 uppercase">Tool Calls</p>
              </div>
              <div className="bg-[var(--bg-input)] p-4 rounded-lg text-center border border-[var(--border)]">
                <p className="text-2xl font-bold text-purple-400">{metrics.llm?.total_tokens_in || 0}</p>
                <p className="text-[10px] text-[var(--text-muted)] mt-1 uppercase">Tokens In</p>
              </div>
              <div className="bg-[var(--bg-input)] p-4 rounded-lg text-center border border-[var(--border)]">
                <p className="text-2xl font-bold text-blue-400">{metrics.llm?.total_tokens_out || 0}</p>
                <p className="text-[10px] text-[var(--text-muted)] mt-1 uppercase">Tokens Out</p>
              </div>
              <div className="bg-[var(--bg-input)] p-4 rounded-lg text-center border border-[var(--border)]">
                <p className="text-2xl font-bold text-red-400">{metrics.guardrails?.total_input_blocked || 0}</p>
                <p className="text-[10px] text-[var(--text-muted)] mt-1 uppercase">Inputs Blocked</p>
              </div>
            </div>
          </div>
        )}

      </div>
    </div>
  );
}
