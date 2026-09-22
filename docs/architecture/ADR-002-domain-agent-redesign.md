# ADR 002: Domain Agent Redesign

## Status
Proposed / Discovery Complete

## Context
The current Customer Support Agent relies on technology-bound agents (`db_agent`, `rag_agent`, `web_agent`). This architecture forces a single agent (`db_agent`) to understand complex, disjointed business rules across multiple domains (Orders, Payments, Ticketing). It also couples the semantic understanding of the user's request directly to the implementation details of the response.

## Decision
We will migrate to a Domain-Oriented Multi-Agent Architecture. 
1. Agents will be defined by their business domain (`General`, `Product`, `Order`, `Payment`, `Escalation`).
2. Technical capabilities (DB, Web Search, RAG) will be abstracted as **Shared Tools**.
3. Business logic will be codified into versionable **Agent Skills** (SOPs).
4. A **Semantic Router** will determine the domain and intent without dictating the execution path.
5. A **Guardian** module will intercept responses and escalation requests to enforce policies (like preventing duplicate tickets).

## Consequences

**Positive:**
- Separation of concerns: Business rules live in Domain Skills, not embedded in monolithic prompts.
- Testability: We can evaluate the Warranty Skill independently of the Refund Skill.
- Scalability: New domains (e.g., `SubscriptionAgent`) can be added without bloating the existing `db_agent`.

**Negative:**
- Increased architectural complexity.
- Requires state management (Supervisor) to coordinate between Domain Agents and the Guardian.
- Migration will require running two parallel graph implementations temporarily.
