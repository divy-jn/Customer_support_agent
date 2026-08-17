"use client";

import { useState, useEffect, useCallback } from "react";
import { SkeletonTable } from "../../components/LoadingSkeleton";
import EmptyState from "../../components/EmptyState";
import { useToast } from "../../components/Toast";
import { API_BASE } from "../../lib/api";

const STATUS_OPTIONS = ["open", "in_progress", "pending_customer", "escalated", "closed"];
const PRIORITY_OPTIONS = ["low", "medium", "high", "critical"];

function getBadgeClass(status) {
  const map = {
    open: "badge-open", closed: "badge-closed", pending_customer: "badge-pending",
    escalated: "badge-escalated", in_progress: "badge-in_progress",
    critical: "badge-critical", high: "badge-high", medium: "badge-medium", low: "badge-low",
  };
  return map[status] || "badge-medium";
}

export default function TicketsPage() {
  const [tickets, setTickets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchId, setSearchId] = useState("");
  const [selectedTicket, setSelectedTicket] = useState(null);
  const [filterStatus, setFilterStatus] = useState("");
  const [filterPriority, setFilterPriority] = useState("");
  const [updatingTicketId, setUpdatingTicketId] = useState(null);
  const { toast, ToastContainer } = useToast();

  const fetchTickets = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/tickets?limit=50`);
      if (res.ok) {
        const data = await res.json();
        setTickets(Array.isArray(data) ? data : (data.data || []));
      } else {
        console.error("Failed to fetch tickets:", res.status);
      }
    } catch (err) {
      console.error("Failed to fetch tickets:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    (async () => {
      await fetchTickets();
    })();
  }, [fetchTickets]);

  const searchTicket = async (e) => {
    e.preventDefault();
    if (!searchId.trim()) return;
    try {
      const res = await fetch(`${API_BASE}/api/v1/tickets/${searchId}`);
      if (res.ok) {
        const data = await res.json();
        setSelectedTicket(data);
      } else {
        toast(`Ticket #${searchId} not found`, "error");
      }
    } catch {
      toast("Failed to connect to server", "error");
    }
  };

  // Inline status update
  const updateTicketStatus = async (ticketId, newStatus) => {
    setUpdatingTicketId(ticketId);
    try {
      const res = await fetch(`${API_BASE}/api/v1/tickets/${ticketId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus }),
      });
      if (res.ok) {
        setTickets((prev) =>
          prev.map((t) => (t.id === ticketId ? { ...t, status: newStatus } : t))
        );
        if (selectedTicket?.id === ticketId) {
          setSelectedTicket((prev) => ({ ...prev, status: newStatus }));
        }
        toast(`Ticket #${ticketId} → ${newStatus.replace("_", " ")}`, "success");
      } else {
        toast("Failed to update ticket status", "error");
      }
    } catch {
      toast("Network error", "error");
    } finally {
      setUpdatingTicketId(null);
    }
  };

  // Filter tickets
  const filteredTickets = tickets.filter((t) => {
    if (filterStatus && t.status !== filterStatus) return false;
    if (filterPriority && t.priority !== filterPriority) return false;
    return true;
  });

  if (loading) {
    return (
      <div className="p-6 page-enter">
        <SkeletonTable rows={8} cols={6} />
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 page-enter">
      <ToastContainer />

      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-4">
        <div>
          <h2 className="text-2xl font-bold">Support Tickets</h2>
          <p className="text-sm text-[var(--text-muted)] mt-1">
            {filteredTickets.length} of {tickets.length} tickets shown
          </p>
        </div>
        <form onSubmit={searchTicket} className="flex gap-2">
          <input
            type="number"
            value={searchId}
            onChange={(e) => setSearchId(e.target.value)}
            placeholder="Ticket #"
            className="bg-[var(--bg-input)] border border-[var(--border)] rounded-xl px-4 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--border-glow)] w-32"
          />
          <button type="submit" className="btn-glow px-4 py-2 text-sm">
            Search
          </button>
        </form>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <span className="text-xs text-[var(--text-muted)] uppercase tracking-wider font-medium">
          Filters:
        </span>
        <select
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
          className="bg-[var(--bg-input)] border border-[var(--border)] rounded-lg px-3 py-1.5 text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]"
        >
          <option value="">All Statuses</option>
          {STATUS_OPTIONS.map((s) => (
            <option key={s} value={s}>{s.replace("_", " ")}</option>
          ))}
        </select>
        <select
          value={filterPriority}
          onChange={(e) => setFilterPriority(e.target.value)}
          className="bg-[var(--bg-input)] border border-[var(--border)] rounded-lg px-3 py-1.5 text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent)]"
        >
          <option value="">All Priorities</option>
          {PRIORITY_OPTIONS.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
        {(filterStatus || filterPriority) && (
          <button
            onClick={() => { setFilterStatus(""); setFilterPriority(""); }}
            className="text-xs text-[var(--accent)] hover:underline"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Selected Ticket Detail */}
      {selectedTicket && (
        <div className="glass-card p-6 animate-fade-in">
          {selectedTicket.error ? (
            <p className="text-sm text-red-400">{selectedTicket.error}</p>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-lg font-semibold">Ticket #{selectedTicket.id}</h3>
                <button
                  onClick={() => setSelectedTicket(null)}
                  className="text-[var(--text-muted)] hover:text-[var(--text-primary)] text-sm"
                >
                  ✕ Close
                </button>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div>
                  <p className="text-[var(--text-muted)] text-xs">Customer</p>
                  <p className="font-medium">{selectedTicket.customers?.name || `ID: ${selectedTicket.customer_id}`}</p>
                </div>
                <div>
                  <p className="text-[var(--text-muted)] text-xs mb-1">Status</p>
                  <select
                    value={selectedTicket.status}
                    onChange={(e) => updateTicketStatus(selectedTicket.id, e.target.value)}
                    className={`text-xs px-2 py-1 rounded font-bold bg-[var(--bg-input)] border border-[var(--border)] focus:outline-none focus:border-[var(--accent)] ${
                      updatingTicketId === selectedTicket.id ? "opacity-50" : ""
                    }`}
                  >
                    {STATUS_OPTIONS.map((s) => (
                      <option key={s} value={s}>{s.replace("_", " ")}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <p className="text-[var(--text-muted)] text-xs">Priority</p>
                  <span className={`badge ${getBadgeClass(selectedTicket.priority)}`}>{selectedTicket.priority}</span>
                </div>
                <div>
                  <p className="text-[var(--text-muted)] text-xs">Type</p>
                  <p className="font-medium capitalize">{selectedTicket.type?.replace("_", " ")}</p>
                </div>
              </div>
              <div>
                <p className="text-[var(--text-muted)] text-xs mb-1">Subject</p>
                <p className="text-sm">{selectedTicket.subject}</p>
              </div>
              <div>
                <p className="text-[var(--text-muted)] text-xs mb-1">Description</p>
                <p className="text-sm text-[var(--text-secondary)]">{selectedTicket.description}</p>
              </div>
              {selectedTicket.resolution && (
                <div>
                  <p className="text-[var(--text-muted)] text-xs mb-1">Resolution</p>
                  <p className="text-sm text-emerald-400">{selectedTicket.resolution}</p>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Tickets Table */}
      {filteredTickets.length === 0 ? (
        <EmptyState
          icon="🎫"
          title="No tickets match your filters"
          description="Try adjusting your filter criteria."
          action={{ label: "Clear Filters", onClick: () => { setFilterStatus(""); setFilterPriority(""); } }}
        />
      ) : (
        <div className="glass-card overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-[var(--border)]">
                  <th className="text-left text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider px-5 py-3">ID</th>
                  <th className="text-left text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider px-5 py-3">Subject</th>
                  <th className="text-left text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider px-5 py-3">Type</th>
                  <th className="text-left text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider px-5 py-3">Status</th>
                  <th className="text-left text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider px-5 py-3">Priority</th>
                  <th className="text-left text-xs font-medium text-[var(--text-muted)] uppercase tracking-wider px-5 py-3">Rating</th>
                </tr>
              </thead>
              <tbody>
                {filteredTickets.map((ticket, i) => (
                  <tr
                    key={ticket.id || i}
                    onClick={() => setSelectedTicket(ticket)}
                    className="border-b border-[var(--border)]/50 cursor-pointer hover:bg-[var(--bg-input)] transition-colors"
                  >
                    <td className="px-5 py-3 text-sm font-mono text-[var(--accent-hover)]">#{ticket.id}</td>
                    <td className="px-5 py-3 text-sm max-w-[250px] truncate">{ticket.subject}</td>
                    <td className="px-5 py-3 text-sm capitalize text-[var(--text-secondary)]">{ticket.type?.replace("_", " ")}</td>
                    <td className="px-5 py-3">
                      <select
                        value={ticket.status}
                        onChange={(e) => { e.stopPropagation(); updateTicketStatus(ticket.id, e.target.value); }}
                        onClick={(e) => e.stopPropagation()}
                        className={`badge ${getBadgeClass(ticket.status)} bg-transparent border-none cursor-pointer focus:outline-none`}
                      >
                        {STATUS_OPTIONS.map((s) => (
                          <option key={s} value={s}>{s.replace("_", " ")}</option>
                        ))}
                      </select>
                    </td>
                    <td className="px-5 py-3">
                      <span className={`badge ${getBadgeClass(ticket.priority)}`}>{ticket.priority}</span>
                    </td>
                    <td className="px-5 py-3 text-sm text-[var(--text-muted)]">
                      {ticket.satisfaction_rating
                        ? `${"★".repeat(ticket.satisfaction_rating)}${"☆".repeat(5 - ticket.satisfaction_rating)}`
                        : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
