# Customer Support Agent

An AI-powered customer support platform designed to provide context-aware assistance, retrieve relevant knowledge, work with customer and order data, and escalate complex conversations to human agents.

## Overview

The system combines conversational AI, retrieval-augmented generation, agentic workflows, backend tools, persistent conversations, and a support-agent interface.

## Architecture

- **Backend:** Python, FastAPI
- **AI Orchestration:** LangGraph, LangChain
- **RAG:** Pinecone
- **Database:** Supabase PostgreSQL
- **Frontend:** Next.js, React, Tailwind CSS
- **Real-time:** WebSockets
- **Deployment:** Vercel and container-based backend deployment

## Key Features

- AI-powered customer support
- Retrieval-Augmented Generation
- Agentic intent routing and tool use
- Customer and order data access
- Human-agent escalation
- Persistent conversation history
- Support dashboard and real-time chat
- Automated testing and production hardening

## Development

The project is actively evolving across AI workflows, retrieval quality, backend reliability, UI, deployment, and security.

### Local Setup

```bash
# Backend
cd backend
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Frontend
cd frontend
npm install
npm run dev
```

Create your local environment configuration from `.env.example`. Never commit real credentials.
