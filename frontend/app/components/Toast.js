"use client";

import { useState, useEffect, useCallback } from "react";

/**
 * Toast notification component with auto-dismiss.
 * 
 * Usage:
 *   const { toast, ToastContainer } = useToast();
 *   toast("Message saved!", "success");
 * 
 *   // In JSX:
 *   <ToastContainer />
 */

const VARIANTS = {
  success: {
    bg: "rgba(34, 197, 94, 0.15)",
    border: "rgba(34, 197, 94, 0.3)",
    color: "#4ade80",
    icon: "✓",
  },
  error: {
    bg: "rgba(239, 68, 68, 0.15)",
    border: "rgba(239, 68, 68, 0.3)",
    color: "#f87171",
    icon: "✕",
  },
  warning: {
    bg: "rgba(245, 158, 11, 0.15)",
    border: "rgba(245, 158, 11, 0.3)",
    color: "#fbbf24",
    icon: "⚠",
  },
  info: {
    bg: "rgba(99, 102, 241, 0.15)",
    border: "rgba(99, 102, 241, 0.3)",
    color: "#818cf8",
    icon: "ℹ",
  },
};

let _toastId = 0;

export function useToast(autoDismissMs = 4000) {
  const [toasts, setToasts] = useState([]);

  const removeToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (message, variant = "info") => {
      const id = ++_toastId;
      setToasts((prev) => [...prev, { id, message, variant }]);
      if (autoDismissMs > 0) {
        setTimeout(() => removeToast(id), autoDismissMs);
      }
    },
    [autoDismissMs, removeToast]
  );

  function ToastContainer() {
    return (
      <div
        style={{
          position: "fixed",
          top: 16,
          right: 16,
          zIndex: 9999,
          display: "flex",
          flexDirection: "column",
          gap: 8,
          pointerEvents: "none",
          maxWidth: 380,
        }}
      >
        {toasts.map((t) => {
          const v = VARIANTS[t.variant] || VARIANTS.info;
          return (
            <div
              key={t.id}
              className="animate-fade-in"
              style={{
                pointerEvents: "auto",
                background: v.bg,
                border: `1px solid ${v.border}`,
                borderRadius: 12,
                padding: "12px 16px",
                display: "flex",
                alignItems: "center",
                gap: 10,
                backdropFilter: "blur(12px)",
                boxShadow: "0 8px 32px rgba(0,0,0,0.3)",
              }}
            >
              <span
                style={{
                  width: 24,
                  height: 24,
                  borderRadius: "50%",
                  background: v.bg,
                  border: `1px solid ${v.border}`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 12,
                  fontWeight: 700,
                  color: v.color,
                  flexShrink: 0,
                }}
              >
                {v.icon}
              </span>
              <span
                style={{
                  fontSize: 13,
                  fontWeight: 500,
                  color: v.color,
                  flex: 1,
                }}
              >
                {t.message}
              </span>
              <button
                onClick={() => removeToast(t.id)}
                style={{
                  background: "none",
                  border: "none",
                  color: v.color,
                  cursor: "pointer",
                  opacity: 0.6,
                  fontSize: 14,
                  padding: 2,
                  flexShrink: 0,
                }}
              >
                ✕
              </button>
              {/* Auto-dismiss progress bar */}
              <div
                style={{
                  position: "absolute",
                  bottom: 0,
                  left: 0,
                  right: 0,
                  height: 2,
                  borderRadius: "0 0 12px 12px",
                  background: v.color,
                  animation: `toast-progress ${autoDismissMs}ms linear forwards`,
                }}
              />
            </div>
          );
        })}
      </div>
    );
  }

  return { toast, ToastContainer };
}
