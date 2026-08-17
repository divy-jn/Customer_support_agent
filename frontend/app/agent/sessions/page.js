"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import EmptyState from "../../components/EmptyState";
import { useToast } from "../../components/Toast";
import { API_BASE, WS_BASE } from "../../lib/api";

const CANNED_RESPONSES = [
  { label: "Greeting", text: "Hello! I'm a support agent. Let me help you with your concern." },
  { label: "Checking", text: "Let me look into this for you. Please give me a moment." },
  { label: "Escalate", text: "I'm escalating this to our specialist team. You'll hear back within 24 hours." },
  { label: "Resolved", text: "I'm glad I could help! Is there anything else you need assistance with?" },
  { label: "Apology", text: "I sincerely apologize for the inconvenience. Let me make this right for you." },
];

function SessionTimer({ startTime }) {
  const [elapsed, setElapsed] = useState("0:00");
  useEffect(() => {
    if (!startTime) return;
    const start = new Date(startTime).getTime();
    const tick = () => {
      const diff = Math.floor((Date.now() - start) / 1000);
      const m = Math.floor(diff / 60);
      const s = diff % 60;
      setElapsed(`${m}:${s.toString().padStart(2, "0")}`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [startTime]);
  return <span className="font-mono text-xs text-[var(--text-muted)]">⏱ {elapsed}</span>;
}

export default function LiveSessionsPage() {
  const [sessions, setSessions] = useState([]);
  const [selectedSession, setSelectedSession] = useState(null);
  const [sessionMessages, setSessionMessages] = useState([]);
  const [agentInput, setAgentInput] = useState("");
  const [isMonitoring, setIsMonitoring] = useState(false);
  const [showCanned, setShowCanned] = useState(false);
  const [hasTakenOver, setHasTakenOver] = useState(false);
  const wsRef = useRef(null);
  const messagesEndRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  const selectedSessionRef = useRef(null);
  const { toast, ToastContainer } = useToast();

  // Fetch active sessions
  useEffect(() => {
    const fetchSessions = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/dashboard/sessions`);
        const data = await res.json();
        setSessions(data.active_sessions || []);
      } catch (err) {
        console.error("Failed to fetch sessions:", err);
      }
    };
    fetchSessions();
    const interval = setInterval(fetchSessions, 5000);
    return () => clearInterval(interval);
  }, []);

  // Connect to session as agent
  const monitorSession = useCallback((session) => {
    if (wsRef.current) wsRef.current.close();

    setSelectedSession(session);
    selectedSessionRef.current = session;
    setSessionMessages([]);
    setIsMonitoring(true);
    setHasTakenOver(false);
    if (reconnectTimerRef.current) { clearTimeout(reconnectTimerRef.current); reconnectTimerRef.current = null; }

    const ws = new WebSocket(`${WS_BASE}/ws/agent/${session.session_id}`);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      if (data.type === "ping") return;

      if (data.type === "session_state") {
        setSessionMessages(data.conversation_history || []);
      } else if (data.type === "customer_message") {
        setSessionMessages((prev) => [
          ...prev,
          { role: "customer", content: data.message, timestamp: data.timestamp },
        ]);
      } else if (data.type === "agent_response") {
        setSessionMessages((prev) => [
          ...prev,
          {
            role: "agent",
            content: data.message,
            agent_name: data.agent_name || "Adi",
            escalated: data.escalated,
            timestamp: data.timestamp,
          },
        ]);
      } else if (data.type === "escalation_alert") {
        setSessions((prev) =>
          prev.map((s) =>
            s.session_id === data.session_id
              ? { ...s, escalated: true, urgency: data.urgency }
              : s
          )
        );
        toast("🚨 Escalation alert! Customer requires human attention.", "error");
      }
    };

    ws.onclose = () => {
      setIsMonitoring(false);
      // Auto-reconnect after 3 seconds if we still have a selected session
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = setTimeout(() => {
        if (selectedSessionRef.current) {
          const ws2 = new WebSocket(`${WS_BASE}/ws/agent/${selectedSessionRef.current.session_id}`);
          wsRef.current = ws2;
          ws2.onmessage = ws.onmessage;
          ws2.onclose = ws.onclose;
          ws2.onopen = () => setIsMonitoring(true);
          ws2.onerror = () => ws2.close();
        }
      }, 3000);
    };

    ws.onerror = () => ws.close();
  }, [toast]);

  // Send message as human agent (takeover)
  const sendAgentMessage = (e) => {
    e.preventDefault();
    if (!agentInput.trim() || !wsRef.current || wsRef.current.readyState !== 1) return;

    wsRef.current.send(
      JSON.stringify({
        type: "agent_message",
        message: agentInput.trim(),
        agent_name: "Support Agent",
      })
    );

    setSessionMessages((prev) => [
      ...prev,
      {
        role: "agent",
        content: agentInput.trim(),
        agent_name: "Support Agent",
        human_agent: true,
        timestamp: new Date().toISOString(),
      },
    ]);

    setAgentInput("");
  };

  // Insert canned response
  const insertCanned = (text) => {
    setAgentInput(text);
    setShowCanned(false);
  };

  // Takeover the conversation
  const takeoverSession = () => {
    if (wsRef.current && wsRef.current.readyState === 1) {
      wsRef.current.send(
        JSON.stringify({ type: "takeover", agent_name: "Support Agent" })
      );
      setHasTakenOver(true);
      toast("You've taken over this conversation.", "success");
    }
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [sessionMessages]);

  // Sentiment emoji from last few messages
  const getSentiment = (session) => {
    const msg = (session.last_message || "").toLowerCase();
    if (/angry|furious|terrible|worst|hate|unacceptable|demand/.test(msg)) return "😡";
    if (/frustrated|annoyed|disappointed|upset|unhappy/.test(msg)) return "😤";
    if (/thank|great|perfect|excellent|happy|love|awesome/.test(msg)) return "😊";
    if (/help|issue|problem|broken|wrong/.test(msg)) return "😟";
    return "💬";
  };

  return (
    <div className="h-screen flex page-enter">
      <ToastContainer />

      {/* Sessions List */}
      <div className="w-80 border-r border-[var(--border)] flex flex-col bg-[var(--bg-secondary)]">
        <div className="p-4 border-b border-[var(--border)]">
          <h3 className="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider">
            Live Sessions
          </h3>
          <p className="text-xs text-[var(--text-muted)] mt-1">
            {sessions.length} active
          </p>
        </div>

        <div className="flex-1 overflow-y-auto">
          {sessions.length === 0 ? (
            <EmptyState
              icon="💬"
              title="No active sessions"
              description="Sessions appear when customers connect."
            />
          ) : (
            sessions.map((s) => (
              <button
                key={s.session_id}
                onClick={() => monitorSession(s)}
                className={`w-full text-left px-4 py-3 border-b border-[var(--border)]/50 hover:bg-[var(--bg-card)] transition-colors ${
                  selectedSession?.session_id === s.session_id
                    ? "bg-[var(--bg-card)] border-l-2 border-l-[var(--accent)]"
                    : ""
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <span className="text-base">{getSentiment(s)}</span>
                    <span className="text-sm font-medium text-[var(--text-primary)]">
                      {s.session_id.slice(0, 8)}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    {s.escalated && (
                      <span className="bg-red-500/20 text-red-400 text-[9px] px-1.5 py-0.5 rounded-full font-bold animate-pulse">
                        ESCALATED
                      </span>
                    )}
                    <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                  </div>
                </div>
                <p className="text-xs text-[var(--text-muted)] truncate">
                  {s.last_message || "No messages yet"}
                </p>
                <p className="text-[10px] text-[var(--text-muted)] mt-1 opacity-50">
                  {s.message_count} messages
                </p>
              </button>
            ))
          )}
        </div>
      </div>

      {/* Session Detail / Chat Monitor */}
      <div className="flex-1 flex flex-col">
        {!selectedSession ? (
          <div className="flex-1 flex items-center justify-center text-[var(--text-muted)]">
            <EmptyState
              icon="🎧"
              title="Select a session to monitor"
              description="View the conversation in real-time and take over if needed."
            />
          </div>
        ) : (
          <>
            {/* Session header */}
            <div className="border-b border-[var(--border)] px-6 py-3 flex items-center justify-between bg-[var(--bg-secondary)]/80 backdrop-blur-md">
              <div>
                <div className="flex items-center gap-3">
                  <h3 className="text-sm font-semibold">
                    Session {selectedSession.session_id.slice(0, 12)}…
                  </h3>
                  <SessionTimer startTime={selectedSession.created_at || new Date().toISOString()} />
                </div>
                <p className="text-xs text-[var(--text-muted)]">
                  {isMonitoring ? (
                    <span className="flex items-center gap-1">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse"></span>
                      Monitoring live
                    </span>
                  ) : "Disconnected"}{" "}
                  · {sessionMessages.length} messages
                  {hasTakenOver && (
                    <span className="ml-2 bg-emerald-500/20 text-emerald-400 text-[9px] px-1.5 py-0.5 rounded-full font-bold">
                      TAKEN OVER
                    </span>
                  )}
                </p>
              </div>
              <button
                onClick={takeoverSession}
                disabled={hasTakenOver}
                className={`px-4 py-2 text-xs font-medium rounded-xl transition-all ${
                  hasTakenOver
                    ? "bg-emerald-600/20 text-emerald-400 border border-emerald-500/30 cursor-default"
                    : "btn-glow"
                }`}
              >
                {hasTakenOver ? "✓ Taken Over" : "Take Over Conversation"}
              </button>
            </div>

            {/* Messages */}
            <div className="flex-1 overflow-y-auto px-6 py-4 space-y-3">
              {sessionMessages.map((msg, i) => (
                <div
                  key={i}
                  className={`flex ${msg.role === "customer" ? "justify-end" : "justify-start"} ${
                    msg.role === "customer" ? "chat-bubble-customer" : "chat-bubble-agent"
                  }`}
                >
                  <div
                    className={`max-w-[70%] rounded-2xl px-4 py-2.5 ${
                      msg.role === "customer"
                        ? "bg-blue-600/20 border border-blue-500/30 text-blue-100"
                        : msg.role === "system"
                        ? "bg-transparent text-[var(--text-muted)] text-center text-xs mx-auto"
                        : msg.human_agent
                        ? "bg-emerald-600/20 border border-emerald-500/30 text-emerald-100"
                        : "bg-[var(--bg-card)] border border-[var(--border)] text-[var(--text-primary)]"
                    }`}
                  >
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="text-[10px] font-semibold uppercase tracking-wider opacity-70">
                        {msg.role === "customer"
                          ? "Customer"
                          : msg.human_agent
                          ? msg.agent_name || "Human Agent"
                          : msg.agent_name || "AI Agent"}
                      </span>
                      {msg.escalated && (
                        <span className="badge badge-escalated text-[8px]">Escalated</span>
                      )}
                    </div>
                    <p className="text-sm leading-relaxed whitespace-pre-wrap">{msg.content}</p>
                    {msg.timestamp && (
                      <p className="text-[10px] opacity-40 mt-1">
                        {new Date(msg.timestamp).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </p>
                    )}
                  </div>
                </div>
              ))}
              <div ref={messagesEndRef} />
            </div>

            {/* Agent input with canned responses */}
            <div className="border-t border-[var(--border)] bg-[var(--bg-secondary)]/80 backdrop-blur-md">
              {/* Canned responses bar */}
              {showCanned && (
                <div className="px-6 pt-3 flex flex-wrap gap-2 animate-fade-in">
                  {CANNED_RESPONSES.map((c, i) => (
                    <button
                      key={i}
                      onClick={() => insertCanned(c.text)}
                      className="text-[11px] px-3 py-1.5 rounded-full bg-[var(--bg-input)] border border-[var(--border)] text-[var(--text-secondary)] hover:border-[var(--accent)] hover:text-white transition-all"
                      title={c.text}
                    >
                      {c.label}
                    </button>
                  ))}
                </div>
              )}

              <form onSubmit={sendAgentMessage} className="px-6 py-3">
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setShowCanned(!showCanned)}
                    className={`w-10 h-10 rounded-xl flex items-center justify-center text-lg transition-all border ${
                      showCanned
                        ? "bg-[var(--accent)]/20 border-[var(--accent)] text-[var(--accent)]"
                        : "bg-[var(--bg-input)] border-[var(--border)] text-[var(--text-muted)] hover:text-white"
                    }`}
                    title="Quick replies"
                  >
                    ⚡
                  </button>
                  <input
                    type="text"
                    value={agentInput}
                    onChange={(e) => setAgentInput(e.target.value)}
                    placeholder="Type a response as Support Agent..."
                    className="flex-1 bg-[var(--bg-input)] border border-[var(--border)] rounded-xl px-4 py-2.5 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-emerald-500 transition-all"
                  />
                  <button
                    type="submit"
                    disabled={!agentInput.trim() || !isMonitoring}
                    className="px-5 py-2.5 text-sm font-medium rounded-xl bg-emerald-600 text-white hover:bg-emerald-500 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                  >
                    Send
                  </button>
                </div>
              </form>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
