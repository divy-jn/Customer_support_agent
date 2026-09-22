# ADR-001: Domain-Oriented Multi-Agent Architecture

- **Status:** Proposed
- **Date:** 2026-09-22
- **Scope:** Customer Support Agent redesign

## Context

The current application treats implementation capabilities such as database access, RAG, web search, and escalation handling as separate routing destinations. The Phase 2 experiments showed that this boundary is brittle: semantic intent classification, implementation routing, tool selection, and business policy are coupled.

The redesign separates **business-domain ownership** from **capabilities**.

## Decision

Customer-facing agents represent customer problem domains. Shared capabilities remain tools.

### Domain agents

1. **General Agent** — FAQs, policies, general support questions.
2. **Product Agent** — product information, product issues, warranty workflows, service-center guidance.
3. **Order Agent** — order status, delivery, cancellation, order-related problems.
4. **Payment Agent** — payment failures, billing, refunds, payment disputes.
5. **Guardian / Escalation Agent** — safety, complaint severity, unresolved/repeated support issues, and human-handoff decisions.

These are business owners of the conversation, not wrappers around a single tool.

### Shared tools

Database access, RAG/vector retrieval, web search, ticket operations, email, and similar capabilities are tools. Multiple domain agents may use the same tool subject to skill/tool permissions.

### Router contract

The router classifies the customer's problem semantically. It does **not** select implementation tools.

The preferred output is:

```json
{
  "domain": "order",
  "intent": "tracking",
  "sentiment": "neutral",
  "urgency": "normal"
}
```

The Supervisor/Orchestrator maps the validated domain to the owning agent and maintains workflow state.

### Skills

Agents are refined through versioned, scoped skills rather than large hardcoded system prompts. A skill is an instruction artifact (initially Markdown-backed) describing:

- when the skill applies;
- required inputs and context;
- business rules and decision procedure;
- allowed tools and tool argument constraints;
- prohibited actions;
- expected output/hand-off contract;
- failure and escalation behavior;
- verification requirements.

The global safety policy remains separate and applies to every agent/skill.

### State and memory

PostgreSQL is the authoritative store for transactional/workflow state. The vector store is used for semantic retrieval and conversational/document memory where appropriate; it is not the source of truth for workflow state.

The workflow state must be able to represent at least:

- conversation/session ID;
- customer ID;
- active domain and intent;
- active workflow/skill;
- extracted entities (order/product/ticket/etc.);
- tool results and provenance;
- pending user action/approval;
- escalation state;
- resolution status.

### External web usage

Web search is not a general fallback. It is invoked only when the selected skill explicitly requires external information that the internal system does not own.

Example: warranty support may verify warranty/purchase state internally, then use web search only to locate the manufacturer's official support/service-center resources.

## Warranty reference workflow

Example request:

> "I have an issue with the phone I purchased and it is under one-year warranty. I want to contact the team/fix/refund it."

Expected flow:

1. Router classifies the problem as `product` / warranty-related.
2. Product Agent selects the Warranty skill.
3. Warranty skill uses the appropriate internal tool(s) to verify purchase/warranty status.
4. If warranty is active, the agent explains that warranty claims are handled by the manufacturer/brand when that is the supported business policy.
5. Only then does the skill invoke web search for official brand support/service-center information when needed.
6. The agent provides the official service-center/support route; it does not invent warranty terms or claim that the company processed a warranty action it cannot actually perform.
7. If the issue remains unresolved or meets escalation policy, Guardian handles the human-support workflow.

## Escalation policy direction

A request for a human is a signal, not an unconditional escalation command. Guardian should consider:

- active ticket count;
- related tickets created in the previous 24–48 hours;
- whether a related ticket already exists;
- severity/sentiment/urgency;
- whether the domain agent has already attempted resolution;
- whether the requested action is supported;
- whether the issue is unresolved or repeatedly reported.

When an existing related ticket is available, prefer updating/escalating that case over creating a duplicate ticket, subject to the tool/policy contract.

## Consequences

### Positive

- Domain agents are understandable from a customer-support perspective.
- Tools can be reused across domains without pretending to be agents.
- Skills become independently versionable and testable.
- Router semantics are decoupled from implementation details.
- Multi-turn workflows have an explicit state model.
- Web search is constrained to justified use cases.
- Escalation can be policy-driven rather than keyword-driven.

### Costs / risks

- More explicit workflow state is required.
- Domain agents need clear ownership boundaries to prevent overlap.
- Skill files require versioning and regression tests.
- A migration period is needed while the existing graph is replaced incrementally.
- Additional orchestration logic can increase complexity if every workflow is made agentic unnecessarily.

## Migration rule

Do not rewrite the entire system in one change. Migrate one vertical workflow end-to-end, prove the contracts and evaluation gates, then reuse the pattern for the remaining domains.

The first vertical is **Product → Warranty** because it exercises domain routing, skills, DB verification, constrained web search, response policy, and potential escalation without requiring the system to process warranty claims itself.
