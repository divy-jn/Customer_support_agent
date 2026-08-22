"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useToast } from "./components/Toast";
import { API_BASE, WS_BASE } from "./lib/api";

export default function CustomerChatPage() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  // Lazy initializers — restore from localStorage without a useEffect
  const [sessionId, setSessionId] = useState(() => {
    if (typeof window === "undefined") return null;
    return localStorage.getItem("intellisupport_session_id") || null;
  });
  const [isConnected, setIsConnected] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [reconnectCountdown, setReconnectCountdown] = useState(0);
  const typingTimeoutRef = useRef(null);

  // Customer Identity
  const [customerEmail, setCustomerEmail] = useState("");
  const [identifiedCustomer, setIdentifiedCustomer] = useState(() => {
    if (typeof window === "undefined") return null;
    try {
      const saved = localStorage.getItem("intellisupport_customer");
      return saved ? JSON.parse(saved) : null;
    } catch { return null; }
  });
  const [identifying, setIdentifying] = useState(false);
  const [showIdentityModal, setShowIdentityModal] = useState(() => {
    if (typeof window === "undefined") return true;
    return !localStorage.getItem("intellisupport_customer");
  });

  // Drawer
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [customerHistory, setCustomerHistory] = useState(null);

  // Auto-scroll
  const [autoScroll, setAutoScroll] = useState(true);

  const wsRef = useRef(null);
  const messagesEndRef = useRef(null);
  const messagesContainerRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  const inputRef = useRef(null);
  const connectWsRef = useRef(null);

  const { toast, ToastContainer } = useToast();

  // ── Auto-scroll with lock ──
  const scrollToBottom = () => {
    if (autoScroll) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  };
  useEffect(scrollToBottom, [messages, isTyping, autoScroll]);

  const handleScroll = () => {
    const el = messagesContainerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
    setAutoScroll(atBottom);
  };

  // Session persistence — restore handled by lazy useState initializers above.

  useEffect(() => {
    if (sessionId) localStorage.setItem("intellisupport_session_id", sessionId);
  }, [sessionId]);

  // ── WebSocket ──
  const connectWebSocket = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState <= 1) return;

    const savedSession = localStorage.getItem("intellisupport_session_id");
    const wsUrl = savedSession
      ? `${WS_BASE}/ws/chat/${savedSession}`
      : `${WS_BASE}/ws/chat`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      setReconnecting(false);
      setReconnectCountdown(0);
      if (reconnectTimerRef.current) {
        clearInterval(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "ping") return;

      if (data.type === "system") {
        if (data.session_id) setSessionId(data.session_id);
        setMessages((prev) => {
          // Prevent duplicate welcome messages on reconnect
          if (prev.some(m => m.role === "system" && m.content === data.message)) {
            return prev;
          }
          return [
            ...prev,
            { role: "system", content: data.message, timestamp: data.timestamp },
          ];
        });
        setIsTyping(false);
      } else if (data.type === "typing") {
        setIsTyping(true);
        // Auto-clear typing indicator after 30s as a safety net
        if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current);
        typingTimeoutRef.current = setTimeout(() => setIsTyping(false), 30000);
      } else if (data.type === "agent_response") {
        setIsTyping(false);
        if (typingTimeoutRef.current) { clearTimeout(typingTimeoutRef.current); typingTimeoutRef.current = null; }
        setMessages((prev) => [
          ...prev,
          {
            role: "agent",
            content: data.message,
            agent_name: data.agent_name || "Adi",
            escalated: data.escalated,
            timestamp: data.timestamp,
            status: "delivered",
          },
        ]);
      } else if (data.type === "error") {
        setIsTyping(false);
        if (typingTimeoutRef.current) { clearTimeout(typingTimeoutRef.current); typingTimeoutRef.current = null; }
        setMessages((prev) => [
          ...prev,
          { role: "error", content: data.message, timestamp: data.timestamp },
        ]);
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      setIsTyping(false);
      if (typingTimeoutRef.current) { clearTimeout(typingTimeoutRef.current); typingTimeoutRef.current = null; }
      setReconnecting(true);
      let countdown = 3;
      setReconnectCountdown(countdown);
      reconnectTimerRef.current = setInterval(() => {
        countdown--;
        setReconnectCountdown(countdown);
        if (countdown <= 0) {
          clearInterval(reconnectTimerRef.current);
          reconnectTimerRef.current = null;
          connectWsRef.current?.();
        }
      }, 1000);
    };

    ws.onerror = () => ws.close();
  }, []);

  // Keep ref in sync so onclose always calls the latest connectWebSocket
  useEffect(() => {
    connectWsRef.current = connectWebSocket;
  }, [connectWebSocket]);

  useEffect(() => {
    connectWebSocket();
    return () => {
      if (reconnectTimerRef.current) clearInterval(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [connectWebSocket]);

  // ── Customer Identification ──
  const handleIdentify = async (e) => {
    e.preventDefault();
    if (!customerEmail.trim()) return;
    setIdentifying(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/customers/search?q=${encodeURIComponent(customerEmail)}`
      );
      const data = await res.json();
      if (Array.isArray(data) && data.length > 0) {
        setIdentifiedCustomer(data[0]);
        setShowIdentityModal(false);
        localStorage.setItem("intellisupport_customer", JSON.stringify(data[0]));
        toast(`Welcome back, ${data[0].name}! 🎉`, "success");
        if (wsRef.current && wsRef.current.readyState === 1) {
          wsRef.current.send(
            JSON.stringify({
              message: `[System] Customer identified as ${data[0].name} (${data[0].email})`,
              customer_id: data[0].id,
            })
          );
        }
      } else {
        toast("No account found with that email. Try another or continue as guest.", "warning");
      }
    } catch (err) {
      toast("Connection error. Please try again.", "error");
    } finally {
      setIdentifying(false);
    }
  };

  const continueAsGuest = () => {
    setShowIdentityModal(false);
    setIdentifiedCustomer(null);
    inputRef.current?.focus();
  };

  const loadHistory = async () => {
    if (!identifiedCustomer) return;
    try {
      const res = await fetch(`${API_BASE}/api/v1/customers/${identifiedCustomer.id}/history`);
      const data = await res.json();
      setCustomerHistory(data);
    } catch (err) {
      console.error(err);
    }
  };

  const toggleDrawer = () => {
    setDrawerOpen(!drawerOpen);
    if (!drawerOpen && identifiedCustomer && !customerHistory) loadHistory();
  };

  // ── Send Message ──
  const sendMessage = (e) => {
    e.preventDefault();
    if (!input.trim() || !wsRef.current || wsRef.current.readyState !== 1) return;
    const msg = input.trim();
    setMessages((prev) => [
      ...prev,
      { role: "customer", content: msg, timestamp: new Date().toISOString(), status: "sent" },
    ]);
    wsRef.current.send(
      JSON.stringify({ message: msg, customer_id: identifiedCustomer?.id })
    );
    setInput("");
    setAutoScroll(true);
  };

  // ── Markdown ──
  const renderMarkdown = (text) => {
    if (!text) return text;
    let html = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/`([^`]+)`/g, '<code style="background:rgba(99,102,241,0.15);padding:2px 6px;border-radius:4px;font-size:12px;color:#a5b4fc;">$1</code>');
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" style="color:#818cf8;text-decoration:underline;">$1</a>');
    html = html.replace(/^[-•]\s+(.*)$/gm, '<div style="display:flex;gap:8px;margin:2px 0;"><span style="color:#818cf8;">•</span><span>$1</span></div>');
    html = html.replace(/^(\d+)\.\s+(.*)$/gm, '<div style="display:flex;gap:8px;margin:2px 0;"><span style="color:#818cf8;font-weight:600;">$1.</span><span>$2</span></div>');
    return html;
  };

  // Quick suggestion chips
  const suggestions = [
    "Track my order",
    "I need help with a product",
    "Check my ticket status",
    "Talk to a human agent",
  ];

  const sendSuggestion = (text) => {
    if (!wsRef.current || wsRef.current.readyState !== 1) return;
    setMessages((prev) => [
      ...prev,
      { role: "customer", content: text, timestamp: new Date().toISOString(), status: "sent" },
    ]);
    wsRef.current.send(JSON.stringify({ message: text, customer_id: identifiedCustomer?.id }));
    setAutoScroll(true);
  };

  const hasMessages = messages.filter((m) => m.role !== "system").length > 0;

  return (
    <div className="h-screen flex bg-[var(--bg-primary)] overflow-hidden">
      <ToastContainer />

      {/* ══════════════════════════════════════════
          FULL-SCREEN IDENTITY MODAL
          ══════════════════════════════════════════ */}
      {showIdentityModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
          <div className="w-full max-w-md mx-4 animate-fade-in">
            {/* Floating card */}
            <div className="glass-card p-8 shadow-2xl shadow-indigo-500/10 relative overflow-hidden">
              {/* Decorative gradient orb */}
              <div className="absolute -top-20 -right-20 w-40 h-40 bg-gradient-to-br from-indigo-500/20 to-purple-500/20 rounded-full blur-3xl pointer-events-none" />
              <div className="absolute -bottom-20 -left-20 w-40 h-40 bg-gradient-to-br from-blue-500/10 to-cyan-500/10 rounded-full blur-3xl pointer-events-none" />

              {/* Logo */}
              <div className="flex justify-center mb-6">
                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-2xl font-black shadow-lg shadow-indigo-500/30">
                  AI
                </div>
              </div>

              <h2 className="text-xl font-bold text-center mb-1">
                Welcome to <span className="bg-gradient-to-r from-indigo-400 to-purple-400 bg-clip-text text-transparent">Project Bestie</span>
              </h2>
              <p className="text-sm text-[var(--text-muted)] text-center mb-6">
                Get instant AI-powered support for orders, tickets, and more.
              </p>

              {/* Identify form */}
              <form onSubmit={handleIdentify} className="space-y-3">
                <div>
                  <label className="text-xs text-[var(--text-secondary)] font-medium mb-1.5 block">
                    Link your account (optional)
                  </label>
                  <input
                    type="email"
                    value={customerEmail}
                    onChange={(e) => setCustomerEmail(e.target.value)}
                    placeholder="Enter your email address"
                    className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500/50 transition-all placeholder:text-[var(--text-muted)]"
                  />
                </div>
                <button
                  type="submit"
                  disabled={identifying || !customerEmail.trim()}
                  className="w-full py-3 rounded-xl bg-gradient-to-r from-indigo-600 to-purple-600 text-white font-semibold text-sm hover:from-indigo-500 hover:to-purple-500 transition-all disabled:opacity-40 disabled:cursor-not-allowed shadow-lg shadow-indigo-500/20 hover:shadow-indigo-500/40 flex items-center justify-center gap-2"
                >
                  {identifying ? (
                    <>
                      <span className="w-4 h-4 border-2 border-white/40 border-t-white rounded-full animate-spin" />
                      Looking up...
                    </>
                  ) : (
                    <>
                      <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 20 20" fill="currentColor">
                        <path fillRule="evenodd" d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z" clipRule="evenodd" />
                      </svg>
                      Link My Account
                    </>
                  )}
                </button>
              </form>

              <div className="flex items-center gap-3 my-5">
                <div className="flex-1 border-t border-[var(--border)]"></div>
                <span className="text-[10px] text-[var(--text-muted)] uppercase tracking-widest">or</span>
                <div className="flex-1 border-t border-[var(--border)]"></div>
              </div>

              <button
                onClick={continueAsGuest}
                className="w-full py-3 rounded-xl bg-[var(--bg-input)] border border-[var(--border)] text-[var(--text-secondary)] font-medium text-sm hover:border-indigo-500/50 hover:text-white transition-all"
              >
                Continue as Guest →
              </button>

              <p className="text-[10px] text-[var(--text-muted)] text-center mt-5 leading-relaxed">
                By continuing you agree to our terms of service. Your conversations may be recorded for quality assurance.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ══════════════════════════════════════════
          MAIN CHAT AREA
          ══════════════════════════════════════════ */}
      <div className="flex-1 flex flex-col relative">

        {/* Reconnecting Banner */}
        {reconnecting && (
          <div className="reconnect-banner z-30">
            ⚠ Connection lost. Reconnecting in {reconnectCountdown}s…
          </div>
        )}

        {/* Header */}
        <header className="border-b border-[var(--border)] px-6 py-3.5 flex items-center justify-between bg-[var(--bg-secondary)]/90 backdrop-blur-xl z-10 shrink-0">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white font-black text-sm shrink-0 shadow-lg shadow-indigo-500/20">
              AI
            </div>
            <div>
              <h2 className="text-sm font-bold text-[var(--text-primary)]">
                Project Bestie
              </h2>
              <div className="flex items-center gap-1.5 text-[11px]">
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    isConnected
                      ? "bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.8)]"
                      : "bg-red-400"
                  }`}
                />
                <span className="text-[var(--text-muted)]">
                  {isConnected ? "Online — typically replies instantly" : reconnecting ? "Reconnecting…" : "Offline"}
                </span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {identifiedCustomer && (
              <button
                onClick={toggleDrawer}
                className="flex items-center gap-2 px-3 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border)] hover:border-indigo-500/50 transition-all text-xs group"
              >
                <div className="w-6 h-6 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-500 flex items-center justify-center text-white font-bold text-[10px] shrink-0">
                  {identifiedCustomer.name.charAt(0)}
                </div>
                <span className="text-[var(--text-secondary)] group-hover:text-white transition-colors hidden sm:inline">
                  {identifiedCustomer.name}
                </span>
              </button>
            )}
            {!identifiedCustomer && !showIdentityModal && (
              <button
                onClick={() => setShowIdentityModal(true)}
                className="text-[11px] text-[var(--text-muted)] hover:text-indigo-400 transition-colors px-3 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border)] hover:border-indigo-500/30"
              >
                Link Account
              </button>
            )}
          </div>
        </header>

        {/* Messages */}
        <div
          ref={messagesContainerRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto px-4 sm:px-6 py-6 relative"
        >
          <div className="max-w-3xl mx-auto space-y-4">

            {/* Welcome state when no real messages */}
            {!hasMessages && !showIdentityModal && (
              <div className="flex flex-col items-center justify-center pt-16 pb-8 animate-fade-in">
                <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-indigo-500/10 to-purple-500/10 border border-indigo-500/20 flex items-center justify-center mb-6">
                  <svg xmlns="http://www.w3.org/2000/svg" className="w-10 h-10 text-indigo-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H8.25m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H12m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 01-2.555-.337A5.972 5.972 0 015.41 20.97a5.969 5.969 0 01-.474-.065 4.48 4.48 0 00.978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25z" />
                  </svg>
                </div>
                <h3 className="text-lg font-semibold text-[var(--text-primary)] mb-2">
                  How can I help you today?
                </h3>
                <p className="text-sm text-[var(--text-muted)] text-center max-w-sm mb-8">
                  I can help you track orders, resolve issues, check ticket status, and much more.
                </p>

                {/* Suggestion chips */}
                <div className="flex flex-wrap justify-center gap-2 max-w-md">
                  {suggestions.map((s, i) => (
                    <button
                      key={i}
                      onClick={() => sendSuggestion(s)}
                      className="px-4 py-2.5 rounded-xl bg-[var(--bg-card)] border border-[var(--border)] text-sm text-[var(--text-secondary)] hover:border-indigo-500/50 hover:text-white hover:bg-[var(--bg-input)] transition-all hover:shadow-md hover:shadow-indigo-500/5"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Message bubbles */}
            {messages.map((msg, i) => (
              <div
                key={i}
                className={`flex ${
                  msg.role === "customer" ? "justify-end" : "justify-start"
                } ${
                  msg.role === "customer" ? "chat-bubble-customer" : "chat-bubble-agent"
                }`}
              >
                {/* Agent avatar */}
                {msg.role === "agent" && (
                  <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-[10px] font-bold mr-2 mt-1 shrink-0 shadow-md shadow-indigo-500/20">
                    AI
                  </div>
                )}

                <div
                  className={`max-w-[80%] sm:max-w-[70%] rounded-2xl px-4 py-3 ${
                    msg.role === "customer"
                      ? "bg-gradient-to-br from-indigo-600 to-purple-600 text-white rounded-br-md shadow-lg shadow-indigo-500/20"
                      : msg.role === "system"
                      ? "bg-[var(--bg-card)]/50 text-[var(--text-muted)] text-center text-[11px] mx-auto border border-[var(--border)]/50 rounded-full px-4 py-1.5"
                      : msg.role === "error"
                      ? "bg-red-950/30 border border-red-500/20 text-red-300 text-sm"
                      : "bg-[var(--bg-card)] border border-[var(--border)] text-[var(--text-primary)] rounded-bl-md"
                  }`}
                >
                  {/* Escalation badge */}
                  {msg.role === "agent" && msg.escalated && (
                    <div className="flex items-center gap-1.5 mb-2 pb-1.5 border-b border-amber-500/20">
                      <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                      <span className="text-[10px] text-amber-400 font-semibold uppercase tracking-wider">
                        Human Agent Connected
                      </span>
                    </div>
                  )}

                  {/* Content */}
                  {msg.role === "agent" ? (
                    <div
                      className="text-sm leading-relaxed whitespace-pre-wrap"
                      dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                    />
                  ) : (
                    <p className="text-sm leading-relaxed whitespace-pre-wrap">{msg.content}</p>
                  )}

                  {/* Timestamp + status */}
                  {msg.timestamp && msg.role !== "system" && (
                    <div className={`flex items-center gap-1.5 mt-1.5 ${msg.role === "customer" ? "justify-end" : ""}`}>
                      <span className={`text-[10px] ${msg.role === "customer" ? "text-white/50" : "text-[var(--text-muted)]"}`}>
                        {new Date(msg.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                      </span>
                      {msg.role === "customer" && msg.status && (
                        <span className="text-[10px] text-white/40">
                          {msg.status === "sent" ? "✓" : msg.status === "delivered" ? "✓✓" : ""}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>
            ))}

            {/* Typing indicator */}
            {isTyping && (
              <div className="flex justify-start chat-bubble-agent">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white text-[10px] font-bold mr-2 mt-1 shrink-0 shadow-md shadow-indigo-500/20">
                  AI
                </div>
                <div className="bg-[var(--bg-card)] border border-[var(--border)] rounded-2xl rounded-bl-md px-5 py-3.5 flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-indigo-400 typing-dot" />
                  <span className="w-2 h-2 rounded-full bg-indigo-400 typing-dot" />
                  <span className="w-2 h-2 rounded-full bg-indigo-400 typing-dot" />
                </div>
              </div>
            )}

            {/* Scroll anchor */}
            <div ref={messagesEndRef} />
          </div>

          {/* Scroll-to-bottom FAB */}
          {!autoScroll && (
            <button
              onClick={() => {
                setAutoScroll(true);
                messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
              }}
              className="fixed bottom-28 right-8 w-10 h-10 rounded-full bg-[var(--bg-card)] border border-[var(--border)] text-[var(--text-muted)] hover:text-white hover:border-indigo-500 flex items-center justify-center shadow-xl transition-all z-20 animate-fade-in"
            >
              ↓
            </button>
          )}
        </div>

        {/* Input Bar */}
        <div className="border-t border-[var(--border)] bg-[var(--bg-secondary)]/95 backdrop-blur-xl shrink-0">
          <form onSubmit={sendMessage} className="max-w-3xl mx-auto px-4 sm:px-6 py-4">
            <div className="flex gap-2 items-end">
              <div className="flex-1 relative">
                <input
                  ref={inputRef}
                  type="text"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  placeholder={isConnected ? "Type your message..." : "Connecting..."}
                  disabled={!isConnected}
                  className="w-full bg-[var(--bg-input)] border border-[var(--border)] rounded-2xl px-5 py-3.5 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 transition-all disabled:opacity-50"
                />
              </div>
              <button
                type="submit"
                disabled={!input.trim() || !isConnected}
                className="w-12 h-12 rounded-2xl bg-gradient-to-br from-indigo-600 to-purple-600 flex items-center justify-center text-white disabled:opacity-30 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-indigo-500/30 transition-all shrink-0 group"
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className="w-5 h-5 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-transform"
                  viewBox="0 0 24 24"
                  fill="currentColor"
                >
                  <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
                </svg>
              </button>
            </div>
            <p className="text-[10px] text-[var(--text-muted)] text-center mt-2.5 opacity-50 tracking-wide">
              Powered by Project Bestie · Responses may be generated
            </p>
          </form>
        </div>
      </div>

      {/* ══════════════════════════════════════════
          CUSTOMER INFO DRAWER
          ══════════════════════════════════════════ */}
      {drawerOpen && (
        <div className="w-80 border-l border-[var(--border)] bg-[var(--bg-secondary)] flex flex-col shadow-2xl animate-fade-in shrink-0">
          <div className="p-4 border-b border-[var(--border)] flex justify-between items-center shrink-0">
            <h3 className="font-semibold text-sm">My Profile</h3>
            <button
              onClick={toggleDrawer}
              className="w-7 h-7 rounded-lg bg-[var(--bg-input)] border border-[var(--border)] flex items-center justify-center text-[var(--text-muted)] hover:text-white hover:border-red-500/50 transition-all text-xs"
            >
              ✕
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-5">
            {/* Profile */}
            {identifiedCustomer && (
              <div className="bg-gradient-to-br from-indigo-500/10 to-purple-500/10 border border-indigo-500/20 rounded-xl p-4 text-center">
                <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 text-white flex items-center justify-center text-xl font-bold mx-auto mb-3 shadow-lg shadow-indigo-500/20">
                  {identifiedCustomer.name.charAt(0)}
                </div>
                <h4 className="font-bold text-[var(--text-primary)]">{identifiedCustomer.name}</h4>
                <p className="text-xs text-[var(--text-muted)] mt-0.5">{identifiedCustomer.email}</p>
                <p className="text-[10px] text-[var(--text-muted)] mt-2 bg-[var(--bg-input)] px-2 py-1 rounded-lg inline-block">
                  ID: {identifiedCustomer.id}
                </p>
              </div>
            )}

            {/* Tickets */}
            <div>
              <h4 className="text-[11px] font-bold text-[var(--text-muted)] uppercase tracking-wider mb-2">
                My Tickets
              </h4>
              {!customerHistory ? (
                <div className="flex justify-center py-4">
                  <div className="w-5 h-5 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                </div>
              ) : customerHistory.recent_tickets?.length > 0 ? (
                <div className="space-y-2">
                  {customerHistory.recent_tickets.map((t) => (
                    <div key={t.id} className="bg-[var(--bg-input)] p-3 rounded-xl border border-[var(--border)] hover:border-indigo-500/30 transition-colors">
                      <div className="flex justify-between items-start mb-1">
                        <span className="font-medium text-xs truncate pr-2">#{t.id} — {t.subject}</span>
                        <span className={`badge badge-${t.status} text-[8px]`}>{t.status}</span>
                      </div>
                      <div className="text-[10px] text-[var(--text-muted)] flex justify-between">
                        <span className="uppercase">{t.type}</span>
                        <span>{new Date(t.created_at).toLocaleDateString()}</span>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-[var(--text-muted)] text-center py-3">No tickets found</p>
              )}
            </div>

            {/* Orders */}
            <div>
              <h4 className="text-[11px] font-bold text-[var(--text-muted)] uppercase tracking-wider mb-2">
                Recent Orders
              </h4>
              {!customerHistory ? (
                <div className="flex justify-center py-4">
                  <div className="w-5 h-5 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
                </div>
              ) : customerHistory.recent_orders?.length > 0 ? (
                <div className="space-y-2">
                  {customerHistory.recent_orders.map((o) => (
                    <div key={o.id} className="bg-[var(--bg-input)] p-3 rounded-xl border border-[var(--border)] hover:border-indigo-500/30 transition-colors">
                      <div className="flex justify-between items-start mb-1">
                        <span className="font-medium text-xs">Order #{o.id}</span>
                        <span className={`text-[9px] px-2 py-0.5 rounded-full font-bold uppercase ${
                          o.status === "delivered" ? "bg-emerald-500/10 text-emerald-400" :
                          o.status === "cancelled" ? "bg-red-500/10 text-red-400" :
                          "bg-indigo-500/10 text-indigo-400"
                        }`}>{o.status}</span>
                      </div>
                      <p className="text-[10px] text-[var(--text-muted)] truncate">{o.products?.name}</p>
                      <p className="text-[10px] text-[var(--text-muted)] mt-1">{new Date(o.order_date).toLocaleDateString()}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-[var(--text-muted)] text-center py-3">No orders found</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
