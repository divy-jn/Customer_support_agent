import { API_BASE } from "./api";

const AGENT_TOKEN_KEY = "intellisupport_agent_jwt";

/**
 * Obtain a local-development agent JWT. In deployed environments, replace this
 * dev endpoint with the real support-agent sign-in flow; never enable DEBUG_MODE
 * in production.
 */
export async function getAgentToken({ forceRefresh = false } = {}) {
  if (typeof window === "undefined") {
    throw new Error("Agent authentication is only available in the browser.");
  }

  if (forceRefresh) localStorage.removeItem(AGENT_TOKEN_KEY);
  const existing = localStorage.getItem(AGENT_TOKEN_KEY);
  if (existing) return existing;

  // #review: Relies on dev-login which is strictly rejected when DEBUG_MODE=False on backend
  const response = await fetch(`${API_BASE}/api/v1/auth/agent/dev-login`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    throw new Error(
      response.status === 403
        ? "Agent dev login is disabled. Configure a real agent authentication flow."
        : `Agent login failed (${response.status}). Check the backend URL and DEBUG_MODE.`,
    );
  }

  const data = await response.json();
  if (!data.access_token) throw new Error("Agent login returned no access token.");
  localStorage.setItem(AGENT_TOKEN_KEY, data.access_token);
  return data.access_token;
}

export async function agentFetch(url, init = {}) {
  let token = await getAgentToken();
  const send = (accessToken) =>
    fetch(url, {
      ...init,
      headers: {
        ...(init.headers || {}),
        Authorization: `Bearer ${accessToken}`,
      },
    });

  let response = await send(token);
  if (response.status === 401) {
    token = await getAgentToken({ forceRefresh: true });
    response = await send(token);
  }
  return response;
}
