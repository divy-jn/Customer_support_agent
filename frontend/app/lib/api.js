/**
 * Centralized API / WebSocket base URLs.
 *
 * Override via environment variables (set in .env.local):
 *   NEXT_PUBLIC_API_URL=https://api.example.com
 *   NEXT_PUBLIC_WS_URL=wss://api.example.com
 */
// Centralized API and WebSocket base endpoints
// #review: Default fallback port is 8002, ensure backend api_port in config.py matches this or is set via .env.local
export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8002";
export const WS_BASE  = process.env.NEXT_PUBLIC_WS_URL  || "ws://localhost:8002";
