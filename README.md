# Customer Support Agent — Under Development

> **Project Status — Under Development**
>
> Customer Support Agent (CSA) is an AI-powered customer support platform currently under active development. It combines conversational AI, retrieval-augmented generation, agentic workflows, customer and order context, and human-agent collaboration.
>
> **Current state:**
> - Core backend, AI orchestration, RAG, database, frontend, and real-time communication are implemented.
> - The project is actively evolving across AI workflows, retrieval quality, backend reliability, support UX, testing, security, and deployment.
> - Architecture and interfaces may change as development continues.
> - This repository should be considered a work in progress rather than a finished production product.
>
> This status section will evolve as the project moves through development, testing, hardening, and deployment.

---

Customer Support Agent is designed to provide context-aware assistance, retrieve relevant knowledge, work with customer and order information, and escalate complex conversations to human support agents.

## Architecture

* **Backend**: Python & FastAPI
* **AI Orchestration**: LangGraph & LangChain
* **RAG**: Pinecone
* **Database**: Supabase PostgreSQL
* **Session / Cache**: Upstash Redis
* **Frontend**: Next.js, React & TailwindCSS
* **Real-time Communication**: WebSockets
* **LLM Providers**: OpenAI, Anthropic, Google, and open-source models through the project's model layer
* **Deployment**: Vercel (Frontend) & container-based backend deployment

### AI & Agent Workflow

The backend uses an agentic architecture to combine conversational reasoning with retrieved context, external tools, and application data.

* **Intent Routing**: Determines the appropriate support path and required tools for incoming conversations.
* **Knowledge Retrieval**: Retrieves relevant knowledge-base information through Pinecone to ground responses.
* **Customer & Order Context**: Works with customer and order information stored in Supabase PostgreSQL.
* **Human Escalation**: Supports handing complex conversations to human support agents through the support interface.
* **Persistent Sessions**: Maintains conversation and session state for consistent support interactions.

## Core Capabilities

* AI-powered customer support conversations
* Retrieval-Augmented Generation (RAG)
* Agentic intent routing and tool orchestration
* Customer and order data access
* Human-agent escalation and takeover
* Persistent conversation and session handling
* Real-time support communication through WebSockets
* Support analytics and operational workflows

## Development & Deployment

CSA is currently under active development.

Current development is focused on improving:

* Agent and LangGraph workflows
* Retrieval quality and RAG pipelines
* Tool reliability and backend architecture
* Real-time customer/support-agent communication
* Frontend UX and support workflows
* Testing and production hardening
* Deployment and infrastructure

### Local Setup

#### Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
# Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

#### Frontend

```bash
cd frontend
npm install
npm run dev
```

Create your local environment configuration from `.env.example` and add the required credentials locally.

### Deployment

The application is structured for independent frontend and backend deployment. The frontend can be deployed through Vercel, while the backend can be deployed as a container using the repository's deployment configuration.

## Project Structure

```text
Customer_support_agent/
├── backend/          # API, agents, tools, retrieval and business logic
├── frontend/         # Support dashboard and user interface
├── dataset/          # Project data and supporting resources
├── docs/             # Project documentation
└── .env.example      # Safe environment template
```

## Security

Never commit real API keys, database passwords, tokens, or other credentials. Use environment variables or a managed secret store for sensitive configuration.

## License

This project is licensed under the terms of the repository's `LICENSE` file.
