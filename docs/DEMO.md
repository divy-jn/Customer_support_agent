# Live Demonstration Script: IntelliSupport AI Agent

This document provides a complete, 8-10 minute demonstration script designed for project evaluators and SDE interviews. It showcases all primary capabilities of the IntelliSupport agent, including RAG grounding, tool execution, high-risk approvals, and seamless AI-to-Human handoffs.

---

## Preparation (Before the Demo)

1. Ensure the `.env` file is populated with valid keys.
2. Start the backend: `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
3. Start the frontend: `npm run dev`
4. Open two browser windows side-by-side:
   - **Customer Window:** `http://localhost:3000` (Login using a dev-login dropdown).
   - **Agent Dashboard Window:** `http://localhost:3000/agent` (Login with `AGENT_SECRET`).

---

## Demo Flow

### 1. General Support (Intent Classification)
**What you do:** As the customer, type a generic support greeting.
**Example Message:** `"Hi, how are you?"` or `"I need some help."`
**Expected UI Behavior:** The AI replies politely, asking how it can assist.
**What to say:**
> *"Here, the LangGraph Intent Router classifies the message as a general inquiry. It skips the database and vector DB entirely to save latency, routing straight to the general conversation node."*

### 2. RAG & Policy Grounding (Vector Search)
**What you do:** Ask a policy-related question.
**Example Message:** `"What is your return policy for laptops?"`
**Expected UI Behavior:** The AI explains the return policy and cites its source (e.g., `return_policy.md`).
**What to say:**
> *"The Router detects a policy question and delegates to the RAG Agent. It queries Pinecone for the semantic matches, injects the chunks into the LLM context, and generates a grounded response. Notice how the AI explicitly cites the source document, proving it isn't hallucinating."*

### 3. Order Tracking (Secure Tool Execution)
**What you do:** Ask about your past orders.
**Example Message:** `"Can you check the status of my recent orders?"`
**Expected UI Behavior:** The AI fetches the customer's orders from Supabase and formats them nicely.
**What to say:**
> *"This demonstrates the DB Agent executing a SQL tool via the FastAPI backend. Most importantly, the LLM isn't trusted with the Customer ID. The backend securely injects the authenticated JWT `customer_id` into the tool call, ensuring cross-customer data leakage is impossible."*

### 4. Cancellation (High-Risk Approval Flow)
**What you do:** Ask to cancel a specific order.
**Example Message:** `"Cancel my order for the UltraBook Pro 15."`
**Expected UI Behavior:** The AI identifies the order ID, but **does not execute the cancellation**. Instead, the UI displays an explicit "Pending Approval" dialog asking for confirmation.
**What to say:**
> *"For destructive actions, LangGraph intercepts the execution. The graph enters a pending state and waits for human confirmation. This guarantees that an autonomous LLM cannot accidentally or maliciously delete data without the user explicitly approving the exact parameters."*

### 5. Confirming the Action
**What you do:** Click "Yes" or type `"Yes"`.
**Expected UI Behavior:** The AI executes the cancellation, updates the database, and confirms the new status.
**What to say:**
> *"Once confirmed, the LangGraph state machine progresses to the execution node, fires the Supabase update, and generates a final response."*

### 6. Human Escalation (WebSocket Handoff)
**What you do:** Pretend to be frustrated or explicitly ask for a human.
**Example Message:** `"This is unacceptable, I want to speak to a human manager right now."`
**Expected UI Behavior:** 
- The Customer UI switches to "Human Support Mode".
- The Agent Dashboard flashes, showing a new escalated session.
**What to say:**
> *"The Intent Router detected high frustration. It instantly flipped the session's WebSocket mode from 'AI' to 'Human'. The LangGraph execution is paused, and the conversation is now completely live between the frontend and the agent dashboard."*

### 7. Agent Takes Over
**What you do:** Switch to the Agent Dashboard window and click "Takeover" on the escalated session.
**Expected UI Behavior:** The agent can now see the entire chat history.
**What to say:**
> *"As an agent, I have the full context of what the AI was discussing with the customer. The transition is completely seamless."*

### 8. Live Chat & Release
**What you do:** 
1. Send a message from the Agent Dashboard (`"Hi, I'm a manager. How can I resolve this?"`).
2. Send a reply from the Customer window.
3. Click "Release" on the Agent Dashboard.
**Expected UI Behavior:** The Customer UI switches back to "AI Mode".
**What to say:**
> *"Once the issue is resolved, the agent releases the session. The WebSocket router unpauses LangGraph, and the AI regains control, ready to assist the customer with their next inquiry."*

---

## Fallback Scenarios (If something breaks during demo)

- **WebSocket Disconnects:** Simply refresh the page. The backend stores the `session_id` in Supabase, and the transcript will seamlessly reload upon reconnection.
- **LLM Rate Limits:** If the Gemini API hits a rate limit, the backend will return a graceful error message indicating the service is temporarily busy, rather than crashing the WebSocket.
- **RAG misses the context:** Try asking the question slightly differently. The Pinecone similarity search threshold might occasionally filter out short queries.
