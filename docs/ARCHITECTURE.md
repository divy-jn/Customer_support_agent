# System Architecture & Design Choices

This document explains the technical architecture of the IntelliSupport Customer Support Agent, specifically tailored for project evaluations, viva-voce, and SDE interviews.

---

## 1. High-Level Flow

### Request Flow
1. **Frontend:** The Next.js frontend captures user input.
2. **WebSocket Layer:** The message is sent over a WebSocket connection to the FastAPI backend, bypassing HTTP overhead for real-time bidirectional communication.
3. **Authentication:** The connection is validated using a JWT (`customer_id`).
4. **LangGraph Orchestration:** The backend passes the message to LangGraph.
5. **Tool Execution:** The LLM decides what actions to take (e.g., query RAG, query Supabase).
6. **Response:** The final formatted response is streamed back to the frontend.

### Authentication & Ownership Flow
Security is paramount in a multi-tenant system. 
- The **Frontend** never sends `customer_id` in the JSON payload of the WebSocket messages. 
- Instead, the backend extracts the `customer_id` securely from the JWT attached to the WebSocket connection.
- When the LLM decides to execute a tool (like `track_order`), the backend **hard-injects** the authenticated `customer_id` into the tool's parameters.
- If the LLM hallucinates and tries to fetch data for Customer B while Customer A is logged in, the SQL query will enforce `WHERE customer_id = 'A'`, returning nothing.

---

## 2. Component Details & Interview Questions

### Q: Why did you choose LangGraph instead of a standard LangChain AgentExecutor?
**A:** Standard ReAct agents run in a black-box loop. LangGraph allows us to define the AI conversation as a deterministic State Machine. This gives us explicit control over the routing. For example, if we detect an angry customer, we can hard-code an edge to an `EscalationNode` rather than hoping the LLM decides to escalate on its own. It also makes implementing "Pending Approval" gates trivial, as we can simply pause the state machine.

### Q: Why use WebSockets instead of REST APIs?
**A:** Customer support requires real-time features. We need server-to-client push events for two major reasons:
1. When an agent takes over a chat, we need to immediately notify the customer's UI to change its state.
2. AI responses can take several seconds to generate. WebSockets allow us to stream tokens in real-time, improving the perceived latency.

### Q: Why use Supabase?
**A:** Supabase provides an instant, hosted PostgreSQL database. Crucially, it provides out-of-the-box Row Level Security (RLS). While our Next.js frontend doesn't query Supabase directly, RLS guarantees that even if a public API key leaks, the data remains locked down, requiring the backend service role key.

### Q: Why use Pinecone for RAG instead of Postgres pgvector?
**A:** Pinecone is a purpose-built Vector Database optimized for ultra-fast nearest-neighbor search at scale. While `pgvector` is excellent, separating the operational database (Supabase) from the vector search engine (Pinecone) allows us to scale vector search independently of transactional workloads, which is a common pattern in enterprise AI systems.

### Q: How do you prevent cross-customer data leakage?
**A:** The fundamental rule is: **Never trust the LLM with identity**. The LLM operates in an untrusted context. When the LLM says "Fetch order #1234 for customer #5", the backend interception layer (`_execute_tool` in `graph.py`) forcefully overwrites `customer_id = 5` with the `customer_id` extracted from the JWT. 

### Q: How does the High-Risk Approval Flow work?
**A:** Destructive actions (like `cancel_order`) must not be autonomous.
1. When the LLM selects the `cancel_order` tool, the `db_plan_node` detects the action is in the `HIGH_RISK_ACTIONS` set.
2. Instead of executing it, it returns a `pending_approval` state object and asks the user for confirmation.
3. LangGraph pauses.
4. When the user replies "Yes", the backend detects the pending state, routes the execution to `db_execute_node`, verifies the `customer_id` again, and finally executes the SQL mutation.

### Q: What happens if the WebSocket disconnects?
**A:** The frontend is designed to automatically reconnect. The backend persists the entire conversation transcript to Supabase under a unique `session_id`. Upon reconnection, the backend fetches the `session_id` and restores the chat history, making disconnects entirely transparent to the user.
