"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "../../lib/api";
import { adminFetch } from "../../lib/agent-auth";

const levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

export default function ActivityLogsPage() {
  const [level, setLevel] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState({ level: "", search: "" });
  const [result, setResult] = useState({ status: "loading", logs: [] });

  useEffect(() => {
    const controller = new AbortController();
    const params = new URLSearchParams({ limit: "100" });
    if (query.level) params.set("level", query.level);
    if (query.search) params.set("search", query.search);

    async function loadLogs() {
      try {
        const response = await adminFetch(`${API_BASE}/api/v1/admin/logs?${params}`, {
          signal: controller.signal,
          cache: "no-store",
        });
        if (!response.ok) {
          throw new Error(`Could not load activity logs (${response.status}).`);
        }
        const data = await response.json();
        if (!controller.signal.aborted) setResult({ status: "ready", logs: data.logs });
      } catch (error) {
        if (!controller.signal.aborted) {
          setResult({ status: "error", logs: [], error: error.message });
        }
      }
    }

    void loadLogs();
    return () => controller.abort();
  }, [query]);

  function applyFilters(event) {
    event.preventDefault();
    setResult({ status: "loading", logs: [] });
    setQuery({ level, search: search.trim() });
  }

  return (
    <div className="min-w-0 space-y-5 p-4 lg:p-8">
      <h1 className="text-2xl font-semibold text-[var(--text-primary)]">Activity Logs</h1>
      <form onSubmit={applyFilters} className="glass-card flex flex-wrap items-end gap-3 p-4">
        <div className="space-y-1 text-sm">
          <label htmlFor="logs-level" className="block">Level</label>
          <select id="logs-level" value={level} onChange={(event) => setLevel(event.target.value)}
            className="rounded-md border border-white/15 bg-[var(--bg-primary)] px-3 py-2">
            <option value="">All levels</option>
            {levels.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
        </div>
        <div className="min-w-48 flex-1 space-y-1 text-sm">
          <label htmlFor="logs-search" className="block">Search logs</label>
          <input id="logs-search" type="search" value={search} onChange={(event) => setSearch(event.target.value)}
            placeholder="Message, logger, or trace ID"
            className="w-full rounded-md border border-white/15 bg-[var(--bg-primary)] px-3 py-2" />
        </div>
        <button type="submit" className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500">
          Apply filters
        </button>
      </form>

      {result.status === "loading" && <p role="status">Loading activity logs…</p>}
      {result.status === "error" && <p role="alert" className="text-red-400">{result.error}</p>}
      {result.status === "ready" && (result.logs.length === 0 ? (
        <p className="text-[var(--text-muted)]">No matching log entries.</p>
      ) : (
        <div className="glass-card overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-white/10 text-xs text-[var(--text-muted)]">
              <tr>
                {["Time (UTC)", "Level", "Logger", "Message", "Trace ID"].map((column) => (
                  <th key={column} scope="col" className="px-4 py-3 font-medium">{column}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-white/10">
              {result.logs.map((entry, index) => (
                <tr key={`${entry.timestamp}-${index}`} className="align-top">
                  <td className="whitespace-nowrap px-4 py-3 font-mono text-xs">{entry.timestamp}</td>
                  <td className="px-4 py-3 font-medium">{entry.level}</td>
                  <td className="px-4 py-3">{entry.logger}</td>
                  <td className="min-w-48 whitespace-pre-wrap break-words px-4 py-3">{entry.message}</td>
                  <td className="px-4 py-3 font-mono text-xs">{entry.trace_id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  );
}
