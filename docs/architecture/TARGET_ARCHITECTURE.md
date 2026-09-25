# Target Architecture

## Overview
The Customer Support Agent will transition from an intent-to-agent routing model to a strict Domain-Oriented Multi-Agent architecture. Current modules like `db_agent` or `rag_agent` blend technical capabilities (e.g., retrieving from a DB or Pinecone) with domain responsibilities (e.g., handling orders vs. handling billing). The target architecture decouples business domains from their technical capabilities.

## Architecture Flow

User 
  ↓
**Semantic Router**: Classifies the message and extracts semantic information (intent, domain, sentiment, urgency), but does *not* dictate implementation-level execution paths.
  ↓
**Supervisor / Workflow State**: Manages the transactional lifecycle. Stores session details, current domain, active workflow, and intermediate steps (like pending approvals).
  ↓
**Domain Agent**: One of the core domain experts (General, Product, Order, Payment, Escalation).
  ↓
**Agent Skill**: A versionable SOP (Standard Operating Procedure) for a specific workflow (e.g., Warranty Workflow for ProductAgent), dictating how the agent solves the domain problem.
  ↓
**Shared Tools**: Domain-agnostic capabilities (DB queries, vector retrieval, Web Search, ticketing, email).
  ↓
**Result Verification / Guardian**: Validates the proposed outcome against business constraints, duplicate ticket rules, and prompt safety.
  ↓
**Response Generation**: Generates the empathetic final text shown to the user, based on the structured outcome of the domain agent.
  ↓
**Observability**: LangSmith traces and custom telemetry.

## Domain Agents
1. **GeneralAgent**: FAQ, policies, general support questions.
2. **ProductAgent**: Product information, product issues, warranty-related requests, service/support guidance.
3. **OrderAgent**: Order tracking, cancellation, delivery issues, wrong/damaged/missing order issues.
4. **PaymentAgent**: Payment failures, deductions, billing, refunds, transaction issues.
5. **EscalationAgent / Guardian**: Unresolved issues, repeated attempts, human handoff, ticket routing, and duplicate ticket prevention.

## Preparation for Future Phases (Phase B Output)

### Guardian Ticket-History Capability
To enable the Guardian to accurately gate escalations, a new tool capability is required.
**Required Capability:** `find_customer_support_cases(customer_id, status, created_since, domain)`
This capability must answer:
- Active ticket count
- Tickets created in the last 24h/48h
- Matching domain/category
- Unresolved/open status
- Existing ticket IDs
*Current Gap*: `tools.py` only offers `get_ticket(ticket_id)` and `get_customer_history(customer_id)`. Neither provides filtering by domain or time windows cleanly without fetching all history.

### Warranty Verification Capability
To enable the WarrantySkill, the ProductAgent must be able to verify warranty status authoritatively.
**Required Authoritative Data:**
- Customer identity
- Specific order and product
- Purchase date
- Warranty period (from product catalog, parsed from unstructured description)
- Derived warranty status (Active/Expired)

*Current Gap*: The system lacks a dedicated capability to synthesize purchase date + warranty period. See [WARRANTY_WORKFLOW.md](WARRANTY_WORKFLOW.md) for full Phase E discovery details, ambiguity policy, and the required capability contract. Warranty status must NEVER be inferred from the user's conversation text alone.
