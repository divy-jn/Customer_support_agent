import { API_BASE, SUPABASE_URL, SUPABASE_ANON_KEY } from "./api";

const tokenKeys = {
  customer: "intellisupport_customer_app_jwt",
  staff: "intellisupport_staff_app_jwt",
};
const STAFF_ROLE_KEY = "intellisupport_staff_role";
const AUTH_CHANGE = "intellisupport-auth-change";

export function getPortalToken(portal) {
  if (typeof window === "undefined") return null;
  const token = localStorage.getItem(tokenKeys[portal]);
  if (!token) return null;
  try {
    // Check expiry for the UI. The API verifies the signature and permissions.
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return payload.exp * 1000 > Date.now() ? token : null;
  } catch {
    return null;
  }
}

export function getStaffRole() {
  return getPortalToken("staff") ? localStorage.getItem(STAFF_ROLE_KEY) : null;
}

export function subscribeToAuth(onChange) {
  window.addEventListener(AUTH_CHANGE, onChange);
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(AUTH_CHANGE, onChange);
    window.removeEventListener("storage", onChange);
  };
}

export function signOut(portal) {
  localStorage.removeItem(tokenKeys[portal]);
  if (portal === "staff") localStorage.removeItem(STAFF_ROLE_KEY);
  window.dispatchEvent(new Event(AUTH_CHANGE));
}

export function signOutIfCurrent(portal, token) {
  if (localStorage.getItem(tokenKeys[portal]) === token) signOut(portal);
}

export async function signIn(portal, email, password) {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
    throw new Error("Sign-in is unavailable because Supabase Auth is not configured.");
  }

  const auth = await fetch(`${SUPABASE_URL}/auth/v1/token?grant_type=password`, {
    method: "POST",
    headers: { "Content-Type": "application/json", apikey: SUPABASE_ANON_KEY },
    body: JSON.stringify({ email: email.trim(), password }),
  });
  if (!auth.ok) {
    throw new Error(auth.status >= 500
      ? "Sign-in is temporarily unavailable."
      : "Check your email and password and confirm your email before signing in.");
  }
  const { access_token: supabaseToken } = await auth.json();
  if (!supabaseToken) throw new Error("Supabase did not return a sign-in token.");

  const exchange = await fetch(`${API_BASE}/api/auth/supabase/exchange`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${supabaseToken}` },
    body: JSON.stringify({ portal }),
  });
  if (!exchange.ok) {
    const error = await exchange.json().catch(() => ({}));
    throw new Error(error.detail || `Sign-in failed (${exchange.status}).`);
  }
  const { access_token: token, principal } = await exchange.json();
  const roles = portal === "staff" ? ["agent", "admin"] : ["customer"];
  if (!token || !roles.includes(principal?.role)) throw new Error("Invalid application session.");

  localStorage.setItem(tokenKeys[portal], token);
  if (portal === "staff") localStorage.setItem(STAFF_ROLE_KEY, principal.role);
  window.dispatchEvent(new Event(AUTH_CHANGE));
  return principal;
}
