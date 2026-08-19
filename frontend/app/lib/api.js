/**
 * Centralized API / WebSocket base URLs.
 *
 * Override via environment variables (set in .env.local):
 *   NEXT_PUBLIC_API_URL=https://api.example.com
 *   NEXT_PUBLIC_WS_URL=wss://api.example.com
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
export const WS_BASE  = process.env.NEXT_PUBLIC_WS_URL  || "ws://localhost:8000";
