"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { SkeletonTable } from "../../components/LoadingSkeleton";
import EmptyState from "../../components/EmptyState";
import { API_BASE } from "../../lib/api";

export default function CustomersPage() {
  const [customers, setCustomers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedCustomer, setSelectedCustomer] = useState(null);
  const [customerHistory, setCustomerHistory] = useState(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const debounceRef = useRef(null);

  const fetchCustomers = useCallback(async (query = "") => {
    setLoading(true);
    try {
      const url = query
        ? `${API_BASE}/api/v1/customers/search?q=${encodeURIComponent(query)}`
        : `${API_BASE}/api/v1/customers`;
      const res = await fetch(url);
      const data = await res.json();
      setCustomers(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error("Failed to fetch customers:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    (async () => {
      await fetchCustomers();
    })();
  }, [fetchCustomers]);

  // Debounced search as user types
  const handleSearchChange = (e) => {
    const val = e.target.value;
    setSearchQuery(val);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      fetchCustomers(val);
    }, 400);
  };

  const handleSearch = (e) => {
    e.preventDefault();
    if (debounceRef.current) clearTimeout(debounceRef.current);
    fetchCustomers(searchQuery);
  };

  const loadCustomerDetails = async (customer) => {
    setSelectedCustomer(customer);
    setLoadingHistory(true);
    setCustomerHistory(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/customers/${customer.id}/history`);
      const data = await res.json();
      setCustomerHistory(data);
    } catch (err) {
      console.error("Failed to fetch history:", err);
    } finally {
      setLoadingHistory(false);
    }
  };

  return (
    <div className="p-8 h-screen flex flex-col page-enter">
      <div className="mb-6 flex justify-between items-end">
        <div>
          <h1 className="text-2xl font-bold">Customers</h1>
          <p className="text-[var(--text-muted)] mt-1">
            {customers.length} customer{customers.length !== 1 ? "s" : ""}
            {searchQuery && ` matching "${searchQuery}"`}
          </p>
        </div>
        <form onSubmit={handleSearch} className="flex gap-2">
          <div className="relative">
            <input
              type="text"
              value={searchQuery}
              onChange={handleSearchChange}
              placeholder="Search name or email..."
              className="w-72 bg-[var(--bg-input)] border border-[var(--border)] rounded-lg px-4 py-2 text-sm focus:outline-none focus:border-[var(--accent)] pr-8"
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => { setSearchQuery(""); fetchCustomers(""); }}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[var(--text-muted)] hover:text-white text-xs"
              >
                ✕
              </button>
            )}
          </div>
          <button type="submit" className="btn-glow px-4 py-2 text-sm">
            Search
          </button>
        </form>
      </div>

      <div className="flex-1 flex gap-6 overflow-hidden pb-8">
        {/* Customer List */}
        <div className="w-1/2 glass-card overflow-hidden flex flex-col">
          <div className="bg-[#0f0f15] px-4 py-3 border-b border-[var(--border)] grid grid-cols-12 text-xs font-semibold text-[var(--text-secondary)] uppercase tracking-wider">
            <div className="col-span-4">Name</div>
            <div className="col-span-5">Email</div>
            <div className="col-span-3 text-right">Tickets</div>
          </div>

          <div className="flex-1 overflow-y-auto">
            {loading ? (
              <div className="p-4">
                <SkeletonTable rows={6} cols={3} />
              </div>
            ) : customers.length === 0 ? (
              <EmptyState
                icon="👥"
                title={searchQuery ? "No matches found" : "No customers yet"}
                description={searchQuery ? `No customers matching "${searchQuery}"` : "Customers will appear here."}
              />
            ) : (
              <div className="divide-y divide-[var(--border)]/50">
                {customers.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => loadCustomerDetails(c)}
                    className={`w-full text-left grid grid-cols-12 px-4 py-3 hover:bg-[var(--bg-input)] transition-colors ${
                      selectedCustomer?.id === c.id
                        ? "bg-[var(--bg-input)] border-l-2 border-[var(--accent)]"
                        : ""
                    }`}
                  >
                    <div className="col-span-4 flex items-center gap-2">
                      <div className="w-7 h-7 rounded-full bg-gradient-to-br from-[var(--gradient-start)] to-[var(--gradient-end)] flex items-center justify-center text-white font-bold text-[10px] shrink-0">
                        {c.name?.charAt(0)}
                      </div>
                      <span className="font-medium text-sm truncate">{c.name}</span>
                    </div>
                    <div className="col-span-5 text-sm text-[var(--text-muted)] truncate pr-2 flex items-center">
                      {c.email}
                    </div>
                    <div className="col-span-3 text-right text-xs flex items-center justify-end gap-2">
                      {c.open_tickets > 0 && (
                        <span className="bg-red-500/20 text-red-400 px-1.5 py-0.5 rounded">
                          {c.open_tickets} open
                        </span>
                      )}
                      <span className="text-[var(--text-secondary)]">{c.total_tickets || 0}</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Customer Detail Profile */}
        <div className="w-1/2 glass-card overflow-hidden flex flex-col">
          {selectedCustomer ? (
            <>
              <div className="p-6 border-b border-[var(--border)] bg-[#0f0f15]">
                <div className="flex items-center gap-4">
                  <div className="w-16 h-16 rounded-full bg-gradient-to-br from-[var(--gradient-start)] to-[var(--gradient-end)] flex items-center justify-center text-white text-xl font-bold shadow-lg shadow-[var(--accent-glow)]">
                    {selectedCustomer.name.charAt(0)}
                  </div>
                  <div>
                    <h2 className="text-xl font-bold">{selectedCustomer.name}</h2>
                    <p className="text-[var(--text-muted)]">{selectedCustomer.email}</p>
                    <div className="flex items-center gap-3 mt-2 text-xs">
                      <span className="bg-[var(--bg-input)] px-2 py-1 rounded border border-[var(--border)]">
                        ID: {selectedCustomer.id}
                      </span>
                      {selectedCustomer.created_at && (
                        <span className="bg-[var(--bg-input)] px-2 py-1 rounded border border-[var(--border)]">
                          Since {new Date(selectedCustomer.created_at).toLocaleDateString()}
                        </span>
                      )}
                      {selectedCustomer.gender && (
                        <span className="bg-[var(--bg-input)] px-2 py-1 rounded border border-[var(--border)] capitalize">
                          {selectedCustomer.gender}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              </div>

              <div className="flex-1 overflow-y-auto p-6 space-y-6">
                {loadingHistory ? (
                  <div className="flex justify-center p-8">
                    <div className="w-6 h-6 border-2 border-[var(--accent)] border-t-transparent rounded-full animate-spin"></div>
                  </div>
                ) : (
                  <>
                    {/* Quick Stats */}
                    {customerHistory && (
                      <div className="grid grid-cols-3 gap-3">
                        <div className="bg-[var(--bg-input)] p-3 rounded-lg text-center border border-[var(--border)]">
                          <p className="text-lg font-bold text-[var(--accent)]">{customerHistory.recent_tickets?.length || 0}</p>
                          <p className="text-[10px] text-[var(--text-muted)] uppercase">Tickets</p>
                        </div>
                        <div className="bg-[var(--bg-input)] p-3 rounded-lg text-center border border-[var(--border)]">
                          <p className="text-lg font-bold text-emerald-400">{customerHistory.recent_orders?.length || 0}</p>
                          <p className="text-[10px] text-[var(--text-muted)] uppercase">Orders</p>
                        </div>
                        <div className="bg-[var(--bg-input)] p-3 rounded-lg text-center border border-[var(--border)]">
                          <p className="text-lg font-bold text-amber-400">
                            {customerHistory.recent_tickets?.filter(t => t.satisfaction_rating)?.length > 0
                              ? `${(customerHistory.recent_tickets.filter(t => t.satisfaction_rating).reduce((a,t) => a + t.satisfaction_rating, 0) / customerHistory.recent_tickets.filter(t => t.satisfaction_rating).length).toFixed(1)}/5`
                              : "N/A"
                            }
                          </p>
                          <p className="text-[10px] text-[var(--text-muted)] uppercase">Avg Rating</p>
                        </div>
                      </div>
                    )}

                    {/* Tickets */}
                    <div>
                      <h3 className="text-sm font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-3">
                        Support Tickets
                      </h3>
                      {customerHistory?.recent_tickets?.length > 0 ? (
                        <div className="space-y-3">
                          {customerHistory.recent_tickets.map((t) => (
                            <div key={t.id} className="bg-[var(--bg-input)] border border-[var(--border)] rounded-xl p-4 hover:border-[var(--accent)]/30 transition-colors">
                              <div className="flex justify-between items-start mb-2">
                                <h4 className="font-medium text-sm">#{t.id} - {t.subject}</h4>
                                <span className={`badge badge-${t.status}`}>{t.status}</span>
                              </div>
                              <div className="flex justify-between items-center mt-3 text-xs text-[var(--text-muted)]">
                                <span className="uppercase">{t.type}</span>
                                <span>{new Date(t.created_at).toLocaleDateString()}</span>
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <EmptyState icon="🎫" title="No tickets" description="This customer hasn't created any support tickets." />
                      )}
                    </div>

                    {/* Orders */}
                    <div>
                      <h3 className="text-sm font-semibold uppercase tracking-wider text-[var(--text-secondary)] mb-3">
                        Recent Orders
                      </h3>
                      {customerHistory?.recent_orders?.length > 0 ? (
                        <div className="space-y-3">
                          {customerHistory.recent_orders.map((o) => (
                            <div key={o.id} className="bg-[var(--bg-input)] border border-[var(--border)] rounded-xl p-4 flex justify-between items-center hover:border-[var(--accent)]/30 transition-colors">
                              <div>
                                <p className="font-medium text-sm">Order #{o.id}</p>
                                <p className="text-xs text-[var(--text-muted)] mt-1">{o.products?.name}</p>
                              </div>
                              <div className="text-right">
                                <span className={`text-xs px-2 py-1 rounded-full border ${
                                  o.status === "delivered" ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30" :
                                  o.status === "cancelled" ? "bg-red-500/10 text-red-400 border-red-500/30" :
                                  "bg-blue-500/10 text-blue-400 border-blue-500/30"
                                }`}>
                                  {o.status}
                                </span>
                                <p className="text-xs text-[var(--text-muted)] mt-1">
                                  {new Date(o.order_date).toLocaleDateString()}
                                </p>
                              </div>
                            </div>
                          ))}
                        </div>
                      ) : (
                        <EmptyState icon="📦" title="No orders" description="This customer hasn't placed any orders." />
                      )}
                    </div>
                  </>
                )}
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center">
              <EmptyState
                icon="👤"
                title="Select a customer"
                description="Click on a customer to view their full profile and history."
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
