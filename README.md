# IntelliSupport AI

IntelliSupport AI is an advanced, scalable, AI-powered customer support ecosystem. It uses generative AI to instantly answer customer queries, retrieve knowledge base articles, interface with customer order databases, and escalate complex issues to human agents in real-time.

## System Architecture

The project is split into two primary components:

1. **Backend (FastAPI)**:
   - **LangGraph AI Agent**: Manages the conversational state machine and orchestrates intent routing.
   - **Vector Database (Pinecone)**: Retrieves knowledge base documents (RAG) for accurate answers.
   - **Relational Database (Supabase)**: Stores customer and order information.
   - **Session Store (Upstash Redis)**: Maintains persistent, high-performance WebSocket chat history.
   - **Multi-Provider LLM Factory**: Supports rapid switching between OpenAI, Anthropic, Google, and open-source models (via vLLM/Ollama).

2. **Frontend (Next.js)**:
   - Modern, responsive React dashboard built for human support agents.
   - Real-time WebSocket connectivity for taking over AI chats.
   - Analytics dashboard for viewing support statistics.

## Tech Stack

- **Frontend**: Next.js, React, TailwindCSS
- **Backend**: Python, FastAPI, LangGraph, LangChain
- **Databases**: Pinecone (Vector), Supabase (PostgreSQL), Upstash (Redis)
- **Deployment**: Vercel (Frontend), Hugging Face Spaces (Backend Docker Container)

## Setup Instructions

### 1. Prerequisites
Ensure you have the following installed:
- Node.js (v18+)
- Python (3.10+)
- Git

### 2. Backend Setup
1. Navigate to the `backend/` directory.
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\\Scripts\\activate
   pip install -r requirements.txt
   ```
3. Copy the `.env.example` file to `.env` and fill in your API keys (Supabase, Pinecone, Upstash Redis, and LLM Provider).
4. Run the FastAPI server:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

### 3. Frontend Setup
1. Navigate to the `frontend/` directory.
2. Install Node dependencies:
   ```bash
   npm install
   ```
3. Create a `.env.local` file with the following variable pointing to your backend URL:
   ```env
   NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
   ```
4. Start the Next.js development server:
   ```bash
   npm run dev
   ```

## Collaboration and Deployment

This project was built collaboratively by a cross-functional team handling UI, core AI/backend engineering, and infrastructure/databases. 

To deploy this project to production:
1. Deploy the Next.js `frontend/` directory to **Vercel**.
2. Deploy the FastAPI `backend/` directory using the provided `Dockerfile` to **Hugging Face Spaces** or a similar container registry.
