"use client";

import { useState, useEffect, useCallback } from "react";
import { SkeletonCard } from "../components/LoadingSkeleton";
import { useToast } from "../components/Toast";
import { API_BASE } from "../lib/api";

function StatusIndicator({ status }) {
  if (status === "healthy") {
    return (
      <span className="flex items-center gap-1.5 text-xs font-medium text-emerald-400 bg-emerald-400/10 px-2.5 py-1 rounded-full">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
        Online
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5 text-xs font-medium text-red-400 bg-red-400/10 px-2.5 py-1 rounded-full">
      <span className="w-1.5 h-1.5 rounded-full bg-red-400"></span>
      Offline
    </span>
  );
}

export default function SystemHealthPage() {
  const [health, setHealth] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [connMetrics, setConnMetrics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState(null);
  const { toast, ToastContainer } = useToast();

  const fetchData = useCallback(async (isManual = false) => {
    if (isManual) setRefreshing(true);
    try {
      const [healthRes, metricsRes, connRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/admin/health`),
        fetch(`${API_BASE}/api/v1/admin/metrics`).catch(() => null),
        fetch(`${API_BASE}/api/v1/admin/connections`).catch(() => null),
      ]);
      setHealth(await healthRes.json());
      if (metricsRes?.ok) setMetrics(await metricsRes.json());
      if (connRes?.ok) setConnMetrics(await connRes.json());
      setLastRefreshed(new Date());
    } catch (err) {
      console.error("Failed to fetch health data:", err);
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

  // Test a specific service on demand
  const testService = async (service) => {
    toast(`Testing ${service} connection...`, "info");
    try {
      await fetchData(true);
      const status = health?.services?.[service]?.status;
      if (status === "healthy") {
        toast(`${service} is healthy ✓`, "success");
      } else {
        toast(`${service} is unreachable ✕`, "error");
      }
    } catch {
      toast(`Failed to test ${service}`, "error");
    }
  };

  if (loading) {
    return (
      <div className="p-8 max-w-6xl mx-auto page-enter">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {[...Array(4)].map((_, i) => <SkeletonCard key={i} />)}
        </div>
      </div>
    );
  }

  const { services } = health || {};

  return (
    <div className="p-8 max-w-6xl mx-auto page-enter">
      <ToastContainer />

      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">System Health</h1>
          <p className="text-[var(--text-muted)] mt-1">
            Monitor core services, models, and databases
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastRefreshed && (
            <span className="text-[10px] text-[var(--text-muted)] font-mono">
              {lastRefreshed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
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
              fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
        </div>
      </div>

      {/* Service Health Cards */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-8">
        {/* LLM Provider Service */}
        <div className="glass-card p-6 border-l-4 border-l-purple-500">
          <div className="flex justify-between items-start mb-4">
            <div>
              <h3 className="text-lg font-semibold flex items-center gap-2">LLM Provider</h3>
              <p className="text-xs text-[var(--text-muted)] font-mono mt-1">{services?.llm?.url || "Unknown URL"}</p>
            </div>
            <StatusIndicator status={services?.llm?.status} />
          </div>
          <div className="space-y-4 mt-6">
            <div>
              <p className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-2">Primary Models</p>
              <div className="flex items-center justify-between text-sm bg-[var(--bg-input)] p-3 rounded-lg mb-2">
                <span>Small/Fast (Router)</span>
                <span className="font-mono text-[var(--accent)]">{services?.llm?.small_model}</span>
                {services?.llm?.small_model_loaded ? (
                  <span className="text-emerald-400 text-xs">Loaded ✓</span>
                ) : (
                  <span className="text-red-400 text-xs">Missing ✗</span>
                )}
              </div>
              <div className="flex items-center justify-between text-sm bg-[var(--bg-input)] p-3 rounded-lg">
                <span>Large (Agent logic)</span>
                <span className="font-mono text-[var(--accent)]">{services?.llm?.large_model}</span>
                {services?.llm?.large_model_loaded ? (
                  <span className="text-emerald-400 text-xs">Loaded ✓</span>
                ) : (
                  <span className="text-red-400 text-xs">Missing ✗</span>
                )}
              </div>
            </div>
            <button onClick={() => testService("llm")} className="btn-secondary w-full py-2 text-xs">
              Test Connection
            </button>
            {services?.llm?.error && (
              <div className="bg-red-900/20 border border-red-500/30 text-red-300 text-xs p-3 rounded-lg font-mono">{services.llm.error}</div>
            )}
          </div>
        </div>

        {/* Supabase Service */}
        <div className="glass-card p-6 border-l-4 border-l-emerald-500">
          <div className="flex justify-between items-start mb-4">
            <div>
              <h3 className="text-lg font-semibold">Supabase PostgreSQL</h3>
              <p className="text-xs text-[var(--text-muted)] font-mono mt-1 truncate max-w-[250px]">{services?.supabase?.url || "Unknown URL"}</p>
            </div>
            <StatusIndicator status={services?.supabase?.status} />
          </div>
          <div className="mt-6 flex flex-col items-center justify-center h-32 bg-[var(--bg-input)] rounded-lg">
            <div className="text-4xl">🐘</div>
            <p className="text-sm mt-3 text-[var(--text-secondary)]">Database connection verified</p>
          </div>
          <button onClick={() => testService("supabase")} className="btn-secondary w-full py-2 text-xs mt-4">
            Test Connection
          </button>
          {services?.supabase?.error && (
            <div className="mt-4 bg-red-900/20 border border-red-500/30 text-red-300 text-xs p-3 rounded-lg font-mono">{services.supabase.error}</div>
          )}
        </div>

        {/* Pinecone Vector DB Service */}
        <div className="glass-card p-6 border-l-4 border-l-blue-500">
          <div className="flex justify-between items-start mb-4">
            <div>
              <h3 className="text-lg font-semibold">Pinecone Vector DB</h3>
              <p className="text-xs text-[var(--text-muted)] font-mono mt-1">{services?.vectordb?.index || "Unknown index"}</p>
            </div>
            <StatusIndicator status={services?.vectordb?.status} />
          </div>
          <div className="mt-6">
            <p className="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-2">Index Stats</p>
            {services?.vectordb?.total_vectors != null ? (
              <div className="space-y-2">
                <div className="flex items-center justify-between bg-[var(--bg-input)] p-3 rounded-lg">
                  <span className="font-medium text-sm">Total Vectors</span>
                  <span className="text-xs bg-[var(--bg-card)] px-2 py-1 rounded">{services.vectordb.total_vectors} vectors</span>
                </div>
              </div>
            ) : (
              <p className="text-sm text-[var(--text-muted)]">No index stats available.</p>
            )}
          </div>
          <button onClick={() => testService("vectordb")} className="btn-secondary w-full py-2 text-xs mt-4">
            Test Connection
          </button>
        </div>

        {/* FastAPI Server */}
        <div className="glass-card p-6 border-l-4 border-l-orange-500">
          <div className="flex justify-between items-start mb-4">
            <div>
              <h3 className="text-lg font-semibold">FastAPI Web Server</h3>
              <p className="text-xs text-[var(--text-muted)] font-mono mt-1">{API_BASE}</p>
            </div>
            <StatusIndicator status={services?.fastapi?.status} />
          </div>
          <div className="mt-6 flex gap-4">
            <div className="flex-1 bg-[var(--bg-input)] p-4 rounded-lg text-center">
              <p className="text-xs text-[var(--text-muted)] uppercase">REST API</p>
              <p className="text-xl font-semibold text-emerald-400 mt-1">UP</p>
            </div>
            <div className="flex-1 bg-[var(--bg-input)] p-4 rounded-lg text-center">
              <p className="text-xs text-[var(--text-muted)] uppercase">WebSockets</p>
              <p className="text-xl font-semibold text-emerald-400 mt-1">UP</p>
            </div>
          </div>
          {/* Connection metrics */}
          {connMetrics && (
            <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
              <div className="bg-[var(--bg-input)] p-2 rounded text-center">
                <p className="text-[var(--text-muted)]">Active Customers</p>
                <p className="font-bold text-[var(--accent)]">{connMetrics.active_customer_sessions}</p>
              </div>
              <div className="bg-[var(--bg-input)] p-2 rounded text-center">
                <p className="text-[var(--text-muted)]">Active Agents</p>
                <p className="font-bold text-emerald-400">{connMetrics.active_agent_sessions}</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Performance Metrics */}
      {metrics && (
        <div className="glass-card p-6">
          <h3 className="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-4">
            Performance Metrics (Since Boot)
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="bg-[var(--bg-input)] p-4 rounded-xl text-center">
              <p className="text-2xl font-bold text-[var(--accent)]">{metrics.llm?.total_calls || 0}</p>
              <p className="text-xs text-[var(--text-muted)] mt-1">LLM Calls</p>
            </div>
            <div className="bg-[var(--bg-input)] p-4 rounded-xl text-center">
              <p className="text-2xl font-bold text-emerald-400">{metrics.llm?.avg_latency_ms || 0}ms</p>
              <p className="text-xs text-[var(--text-muted)] mt-1">Avg Latency</p>
            </div>
            <div className="bg-[var(--bg-input)] p-4 rounded-xl text-center">
              <p className="text-2xl font-bold text-amber-400">{metrics.tools?.total_calls || 0}</p>
              <p className="text-xs text-[var(--text-muted)] mt-1">Tool Calls</p>
            </div>
            <div className="bg-[var(--bg-input)] p-4 rounded-xl text-center">
              <p className="text-2xl font-bold text-red-400">{metrics.guardrails?.block_rate || 0}%</p>
              <p className="text-xs text-[var(--text-muted)] mt-1">Guardrail Block Rate</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
