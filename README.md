# Customer Support Agent — Under Development

> **Project status — Under Development**
>
> Customer Support Agent is an actively evolving AI-powered customer support platform. The project is being developed around conversational AI, retrieval-augmented generation, agentic workflows, customer data, and human-agent collaboration.
>
> **Current state:**
> - The core application is organized into `backend/`, `frontend/`, and `dataset/`.
> - The backend is built around Python, FastAPI, LangGraph, and LangChain.
> - The frontend provides a modern support-agent interface with real-time communication.
> - AI, retrieval, data access, UI, deployment, testing, and reliability are still being actively developed and refined.
> - This repository should be considered a work in progress rather than a finished production product.
>
> This status section will evolve as the project moves through development, testing, hardening, and deployment.
>
> ---
>
> Customer Support Agent is designed to provide context-aware assistance to customers, retrieve relevant knowledge, work with customer and order information, and escalate complex conversations to human support agents.
>
> ## Architecture
>
> * **Backend**: Python & FastAPI
> * **AI Orchestration**: LangGraph & LangChain
> * **RAG**: Pinecone
> * **Database**: Supabase PostgreSQL
> * **Session / Cache**: Upstash Redis
> * **Frontend**: Next.js, React & TailwindCSS
> * **Real-time Communication**: WebSockets
> * **LLM Providers**: OpenAI, Anthropic, Google, and open-source models through the project's model layer
> * **Deployment**: Vercel (Frontend) & container-based backend deployment
>
> ## Core Capabilities
>
> - AI-powered customer support conversations
> - Retrieval-Augmented Generation for knowledge-grounded responses
> - Agentic intent routing and tool orchestration
> - Customer and order data access
> - Human-agent escalation and takeover
> - Persistent conversation and session handling
> - Real-time support communication through WebSockets
> - Support analytics and operational workflows
>
> ## AI & Agent Workflow
>
> The backend uses an agentic architecture to combine conversational reasoning with external tools and retrieved context.
>
> ### Intent Routing
>
> Incoming conversations are processed through the AI workflow to determine the appropriate support path and required tools.
>
> ### Knowledge Retrieval
>
> Relevant knowledge-base information is retrieved through Pinecone and incorporated into responses to improve contextual accuracy.
>
> ### Customer & Order Context
>
> The system can work with customer and order information stored in Supabase PostgreSQL, allowing responses to be grounded in application data.
>
> ### Human Escalation
>
> Conversations that require human intervention can be handed over to support agents through the frontend dashboard and real-time communication layer.
>
> ## Development
>
> This project is under active development. Current work includes improving:
>
> - Agent and LangGraph workflows
> - Retrieval quality and RAG pipelines
> - Tool reliability and backend architecture
> - Real-time customer/support-agent communication
> - Frontend UX and support workflows
> - Testing and production hardening
> - Deployment and infrastructure
>
> Features, architecture, and interfaces may change as development continues.
>
> ## Local Setup
>
> ### Prerequisites
>
> - Node.js 18+
> - Python 3.10+
> - Git
>
> ### Backend
>
> ```bash
> cd backend
> python -m venv venv
> source venv/bin/activate
> # Windows: venv\Scripts\activate
> pip install -r requirements.txt
> uvicorn app.main:app --host 0.0.0.0 --port 8000
> ```
>
> Create your local environment configuration from `.env.example` and add the required credentials locally.
>
> ### Frontend
>
> ```bash
> cd frontend
> npm install
> npm run dev
> ```
>
> Configure `NEXT_PUBLIC_API_URL` to point to the running backend.
>
> ## Deployment
>
> The application is structured for independent frontend and backend deployment. The frontend can be deployed through Vercel, while the backend can be deployed as a container using the included Docker configuration.
>
> ## Security
>
> Never commit real API keys, database passwords, tokens, or other credentials. Use environment variables or a managed secret store for sensitive configuration.
>
> ## License
>
> This project is licensed under the terms of the repository's `LICENSE` file.
