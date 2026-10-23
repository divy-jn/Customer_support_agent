# IntelliSupport - Intelligent Multi-Agent Customer Support System

[![Continuous Integration](https://github.com/divy-jn/Customer_support_agent/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/divy-jn/Customer_support_agent/actions/workflows/ci.yml)
[![Node.js](https://img.shields.io/badge/Frontend-Next.js%2016%20%7C%20React%2019-black)](file:///c:/building%20projs/major_proj/frontend)
[![Python](https://img.shields.io/badge/Backend-FastAPI%20%7C%20Python%203.10%2B-blue)](file:///c:/building%20projs/major_proj/backend)
[![LangGraph](https://img.shields.io/badge/Orchestration-LangGraph%20V2-green)](file:///c:/building%20projs/major_proj/backend/app/agents/domain_graph.py)
[![Database](https://img.shields.io/badge/Database-PostgreSQL%2015%20%7C%20Supabase-336791)](file:///c:/building%20projs/major_proj/backend/scripts/schema.sql)
[![Vector DB](https://img.shields.io/badge/Vector%20Search-Pinecone-teal)](file:///c:/building%20projs/major_proj/backend/app/rag)

**IntelliSupport** is an enterprise-grade, full-stack Customer Support Agent designed to autonomously resolve complex customer inquiries, execute transactional operations with cryptographic verification, manage real-time human escalation, and enforce strict identity isolation.

Built on an event-driven **LangGraph V2** multi-agent architecture with a **FastAPI** WebSocket backend and a **Next.js 16** frontend, IntelliSupport addresses common conversational AI failure modes—such as context loss, accidental side-effects, race conditions, and unauthorized data access.

---

## 🎯 Problem Statement & Design Objectives

Modern customer support automation frequently suffers from:
1. **Unbounded Agent Actions**: Conversational agents directly executing irreversible side effects (e.g., refunds or cancellations) upon ambiguous user intent.
2. **Context Loss During Escalation**: Disconnected transfers between automated agents and human staff requiring users to repeat history.
3. **Dual-Write Vulnerabilities**: Database state changes succeeding while notifications or webhooks silently fail.
4. **Data Leakage & Insecure Multi-Tenancy**: Agents accessing or modifying resources outside the authenticated customer's boundary.

### Core Objectives
- **Cryptographic Human-in-the-Loop (HITL) Gating**: Two-phase proposal and execution protocol bound by HMAC-SHA256 signatures and Compare-and-Swap (CAS) state validation.
- **Domain-Oriented Specialization**: Deterministic semantic routing across specialized sub-agents (`General`, `Product`, `Order`, `Payment`).
- **Transactional Consistency**: Transactional outbox engine and row-level idempotency locks to guarantee at-least-once delivery with zero duplicate executions.
- **Fail-Closed Security Model**: Strict database-backed session validation, rate limiting, and zero-spoofing ownership verification.

---

## 🏗️ Architecture Overview

```mermaid
graph TD
    subgraph Frontend - Next.js 16 & React 19
        C[Customer Chat UI]
        AC[ApprovalCard UX]
        A[Agent Dashboard]
    end

    subgraph Transport & Security
        WS[WebSocket Manager & Rate Limiter]
        REST[FastAPI REST API]
        AUTH[JWT & Session Ownership Validator]
    end

    subgraph LangGraph V2 Multi-Agent Engine
        SR[Semantic Router]
        SUP[Supervisor & WorkflowState Engine]
        subgraph Specialized Domain Agents
            GA[General Agent]
            PA[Product Agent]
            OA[Order Agent]
            PYA[Payment Agent]
        end
        GDN[Guardian Policy Engine]
        RC[Structured Response Composer]
        HIL[ApprovalService HMAC-SHA256 / CAS]
    end

    subgraph Data & Persistence
        PG[(PostgreSQL 15 / Supabase)]
        OUTBOX[(Transactional Outbox)]
        PIN[(Pinecone Vector DB)]
        RW[Reconciliation & Outbox Worker]
    end

    C <-->|WebSocket + JWT| WS
    A <-->|WebSocket + Agent Auth| WS
    WS --> AUTH --> SUP
    SUP --> SR
    SR --> GA & PA & OA & PYA
    OA & PYA <--> HIL
    GA & PA & OA & PYA --> GDN --> RC
    PA <--> PIN
    HIL <--> PG
    REST <--> PG
    PG --> OUTBOX --> RW
```

---

## ✨ Key Architectural Components

### 1. LangGraph V2 Domain-Oriented Multi-Agent System
- **Deterministic Semantic Router** ([`domain_graph.py`](file:///c:/building%20projs/major_proj/backend/app/agents/domain_graph.py)): Classifies intent into explicit business domains (`general`, `product`, `order`, `payment`).
- **Typed Workflow State** ([`models.py`](file:///c:/building%20projs/major_proj/backend/app/models.py)): Tracks active domain, turn counters, and domain parameters with optimistic concurrency control.
- **Domain Sub-Agents**:
  - **General Agent** ([`general_agent.py`](file:///c:/building%20projs/major_proj/backend/app/agents/general_agent.py)): Answers store policies and FAQs using Pinecone vector retrieval.
  - **Product Agent** ([`rag_agent.py`](file:///c:/building%20projs/major_proj/backend/app/agents/rag_agent.py)): Handles catalog discovery, inventory lookups, and warranty validation.
  - **Order Agent** ([`order_agent.py`](file:///c:/building%20projs/major_proj/backend/app/agents/order_agent.py)): Manages order tracking, address updates, and cancellation workflows.
  - **Payment Agent** ([`payment_agent.py`](file:///c:/building%20projs/major_proj/backend/app/agents/payment_agent.py)): Handles billing status, invoices, and refund proposals.
- **Guardian Policy Engine** ([`guardian.py`](file:///c:/building%20projs/major_proj/backend/app/agents/guardian.py)): Enforces deterministic escalation boundaries, automated support ticket creation, and policy guardrails.
- **Response Composer** ([`response_composer.py`](file:///c:/building%20projs/major_proj/backend/app/agents/response_composer.py)): Normalizes tone, citations, and structure across all domain paths.
- **Runtime Mode Switch**: Controlled via `AGENT_ARCHITECTURE_MODE` (`v2_all`, `v2_shadow`, `v1_only`) enabling seamless rollback if required.

### 2. Cryptographic Human-in-the-Loop (HITL) Approval Engine
High-risk side-effects (e.g. `process_refund`, `cancel_order`) cannot be directly executed in a single conversational turn:
- **Phase 1: Cryptographic Proposal**: The agent creates a `PendingApproval` record with a unique `approval_id`, bounded parameters, and an HMAC-SHA256 signature calculated with a high-entropy secret.
- **Phase 2: Compare-and-Swap Execution**: When confirmation is submitted, [`ApprovalService`](file:///c:/building%20projs/major_proj/backend/app/agents/approval_service.py) verifies the cryptographic signature, validates expiration, and executes a atomic CAS state change (`pending -> approved`). Duplicate requests or race conditions are rejected.
- **Frontend Interactive Component**: Dedicated [`ApprovalCard.js`](file:///c:/building%20projs/major_proj/frontend/app/components/ApprovalCard.js) UI renders clear, non-technical summaries of pending actions with explicit **Confirm** and **Cancel** controls.
- **Interruption Resilience**: If the user switches domains (e.g., asks a product question) during a pending approval, the approval remains intact and does not accidentally trigger on ambiguous replies.

### 3. Transactional Outbox, Idempotency & Database Resilience
- **Transactional Outbox** ([`outbox/lifecycle.py`](file:///c:/building%20projs/major_proj/backend/app/outbox/lifecycle.py)): Email notifications and external side-effects are saved into `outbox_events` within the same atomic database transaction as business operations.
- **Standalone Outbox Worker** ([`outbox/standalone.py`](file:///c:/building%20projs/major_proj/backend/app/outbox/standalone.py)): Asynchronously polls, delivers, and acknowledges pending outbox records with exponential backoff and retry budgets.
- **Concurrency & Idempotency Protection**: Tested under high concurrent loads using PostgreSQL `SELECT ... FOR UPDATE` locks on `idempotency_records` to prevent double-spending or duplicate operations.

### 4. Security, Session Isolation & Audit Redaction
- **Session Ownership Validation**: WebSockets verify that the connection's authenticated `customer_id` matches the persistent database owner, preventing cross-tenant session hijacking.
- **Tool-Level Sandboxing**: Injected `customer_id` context ensures the model cannot fabricate or override user IDs to query unauthorized records.
- **Sensitive Data Scrubbing** ([`logging_security.py`](file:///c:/building%20projs/major_proj/backend/app/logging_security.py)): Automatic redaction of credentials, API tokens, passwords, and PII from application logs.
- **Rate Limiting**: Sliding-window rate limiters on WebSocket connections to protect against flooding.

---

## 🛠️ Technology Stack

| Layer | Technologies |
| :--- | :--- |
| **Backend Framework** | Python 3.10+, FastAPI, Uvicorn, WebSockets, Pydantic v2 |
| **AI Orchestration** | LangGraph, LangChain, Google Gemini (`google-genai`), Pinecone Vector DB |
| **Relational Database** | PostgreSQL 15 / Supabase, Psycopg2, Connection Pooling |
| **Frontend Platform** | Next.js 16 (App Router), React 19, Vanilla CSS, Tailwind CSS |
| **Testing & CI** | Pytest, Playwright E2E, GitHub Actions CI/CD (Ubuntu + Postgres 15 service) |

---

## 📁 Repository Structure

```text
major_proj/
├── .github/workflows/
│   └── ci.yml                          # 3-tier CI pipeline: Frontend, Backend, PostgreSQL 15
├── backend/
│   ├── app/
│   │   ├── agents/                     # LangGraph V2 Multi-Agent Engine
│   │   │   ├── approval_service.py     # HMAC-SHA256 HITL approval service
│   │   │   ├── domain_graph.py         # V2 Semantic router and graph definitions
│   │   │   ├── general_agent.py        # General & FAQ support agent
│   │   │   ├── guardian.py             # Policy guardrail & escalation classifier
│   │   │   ├── order_agent.py          # Order management domain agent
│   │   │   ├── payment_agent.py        # Payment & refund domain agent
│   │   │   ├── rag_agent.py            # Knowledge base retriever agent
│   │   │   ├── response_composer.py    # Structured tone and formatting composer
│   │   │   └── supervisor.py           # Multi-turn supervisor workflow coordinator
│   │   ├── outbox/                     # Transactional outbox pattern & worker
│   │   ├── persistence/                # Redis and PostgreSQL session state storage
│   │   ├── websocket/                  # Live chat handlers, rate limiters, connection manager
│   │   ├── config.py                   # Pydantic environment configuration
│   │   ├── main.py                     # FastAPI application entry point
│   │   └── models.py                   # Domain schemas, state schemas, approval models
│   ├── scripts/
│   │   ├── schema.sql                  # Canonical PostgreSQL DDL (tables, indexes, triggers)
│   │   ├── seed_database.py            # Development seed script
│   │   ├── test_fixtures.sql           # Integration test data fixtures
│   │   └── test_roles.sql              # Database privilege & role isolation definitions
│   └── tests/                          # 940+ unit, integration, and concurrency tests
├── frontend/
│   ├── app/
│   │   ├── agent/                      # Support staff live co-pilot dashboard
│   │   ├── components/
│   │   │   └── ApprovalCard.js         # Interactive cryptographic HITL confirmation card
│   │   ├── page.js                     # Customer chat interface
│   │   └── layout.js                   # Application root layout
│   ├── e2e/
│   │   └── approval_ux.spec.js         # Playwright end-to-end browser tests
│   └── playwright.config.js            # Playwright browser testing configuration
└── docs/                               # Architecture decision records (ADRs) & specifications
```

---

## 🚀 Getting Started

### Prerequisites
- **Python**: 3.10, 3.11, or 3.12
- **Node.js**: 18.x or 20.x+
- **PostgreSQL**: 15+ (Local instance or Supabase project)

---

### 1. Environment Configuration

#### Backend Configuration
Create `backend/.env` based on `backend/.env.example`:
```ini
# Application Secrets
JWT_SECRET=your-minimum-32-character-jwt-secret-here
APPROVAL_SIGNING_SECRET=your-minimum-32-character-hmac-secret-here
AGENT_SECRET=your-minimum-32-character-agent-secret-here

# Architecture Mode: v2_all, v2_shadow, or v1_only
AGENT_ARCHITECTURE_MODE=v2_all

# Database Connections
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-supabase-key
POSTGRES_URL=postgresql://user:password@localhost:5432/dbname

# AI Services
GEMINI_API_KEY=your-google-gemini-api-key
PINECONE_API_KEY=your-pinecone-api-key
PINECONE_INDEX=customer-support
```

#### Frontend Configuration
Create `frontend/.env.local`:
```ini
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000
```

---

### 2. Database Setup

Apply the canonical database schema and seed initial data:
```bash
# Apply schema to PostgreSQL
psql -d your_database -f backend/scripts/schema.sql

# Seed demo catalog, customers, and orders
python backend/scripts/seed_database.py
```

---

### 3. Running Backend Services

#### FastAPI Application
```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Start FastAPI server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

#### Standalone Outbox Worker (Optional / Production)
In a separate terminal, launch the transactional outbox worker:
```bash
cd backend
python -m app.outbox.standalone
```

---

### 4. Running the Frontend

```bash
cd frontend
npm install
npm run dev
```

Navigate to:
- **Customer Chat UI**: [http://localhost:3000](http://localhost:3000)
- **Agent Co-Pilot Dashboard**: [http://localhost:3000/agent](http://localhost:3000/agent)
- **Tech Admin Settings**: [http://localhost:3000/admin](http://localhost:3000/admin)

---

## 🧪 Testing & Verification

The repository contains an exhaustive test pyramid spanning unit, integration, concurrency, and browser E2E suites:

```text
======================= Test Suite Coverage =======================
• Unit & Architecture Tests:  940+ passed (deterministic, mock-isolated)
• Live PostgreSQL Tests:      Dedicated PostgreSQL 15 concurrency & lock suite
• Playwright Browser E2E:     16/16 browser scenarios passed
• Evaluation Suite:           21 golden dataset evals
===================================================================
```

### Running Backend Tests
```bash
cd backend
# Run deterministic unit and architectural suite
python -m pytest tests/ -m "not requires_postgres and not llm_eval" -v
```

### Running Real PostgreSQL Integration Tests
Requires a running disposable PostgreSQL 15 instance:
```bash
cd backend
$env:TEST_POSTGRES_URL="postgresql://postgres:postgres@localhost:5432/postgres"
python -m pytest tests/test_*real*.py tests/test_postgres_concurrency_real.py -v
```

### Running Playwright Browser E2E Tests
```bash
cd frontend
npx playwright test
```

---

## 🔄 Continuous Integration Pipeline

All pull requests and commits to `main` are validated by GitHub Actions across three parallel jobs:

1. **Frontend lint and production build**:
   - Executes ESLint rules and builds the production Next.js 16 / React 19 bundle.
2. **Backend unit and architecture checks**:
   - Runs the complete 940+ offline test suite with deterministic LLM probe gating.
3. **Real PostgreSQL Idempotency & Concurrency Tests**:
   - Spawns a dedicated PostgreSQL 15 service container.
   - Applies `schema.sql`, `test_fixtures.sql`, and `test_roles.sql`.
   - Executes multi-threaded race condition tests, outbox drains, and row-level lock validations.

---

## 📜 License & Acknowledgments

Developed as a major engineering submission demonstrating advanced conversational multi-agent systems, reliable distributed state management, and production-grade AI security patterns.
