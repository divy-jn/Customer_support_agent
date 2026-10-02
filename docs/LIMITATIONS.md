# Project Limitations & Honest Disclosures

This document outlines the known limitations of the IntelliSupport Customer Support Agent. Because this is a major project/portfolio submission, architectural trade-offs were made to prioritize functional completeness and local demonstration stability over extreme production scalability.

---

## 1. Authentication

- **No Real OAuth/SSO:** The customer login currently relies on a `dev-login` endpoint that mints a valid JWT without requiring a real password or OTP verification. In a real deployment, this would be replaced by an Identity Provider (like Auth0, Firebase Auth, or Supabase Auth).
- **Static Agent Secret:** The Agent Dashboard is protected by a static environment variable (`AGENT_SECRET`) rather than role-based access control (RBAC).

## 2. Infrastructure & Scalability

- **Monolithic FastAPI Server:** The backend runs as a single FastAPI instance. 
- **In-Memory WebSocket Manager:** The `ConnectionManager` stores active WebSocket connections in a Python dictionary. If the application were scaled horizontally across multiple servers (e.g., behind a load balancer), WebSocket broadcasts (like agent handoffs) would fail because Server A wouldn't know about connections on Server B. **Solution:** A production app would require a Redis Pub/Sub backplane to synchronize WebSockets across instances.
- **Synchronous External Services in Threads:** Emails are sent using `asyncio.to_thread`. While this prevents blocking the FastAPI event loop, it is not a robust queuing mechanism. If the server crashes mid-request, the email is lost. **Solution:** A production app would use Celery or RabbitMQ for guaranteed background job execution.

## 3. Rate Limiting and Safety

- **No Distributed Rate Limiter:** The application lacks a robust rate-limiting middleware (like Redis-based token buckets) to prevent abuse of the LLM endpoints. 
- **Cost Controls:** Gemini API requests are not strictly throttled per user, meaning a malicious user could rack up API costs.

## 4. Artificial Data

- **Synthetic Dataset:** The Supabase database is populated with synthetic, generated Indian e-commerce data. Real-world edge cases (e.g., malformed addresses, legacy database schemas, or highly complex product variants) are not fully represented.
- **Hardcoded Prompts:** The Agent instructions (like the RAG system prompt) are hardcoded. In a mature system, these might be managed dynamically via LangSmith or a CMS.

## 5. UI/UX Limitations

- **Frontend Error Handling:** If the WebSocket connection drops, the frontend will attempt to reconnect, but it does not implement advanced offline queueing (e.g., saving unsent messages to `localStorage` and syncing them upon reconnection).
- **Basic Dashboard:** The Agent Dashboard is functional for demonstrations but lacks advanced CRM features like ticket tagging, rich text editing, or macro snippets.
