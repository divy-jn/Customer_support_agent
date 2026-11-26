import { getPortalToken, signOut, signOutIfCurrent } from "./supabase-auth";

export async function getAgentToken() {
  const token = getPortalToken("staff");
  if (!token) {
    signOut("staff");
    throw new Error("Sign in to the staff portal.");
  }
  return token;
}

export async function agentFetch(url, init = {}) {
  const token = await getAgentToken();
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(url, { ...init, headers });
  if (response.status === 401) signOutIfCurrent("staff", token);
  return response;
}

export const adminFetch = agentFetch;
