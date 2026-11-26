"use client";

import { useState, useSyncExternalStore } from "react";
import { getStaffRole, signIn, signOut, subscribeToAuth } from "../lib/supabase-auth";

export default function StaffAuthGate({ adminOnly = false, children }) {
  const role = useSyncExternalStore(subscribeToAuth, getStaffRole, () => null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await signIn("staff", email, password);
    } catch (err) {
      setError(err.message);
    } finally {
      setPassword("");
      setBusy(false);
    }
  }

  if (role === "admin" || (role === "agent" && !adminOnly)) {
    return <>
      {children}
      <button className="fixed bottom-4 right-4 z-30 glass-card px-4 py-2 text-sm" onClick={() => signOut("staff")}>
        Sign out
      </button>
    </>;
  }

  return (
    <main className="grid min-h-screen place-items-center p-6">
      <div className="glass-card w-full max-w-sm space-y-4 p-6">
        <h1 className="text-xl font-bold">{adminOnly ? "Administrator sign-in" : "Staff sign-in"}</h1>
        {role === "agent" ? (
          <>
            <p role="alert">403 · This page requires an administrator account.</p>
            <button className="rounded-lg bg-indigo-600 px-3 py-2 text-white" onClick={() => signOut("staff")}>
              Switch account
            </button>
          </>
        ) : (
          <form className="grid gap-3" onSubmit={submit}>
            <label className="grid gap-1">Email
              <input className="rounded-lg border border-[var(--border)] bg-[var(--bg-input)] p-2"
                type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} required />
            </label>
            <label className="grid gap-1">Password
              <input className="rounded-lg border border-[var(--border)] bg-[var(--bg-input)] p-2"
                type="password" autoComplete="current-password" value={password}
                onChange={(event) => setPassword(event.target.value)} required />
            </label>
            {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
            <button type="submit" disabled={busy} className="rounded-lg bg-indigo-600 p-2 font-medium text-white disabled:opacity-50">
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
        )}
      </div>
    </main>
  );
}
