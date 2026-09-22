# Domain-Agent Redesign: Target Architecture

## 1. Target mental model

```text
                         Customer Message
                                |
                                v
                     +-----------------------+
                     | Semantic Intent Router|
                     | domain / intent /     |
                     | sentiment / urgency   |
                     +-----------+-----------+
                                 |
                                 v
                     +-----------------------+
                     | Supervisor / Workflow  |
                     | Orchestrator            |
                     | state + domain mapping |
                     +-----------+------------+
                                 |
          +----------------------+-----------------------+
          |          |            |           |           |
          v          v            v           v           v
      General     Product       Order      Payment    Guardian /
       Agent       Agent        Agent       Agent     Escalation
          |          |            |           |           |
          +----------+------------+-----------+-----------+
                                 |
                         Agent-specific Skill
                                 |
                                 v
                       Shared Capability Tools
          +-------------+-------------+-------------+-------------+
          | DB          | RAG         | Web Search  | Tickets     |
          | Customer    | KB          | official    | create/get/ |
          | Order       | retrieval   | external    | update      |
          | Payment     |             | sources     |             |
          +-------------+-------------+-------------+-------------+
                                 |
                                 v
                           Response / Action
                                 |
                                 v
                         State + Observability
```

## 2. Responsibility boundaries

### Router

Answers **what kind of problem is this?**

It may classify:

- domain;
- intent;
- sentiment;
- urgency;
- confidence / ambiguity.

It must not choose `db`, `rag`, `web`, `email`, or other implementation tools.

### Supervisor / Orchestrator

Answers **which domain workflow owns this conversation and what state is active?**

Responsibilities:

- map validated domain to the owning agent;
- preserve workflow state across turns;
- prevent unnecessary re-routing during a multi-turn workflow;
- enforce global safety/authorization boundaries;
- coordinate handoffs and completion.

### Domain Agent

Answers **who owns the customer's problem?**

The domain agent selects a skill based on the active problem and workflow state.

### Skill

Answers **how should this domain workflow be handled?**

Skills are scoped instruction artifacts. They are not tools and do not directly mutate the database. A skill may define which tools are allowed and what must be verified before using them.

### Tool

Answers **what capability can the system execute?**

Examples:

- database lookup/update;
- vector retrieval;
- ticket lookup/create/update;
- web search;
- email delivery.

## 3. Skill layout

Proposed layout:

```text
backend/app/
  agents/
    router.py
    supervisor.py
    general_agent.py
    product_agent.py
    order_agent.py
    payment_agent.py
    guardian_agent.py
  skills/
    base.py
    registry.py
    policy.py
    general/
      faq.md
    product/
      product_info.md
      warranty.md
    order/
      tracking.md
      cancellation.md
      delivery_issue.md
    payment/
      payment_failure.md
      refund.md
      billing.md
    guardian/
      escalation.md
      ticket_deduplication.md
  tools/
    database.py
    retrieval.py
    web.py
    tickets.py
    email.py
```

The exact filenames can change after inspecting the existing modules; the architectural boundary should not.

## 4. Skill contract

Every production skill should define:

```yaml
name:
purpose:
trigger_conditions:
required_context:
allowed_tools:
forbidden_tools:
verification_steps:
domain_rules:
expected_outcome:
handoff_conditions:
failure_behavior:
```

The runtime loader should validate this contract before a skill can be invoked.

## 5. Warranty vertical

### Input

> "I have an issue with the phone I purchased. It is under one-year warranty. I want to contact the team / fix it / refund it."

### Workflow

```text
User
  |
  v
Router: product / warranty_issue
  |
  v
Supervisor: Product workflow
  |
  v
Product Agent
  |
  v
Warranty Skill
  |
  +--> internal purchase/warranty verification tool
  |          |
  |          +--> active -> continue
  |          +--> inactive/unknown -> explain / request required info
  |
  +--> if supported by policy: identify manufacturer responsibility
  |
  +--> Web Search tool ONLY for official manufacturer support/service-center
  |
  v
Response with official route
  |
  +--> unresolved/repeated/severe -> Guardian
```

The warranty skill must not invent warranty coverage, claim that a warranty claim was filed, or fabricate a service-center link.

## 6. Escalation / Guardian flow

A human request is not an automatic `create_ticket` command.

```text
Escalation signal
       |
       v
Guardian skill
       |
       +--> active tickets?
       +--> related tickets in last 24-48h?
       +--> matching existing case?
       +--> previous resolution attempts?
       +--> severity / urgency?
       +--> supported action?
       |
       +-------------------+
       |                   |
       v                   v
   Existing case       No suitable case
       |                   |
       v                   v
 update/escalate       create case when
 existing ticket       policy requires
```

This prevents the user from learning that saying "manager" always creates a new escalation ticket.

## 7. State model

Transactional workflow state belongs in PostgreSQL. Vector storage remains retrieval/memory infrastructure.

Minimum state:

```text
conversation_id
customer_id
active_domain
active_intent
active_skill
workflow_status
entities
last_verified_facts
tool_call_history
pending_action
pending_approval
active_ticket_id
escalation_status
last_resolution_attempt
```

Do not store authoritative ticket/order/payment/warranty facts only in vector memory.

## 8. Production safety boundaries

1. Authenticate before customer-scoped tools.
2. Never trust LLM-supplied customer IDs when an authenticated identity is available.
3. Read operations should be preferred over mutations until required information is verified.
4. Refund/cancellation/ticket mutations remain behind explicit deterministic policy gates.
5. Web results must be treated as untrusted external information and never as authorization to perform internal actions.
6. Every tool call records tool name, sanitized arguments, result status, latency, and correlation/session ID.
7. Every skill invocation records skill version.
8. Never log secrets, API keys, raw authorization headers, or sensitive payment data.

## 9. Migration sequence

### Phase A — Freeze and inventory

- Freeze the current deterministic/evaluation baseline.
- Record current graph, agents, tools, skills, state fields, and test contracts.
- Identify all existing capabilities and their actual tool boundaries.
- Keep current system runnable throughout migration.

### Phase B — Runtime foundation

- Introduce typed workflow state.
- Introduce skill loader/validator and versioning.
- Separate global policy from domain skills.
- Add structured router output using domain vocabulary.
- Add deterministic domain-to-agent mapping.
- Add observability for routing, skill selection, tool calls, and handoffs.

### Phase C — Product/Warranty vertical

- Implement Product Agent.
- Implement Warranty skill.
- Implement/verify internal warranty/purchase lookup capability.
- Constrain Web Search to official manufacturer support/service-center discovery.
- Add warranty golden cases.
- Add multi-turn cases.
- Add negative cases where web search must not be used.
- Add regression evaluation and latency/tool-call metrics.

### Phase D — Guardian/Escalation

- Implement ticket-history retrieval.
- Implement active-ticket and recent-ticket checks.
- Add duplicate-ticket prevention/update-existing-case behavior.
- Add escalation policy skill.
- Add repeated-failure and human-request evaluation cases.

### Phase E — Remaining domains

Migrate one vertical at a time:

1. Order
2. Payment
3. General/FAQ
4. Product beyond warranty

Each migration must have its own skill set and golden cases before integration into the production graph.

### Phase F — Cutover

- Shadow-run the new architecture against the existing architecture.
- Compare semantic routing, tool selection, tool arguments, outcomes, response compliance, latency, and escalation rate.
- Canary the new architecture.
- Roll back to the last stable version if domain-specific regression gates fail.
- Remove old capability-as-agent nodes only after all domains pass their migration gates.

## 10. Evaluation gates

Maintain the existing Golden Dataset, but extend it by domain and workflow.

Required suites:

- routing/domain classification;
- skill selection;
- tool selection;
- tool argument correctness;
- authorization/security;
- warranty;
- order;
- payment/refund;
- escalation/ticket deduplication;
- multi-turn state;
- web-search boundary;
- hallucination/unsupported-claim prevention;
- transport failure and retry behavior.

No migration should be declared successful from aggregate pass rate alone. A domain is promoted only when its critical workflow cases, safety gates, and system-failure observability all pass.
